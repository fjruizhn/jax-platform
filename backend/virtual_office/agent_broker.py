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
from http_client import get_http_client
from virtual_office.engine_resolver import (OfficeEngineUnavailable, ResolvedOfficeEngine,
                                            load_pinned_office_credential_in_cursor)

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
    """Load the credential pinned into the accepted engine snapshot only."""
    if engine.provider_id != "zhipu" or engine.transport != "http_openai_compat":
        raise OfficeBrokerUnavailable("office provider is not allowlisted")
    await cur.execute(
        "SELECT base_url FROM provider WHERE id=%s AND status='active' AND auth_type='api_key' FOR UPDATE",
        (engine.provider_id,),
    )
    provider = await cur.fetchone()
    if provider is None:
        raise OfficeBrokerUnavailable("office credential is unavailable")
    base_url = provider[0] if isinstance(provider, (tuple, list)) else provider.get("base_url")
    url = _allowed_zai_url(base_url)
    try:
        encrypted_value = await load_pinned_office_credential_in_cursor(cur, engine=engine, for_update=True)
    except OfficeEngineUnavailable as error:
        raise OfficeBrokerUnavailable("office credential is unavailable") from error
    api_key = decrypt_db_secret(encrypted_value)
    if not api_key or len(api_key) > 4096:
        raise OfficeBrokerUnavailable("office credential is unavailable")
    return OfficeDispatchTarget(engine=engine, api_key=api_key, url=url)


async def dispatch(target: OfficeDispatchTarget, *, instructions: str, user_input: str,
                   max_tokens_field: str, max_output_tokens: int,
                   max_response_bytes: int, timeout_seconds: int) -> str:
    """Call the approved GLM endpoint once, without redirects or tools.

    Any timeout, protocol failure, non-2xx response or malformed answer is an
    ambiguous outcome to the durable caller.  There is intentionally no
    provider/model fallback and no automatic retry.
    """
    if not instructions or not user_input:
        raise OfficeBrokerUnavailable("office request is malformed")
    if max_tokens_field not in {"max_tokens", "max_completion_tokens"}:
        raise OfficeBrokerUnavailable("office model limit is unavailable")
    if (not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool)
            or max_output_tokens < 1):
        raise OfficeBrokerUnavailable("office model limit is unavailable")
    if (not isinstance(max_response_bytes, int) or isinstance(max_response_bytes, bool)
            or max_response_bytes < 1):
        raise OfficeBrokerUnavailable("office response limit is unavailable")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1:
        raise OfficeBrokerUnavailable("office timeout is unavailable")
    payload = {"model": target.engine.model_id, "messages": [
        {"role": "system", "content": instructions}, {"role": "user", "content": user_input},
    ], "tools": [], max_tokens_field: max_output_tokens}
    try:
        # The application lifespan creates and closes this shared client.  Keeping
        # the pool here avoids a TCP/TLS handshake for every Office message while
        # the per-request timeout remains deliberately bounded below.
        timeout = httpx.Timeout(_MAX_SECONDS, connect=min(_MAX_SECONDS, 10.0))
        async with asyncio.timeout(timeout_seconds):
            client = await get_http_client()
            async with client.stream(
                "POST", target.url,
                headers={"Authorization": f"Bearer {target.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            ) as response:
                if response.is_redirect or response.status_code < 200 or response.status_code >= 300:
                    raise OfficeBrokerUnavailable("office provider response is unavailable")
                content_length = response.headers.get("content-length")
                if content_length is not None and (not content_length.isdecimal() or int(content_length) > max_response_bytes):
                    raise OfficeBrokerUnavailable("office provider response is unavailable")
                encoded = bytearray()
                async for chunk in response.aiter_bytes():
                    encoded.extend(chunk)
                    if len(encoded) > max_response_bytes:
                        raise OfficeBrokerUnavailable("office provider response is unavailable")
        decoded = json.loads(encoded.decode("utf-8"))
        content = decoded["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content:
            raise OfficeBrokerUnavailable("office provider response is malformed")
        return content
    except (httpx.HTTPError, TimeoutError, asyncio.TimeoutError, KeyError, IndexError, TypeError, ValueError,
            UnicodeDecodeError) as error:
        raise OfficeBrokerUnavailable("office dispatch outcome is unknown") from error
