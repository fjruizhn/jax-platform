"""One narrowly-scoped external transport for Office agents.

The browser supplies only user text and an idempotency key.  URL, credential
and model come from the current, attested Office binding and are checked again
inside the server before egress.
"""
from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import os
from urllib.parse import urlsplit

import httpx

from crypto_secrets import decrypt_db_secret
from virtual_office.engine_resolver import OfficeEngineUnavailable, ResolvedOfficeEngine

_MAX_SECONDS = 45.0


class OfficeBrokerUnavailable(RuntimeError):
    """No safe request can be made, or its outcome is ambiguous."""


@dataclass(frozen=True)
class OfficeDispatchTarget:
    engine: ResolvedOfficeEngine
    api_key: str
    url: str


def _normalise_allowlisted_base(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise OfficeBrokerUnavailable("office provider url is not allowlisted")
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.port is not None
            or not parts.path.rstrip("/") or parts.query or parts.fragment
            or parts.username is not None or parts.password is not None):
        raise OfficeBrokerUnavailable("office provider url is not allowlisted")
    return f"https://{parts.hostname}{parts.path.rstrip('/')}"


def _allowed_zai_url(value: object) -> str:
    configured = os.environ.get("OFFICE_BROKER_ALLOWED_BASE_URLS", "")
    bases = [part.strip() for part in configured.split(",") if part.strip()]
    if not bases:
        raise OfficeBrokerUnavailable("office provider url is not allowlisted")
    allowed = {_normalise_allowlisted_base(base) for base in bases}
    candidate = _normalise_allowlisted_base(value)
    if candidate not in allowed:
        raise OfficeBrokerUnavailable("office provider url is not allowlisted")
    return candidate + "/chat/completions"


async def load_dispatch_target_in_cursor(cur, *, engine: ResolvedOfficeEngine) -> OfficeDispatchTarget:
    """Read exactly one current credential after engine attestation.

    A duplicate active credential is deliberately unavailable: choosing one by
    incidental ordering would make a retry charge a different account.
    """
    if engine.provider_id != "zhipu" or engine.transport != "http_openai_compat":
        raise OfficeBrokerUnavailable("office provider is not allowlisted")
    await cur.execute(
        "SELECT p.base_url,c.encrypted_value FROM provider p JOIN credential c ON c.provider_id=p.id "
        "WHERE p.id=%s AND p.status='active' AND p.auth_type='api_key' AND c.state='active' FOR UPDATE",
        (engine.provider_id,),
    )
    rows = await cur.fetchall()
    if len(rows) != 1:
        raise OfficeBrokerUnavailable("office credential is unavailable")
    url = _allowed_zai_url(rows[0][0])
    api_key = decrypt_db_secret(rows[0][1])
    if not api_key or len(api_key) > 4096:
        raise OfficeBrokerUnavailable("office credential is unavailable")
    return OfficeDispatchTarget(engine=engine, api_key=api_key, url=url)


async def dispatch(target: OfficeDispatchTarget, *, instructions: str, user_input: str) -> str:
    """Call the approved GLM endpoint once, without redirects or tools.

    Any timeout, protocol failure, non-2xx response or malformed answer is an
    ambiguous outcome to the durable caller.  There is intentionally no
    provider/model fallback and no automatic retry.
    """
    if not instructions or not user_input:
        raise OfficeBrokerUnavailable("office request is malformed")
    payload = {"model": target.engine.model_id, "messages": [
        {"role": "system", "content": instructions}, {"role": "user", "content": user_input},
    ], "tools": []}
    try:
        timeout = httpx.Timeout(_MAX_SECONDS, connect=min(_MAX_SECONDS, 10.0))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(target.url, headers={"Authorization": f"Bearer {target.api_key}"},
                                         content=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if response.is_redirect or response.status_code < 200 or response.status_code >= 300:
            raise OfficeBrokerUnavailable("office provider response is unavailable")
        decoded = response.json()
        content = decoded["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content:
            raise OfficeBrokerUnavailable("office provider response is malformed")
        return content
    except (httpx.HTTPError, asyncio.TimeoutError, KeyError, IndexError, TypeError, ValueError) as error:
        raise OfficeBrokerUnavailable("office dispatch outcome is unknown") from error
