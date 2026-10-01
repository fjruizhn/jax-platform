"""Exact-pair MariaDB proof for the F2-C rejected-narrative F2-D fallback.

This runs without the broad application test fixture: it applies only the
F2-D outbox schema in a disposable CI database, then proves that the exact
JAX checkout mints an effective UNAVAILABLE fallback which is durably prepared
and committed as the bytes sent by the ASGI adapter.
"""
from __future__ import annotations

import asyncio
import json

from api.chat import ChatResponse, _parse_contract_response
from api.governed_chat import project_provider_contract
from auth.models import AuthUser
from db.connection import get_pool
from db.output_lifecycle_migration import apply as apply_outbox_schema
from jax.memory.b9 import ScopeContext
from webchat_f2d.transport import prepare_governed_chat_response


async def _apply_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await apply_outbox_schema(cur)
        await conn.commit()


async def _row(outbox_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT state, contract_state, effective_output_digest, response_payload "
                "FROM governed_output_outbox WHERE outbox_id=%s", (outbox_id,)
            )
            return await cur.fetchone()


async def _run() -> None:
    await _apply_schema()
    scope = ScopeContext(
        actor_principal="user:7", actor_type="USER", subject_user_id="7",
        tenant_id="1", project_id=None, calling_component="jax-platform-web-chat",
    )
    rejected = "Hall9000 is healthy."
    governed = project_provider_contract(
        _parse_contract_response(
            '{"claim": [], "analysis": "Hall9000 is healthy.", "judgment": null}'),
        memory_scope=scope, user_id="7", request_id="exact-pair-durable-fallback",
    )
    assert governed.transport_unit is not None
    assert governed.contract_state == "UNAVAILABLE"
    assert rejected not in governed.text
    response = ChatResponse(
        facet="jekyll", response=governed.text, timestamp="2026-10-01T00:00:00Z",
        contract_degraded=governed.contract_degraded,
        response_id=governed.response_id, envelope_digest=governed.envelope_digest,
        source_envelope_digest=governed.source_envelope_digest,
        contract_state=governed.contract_state, governed_plain=True,
    )
    history: list[str] = []

    async def record_history() -> None:
        history.append(governed.text)

    prepared = await prepare_governed_chat_response(
        response=response, transport_unit=governed.transport_unit,
        user=AuthUser(user_id="7", tenant_id="1", role="operator"), memory_scope=scope,
        trusted_metadata={"facet": response.facet, "timestamp": response.timestamp,
                          "contract_degraded": response.contract_degraded},
        on_commit=record_history,
    )
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await prepared({"type": "http", "method": "POST", "path": "/api/chat"}, receive, send)
    body = next(message["body"] for message in messages if message["type"] == "http.response.body")
    state, contract_state, effective_digest, stored = await _row(prepared.authorization.outbox_id)
    assert state == "OUTPUT_COMMITTED_TO_TRANSPORT"
    assert contract_state == "UNAVAILABLE"
    assert effective_digest == governed.envelope_digest
    assert bytes(stored) == body
    assert json.loads(body)["response"] == governed.text
    assert rejected.encode("utf-8") not in body
    assert history == [governed.text]


if __name__ == "__main__":
    asyncio.run(_run())
