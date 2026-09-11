"""kimi en la Mesa web: el chat despacha por `facet.transport`, y kimi tenia
'motor_registry', un transporte que _invoke_facet_dispatch no despacha.

Resultado medido antes de este cambio (2026-09-11): kimi reportaba
`unsupported_transport` en cada barrido de la sonda desde 2026-08-20, 24
eventos por dia, sin un solo turno de chat servido desde 2026-08-18. La API
de Moonshot estaba sana: una llamada real con el payload exacto de la Mesa
web (kimi-k3, max_tokens=131072) dio HTTP 200, finish_reason=stop.

Por que 'motor_registry' era una etiqueta sin lector: Jacobs despacha a kimi
por _MOTOR_FACETS ANTES de mirar facet.transport (jacobs/executor.py, repo
jax), y el worker de Motor Registry despacha por `motor.transport`, que para
kimi ya es 'http_openai_compat'. El unico lector de facet.transport='kimi'
era el chat, y ahi no significaba nada. El arreglo alinea a kimi con ada,
que tiene fila en `motor` para pipelines y sale por http_openai_compat en el
chat, detras del gate authorize-facet.
"""
import asyncio

import pytest

import api.chat as chat_mod
import http_client
from db.connection import get_pool
from db.migrations import _FACET_SEED, _migrate_kimi_chat_transport
from facet_health import OUTCOME_OK
from facet_resolver import ResolvedFacet
from jax_engine.facet_canary import _NOT_DISPATCHED
from tests.test_chat_contract_wrapper import _FakeResponse
from tests.test_chat_facet_validation import _UrlRecordingClient


_SEEDED_CHAT_FACETS = [
    (key, transport)
    for key, _name, _icon, _color, transport, _auto in _FACET_SEED
    if key not in _NOT_DISPATCHED
]


@pytest.mark.parametrize("key,transport", _SEEDED_CHAT_FACETS)
def test_todo_facet_sembrado_es_despachable_por_el_chat(monkeypatch, key, transport):
    """Cierra la CLASE, no la instancia: ningun facet sembrado puede nacer
    con un transporte que el chat no sabe despachar, salvo los que la sonda
    excluye a proposito (_NOT_DISPATCHED, hoy hyde).

    Es conductual, no una lista de transportes copiada: corre el dispatch
    real con el facet resuelto a ese transporte y exige OUTCOME_OK. Un
    transporte nuevo que alguien siembre sin rama de dispatch cae en
    `unsupported_transport` y este test se pone rojo, sin que nadie tenga
    que acordarse de actualizar una segunda lista."""
    resolved = ResolvedFacet(
        key=key,
        provider_id="provider-de-test",
        base_url="https://ejemplo.invalid/v1",
        model="modelo-de-test",
        credential="cred-falsa",
        transport=transport,
        persona=None,
        params=None,
        max_tokens_param="max_tokens",
        max_output_tokens=131072,
    )

    async def _fake_resolve(_key):
        return resolved

    async def _fake_provider_call(*_args, **_kwargs):
        return "listo", 1, 1

    monkeypatch.setattr(chat_mod, "resolve_facet", _fake_resolve)
    for name in ("_call_ollama", "_call_gemini", "_call_openai_compat"):
        monkeypatch.setattr(chat_mod, name, _fake_provider_call)

    # El gate autoriza: lo que se prueba aca es que exista rama de dispatch,
    # no la gobernanza (esa la cubre test_chat_facet_validation.py).
    fake = _UrlRecordingClient(_FakeResponse({"allowed": True}))
    original = http_client._client
    http_client._client = fake
    try:
        texto, _usage, outcome = asyncio.run(chat_mod._invoke_facet_dispatch(
            key,
            {"personalities": {"jax_local": {"system_prompt": "x"}, key: {"system_prompt": "x"}}},
            f"test-dispatchable-{key}",
            "hola",
        ))
    finally:
        http_client._client = original

    assert outcome == OUTCOME_OK, (key, transport, texto)


async def _kimi_transport(cur) -> str:
    await cur.execute("SELECT transport FROM facet WHERE `key`='kimi'")
    (transport,) = await cur.fetchone()
    return transport


@pytest.mark.asyncio
async def test_migracion_pasa_kimi_de_motor_registry_a_http_openai_compat():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            original = await _kimi_transport(cur)
            try:
                await cur.execute("UPDATE facet SET transport='motor_registry' WHERE `key`='kimi'")
                await _migrate_kimi_chat_transport(cur)
                assert await _kimi_transport(cur) == "http_openai_compat"
                # Idempotente: una segunda corrida no cambia nada.
                await _migrate_kimi_chat_transport(cur)
                assert await _kimi_transport(cur) == "http_openai_compat"
            finally:
                await cur.execute("UPDATE facet SET transport=%s WHERE `key`='kimi'", (original,))


@pytest.mark.asyncio
async def test_migracion_no_pisa_un_transporte_distinto_de_motor_registry():
    """Guard: la migracion corrige SOLO el valor viejo conocido. Si alguien
    ya movio a kimi a otro transporte a proposito, no se lo revierte."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            original = await _kimi_transport(cur)
            try:
                await cur.execute("UPDATE facet SET transport='ollama' WHERE `key`='kimi'")
                await _migrate_kimi_chat_transport(cur)
                assert await _kimi_transport(cur) == "ollama"
            finally:
                await cur.execute("UPDATE facet SET transport=%s WHERE `key`='kimi'", (original,))
