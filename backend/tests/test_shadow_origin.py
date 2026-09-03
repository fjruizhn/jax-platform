"""
Columna de ORIGEN declarada por quien llama (2026-09-03).

Por qué: se midió el tráfico de la Mesa para decidir si alcanza para una
muestra de análisis, y una fila de sonda y una de uso real resultaron
INDISTINGUIBLES en la base -- mismo source (axioma-web), mismo user_id
(1), mismo project_id (NULL), mismo request_type (chat). Lo único que
permitía atribuir las sondas de hoy era saber a qué hora se corrieron, un
hecho de la sesión, no de la base. Esta columna cierra eso.

El grano es el TURNO, no la conversación (ver comentario en
db/migrations.py y en shadow_messages.origin). El default 'unattributed'
es deliberado: la ausencia de declaración NO es evidencia de uso
orgánico -- lo contrario sería fail-open (una sonda que se olvida de
marcarse contaminaría la muestra haciéndose pasar por tráfico real).

Corre contra jax_memory_test (fixture `client`, ver conftest.py). Con
JAX_CI_NO_DB=1 se saltea por la Regla 1 de conftest.py (pide `client`).
"""
from __future__ import annotations

import inspect
import uuid

from unittest.mock import patch

from tests.test_chat_contract_wrapper import _FakeResponse


class _RecordingPostClient:
    def __init__(self):
        self.payloads = []

    async def post(self, url, **kwargs):
        if "/motor/authorize-facet" in url:
            return _FakeResponse({"allowed": True, "reason": "OK"})
        self.payloads.append(kwargs.get("json"))
        return _FakeResponse({
            "choices": [{"message": {"content": '{"claim": [], "analysis": "ok", "judgment": null}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })


async def _fetch_origin(shadow_message_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT origin FROM shadow_messages WHERE shadow_message_id = %s",
                (shadow_message_id,),
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def _count_shadow_messages():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM shadow_messages")
            row = await cur.fetchone()
            return row[0]


def _post_chat(client, body, user_suffix):
    # Sin mockear add_safe_task del todo: se lo envuelve para CAPTURAR los
    # argumentos con los que se encoló run_shadow_validation (mismo patrón
    # que test_chat_grounding_wiring.py). NO se deja correr la llamada real:
    # user_id no numérico a propósito (igual que
    # test_chat_endpoint_does_not_break_when_shadow_validation_is_enqueued
    # en test_shadow_validation.py) porque conv_uuid depende de
    # jax.memory.db.start_conversation(), que vive en el esquema de
    # `conversations`/`messages` del repo `jax` -- fuera del alcance de
    # esta migración y de db/migrations.py de este repo. Este helper prueba
    # el CÁLCULO de origin dentro de chat() (spec ítem 2), no la
    # persistencia -- eso lo prueban los tests de más abajo que llaman
    # run_shadow_validation() directo, igual que
    # test_shadow_validation_navigable_without_messages_row (fila sin FK a
    # `messages`).
    import http_client
    from auth.jwt import create_access_token
    token = create_access_token(
        f"test-origin-user-{user_suffix}", f"test-origin-tenant-{user_suffix}", "operator")
    fake = _RecordingPostClient()
    captured = {}

    def spy(background_tasks, fn, *args, **kwargs):
        captured["args"] = args

    original = http_client._client
    http_client._client = fake
    try:
        with patch("jax_engine.background.add_safe_task", side_effect=spy):
            resp = client.post("/api/chat", json=body,
                                headers={"Authorization": f"Bearer {token}"})
    finally:
        http_client._client = original
    return resp, captured


def test_turn_without_origin_computes_unattributed_for_the_validator(client):
    # NO 'web' -- ausencia de declaración != declaración de web. Ver
    # docstring del módulo (fail-open si el default fuera otra cosa).
    resp, captured = _post_chat(client, {"message": "hola", "facet": "jekyll"}, "1")
    assert resp.status_code == 200, resp.text
    assert captured["args"][5] == "unattributed"


def test_turn_with_origin_probe_computes_probe_for_the_validator(client):
    resp, captured = _post_chat(
        client, {"message": "hola", "facet": "jekyll", "origin": "probe"}, "2")
    assert resp.status_code == 200, resp.text
    assert captured["args"][5] == "probe"


def test_run_shadow_validation_persists_the_origin_it_received(client):
    # La otra mitad del camino: lo que chat() calculó (test de arriba) es
    # lo que run_shadow_validation() efectivamente escribe. conv_uuid
    # ficticio a propósito -- shadow_messages no tiene FK a `conversations`
    # (mismo argumento que test_shadow_validation_navigable_without_messages_row).
    from governance_context import validation_context
    import grounding as governance_grounding
    from shadow_validation import run_shadow_validation
    from api.chat import ContractResult

    ctx, _, _ = validation_context()
    snapshot = governance_grounding.build_snapshot(ctx)
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis=None, judgment=None,
        degradation_reason=None, raw_text="...",
    )

    smid_unattributed = str(uuid.uuid4())
    client.portal.call(
        run_shadow_validation, "conv-origin-unattributed", smid_unattributed,
        "jekyll", contract, snapshot, "unattributed")
    assert client.portal.call(_fetch_origin, smid_unattributed) == "unattributed"

    smid_probe = str(uuid.uuid4())
    client.portal.call(
        run_shadow_validation, "conv-origin-probe", smid_probe,
        "jekyll", contract, snapshot, "probe")
    assert client.portal.call(_fetch_origin, smid_probe) == "probe"


def test_origin_outside_closed_vocabulary_is_422_and_writes_no_row(client):
    from auth.jwt import create_access_token
    before = client.portal.call(_count_shadow_messages)
    token = create_access_token("test-origin-user-3", "test-origin-tenant-3", "operator")
    resp = client.post(
        "/api/chat", json={"message": "hola", "facet": "jekyll", "origin": "bogus"},
        headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422
    after = client.portal.call(_count_shadow_messages)
    assert after == before


def test_sixth_argument_is_mandatory(client):
    from shadow_validation import run_shadow_validation
    p = inspect.signature(run_shadow_validation).parameters["origin"]
    assert p.default is inspect.Parameter.empty


def test_run_shadow_validation_clamps_origin_to_20_chars(client):
    # Defensa en profundidad, mismo criterio que facet[:30] en
    # _insert_shadow_message: el vocabulario cerrado de ChatRequest.origin
    # ya lo garantiza en el borde, pero este módulo es invocable por
    # cualquier otro caller -- clampear acá asegura que el INSERT nunca
    # falle con "Data too long" sin importar quién llame.
    from governance_context import validation_context
    import grounding as governance_grounding
    from shadow_validation import run_shadow_validation
    from api.chat import ContractResult

    ctx, _, _ = validation_context()
    snapshot = governance_grounding.build_snapshot(ctx)
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis=None, judgment=None,
        degradation_reason=None, raw_text="...",
    )
    smid = str(uuid.uuid4())
    long_origin = "x" * 50
    client.portal.call(
        run_shadow_validation, "conv-origin-clamp", smid, "jekyll", contract, snapshot, long_origin)
    assert client.portal.call(_fetch_origin, smid) == "x" * 20


def test_migration_adds_origin_column_with_type_and_default(client):
    async def _col():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'shadow_messages' "
                    "AND COLUMN_NAME = 'origin'"
                )
                return await cur.fetchone()

    col_type, nullable, default = client.portal.call(_col)
    assert col_type == "varchar(20)"
    assert nullable == "NO"
    # MariaDB devuelve COLUMN_DEFAULT de un VARCHAR con comillas literales
    # incluidas (p.ej. "'unattributed'"), no el valor pelado.
    assert default == "'unattributed'"
