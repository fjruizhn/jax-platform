"""BackgroundTask de shadow validation (REFORMAS-v3 Fase 2 Sub-proyecto 2).
Corre contra jax_memory_test. La validación de claims/vocab importa
policy/governance/ directo desde ~/jax (sys.path) — sin red, sin mocks
de HTTP, es una llamada Python normal.

NOTA DE ALCANCE (Task 5, ver task-5-report.md): dos ajustes sobre el
texto del brief original, verificados contra el código real de ~/jax
(master) antes de escribir:

1. `test_shadow_validation_claim_produces_authority_invalid_verdict`: el
   brief declaraba `args={"name": "code_swarm"}` para un claim
   CAPABILITY_AVAILABLE. `predicates.yaml` (sin cambios desde Fase 0.5,
   verificado con `git log`) exige args={name, mode} — con solo `name`,
   `validate()` corta en ARGS_MISMATCH *antes* de llegar al chequeo de
   authority (ver el orden real en policy/governance/validator.py:
   UNKNOWN_PREDICATE → ARGS_MISMATCH → AUTHORITY_INVALID → resolver). Se
   agrega `mode` al args para que el claim sí llegue al chequeo de
   authority y produzca AUTHORITY_INVALID como es la intención real del
   test.

2. `test_shadow_validation_sweeps_analysis_and_judgment_for_vocab_hits`:
   el brief usaba el término "trae a hyde" para el canal judgment. La
   categoría `commands` de `policy/vocabulary/closed_vocabulary.yaml`
   está vacía (`[]`) — comentario propio del archivo: "no se encontró un
   registro de comandos" — así que ese término no existe en el
   vocabulario real y ningún sweep correcto lo encontraría. Se sustituye
   por "hyde", término real presente en `facets_las_manos`/`facets_jax`.
"""
import json
import uuid

from api.chat import ContractResult


async def _fetch_shadow_message(shadow_message_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT conv_uuid, facet, contract_parsed, degradation_reason, "
                "has_claim, has_analysis, has_judgment, validated_at "
                "FROM shadow_messages WHERE shadow_message_id = %s",
                (shadow_message_id,),
            )
            return await cur.fetchone()


async def _fetch_claim_verdicts(shadow_message_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT predicate, status, args FROM shadow_claim_verdicts "
                "WHERE shadow_message_id = %s",
                (shadow_message_id,),
            )
            return await cur.fetchall()


async def _fetch_vocab_hits(shadow_message_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT channel, term, category FROM shadow_vocab_hits "
                "WHERE shadow_message_id = %s",
                (shadow_message_id,),
            )
            return await cur.fetchall()


def test_shadow_validation_writes_message_row_and_sets_validated_at(client):
    from shadow_validation import run_shadow_validation
    smid = str(uuid.uuid4())
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis="sin nada verificable",
        judgment=None, degradation_reason=None, raw_text="...",
    )
    client.portal.call(run_shadow_validation, "conv-fake-uuid", smid, "jekyll", contract)

    row = client.portal.call(_fetch_shadow_message, smid)
    assert row is not None
    conv_uuid, facet, contract_parsed, degradation_reason, has_claim, has_analysis, has_judgment, validated_at = row
    assert conv_uuid == "conv-fake-uuid"
    assert facet == "jekyll"
    assert bool(contract_parsed) is True
    assert bool(has_claim) is False
    assert bool(has_analysis) is True
    assert bool(has_judgment) is False
    assert validated_at is not None  # el worker no murió, se completó


def test_shadow_validation_claim_produces_authority_invalid_verdict(client):
    from shadow_validation import run_shadow_validation
    smid = str(uuid.uuid4())
    contract = ContractResult(
        contract_parsed=True,
        claims=[{
            "predicate": "CAPABILITY_AVAILABLE",
            "args": {"name": "code_swarm", "mode": "read_only"},
        }],
        analysis="revisé el catálogo", judgment=None,
        degradation_reason=None, raw_text="...",
    )
    client.portal.call(run_shadow_validation, "conv-fake-uuid-2", smid, "jekyll", contract)

    verdicts = client.portal.call(_fetch_claim_verdicts, smid)
    assert len(verdicts) == 1
    predicate, status, args = verdicts[0]
    assert predicate == "CAPABILITY_AVAILABLE"
    # Resultado esperado de esta ronda (spec, sección "Alcance"): authority
    # siempre INFERIDO, prohibido en canal claim — NO es un bug.
    assert status == "AUTHORITY_INVALID"
    # aiomysql no decodifica JSON automáticamente: la columna vuelve como
    # el texto crudo que insertó json.dumps() en shadow_validation.py.
    assert json.loads(args) == {"name": "code_swarm", "mode": "read_only"}


def test_shadow_validation_sweeps_analysis_and_judgment_for_vocab_hits(client):
    from shadow_validation import run_shadow_validation
    smid = str(uuid.uuid4())
    contract = ContractResult(
        contract_parsed=True, claims=[],
        analysis="mencioné code_swarm en el análisis",
        judgment="y también invocamos a hyde en el judgment",
        degradation_reason=None, raw_text="...",
    )
    client.portal.call(run_shadow_validation, "conv-fake-uuid-3", smid, "jax_local", contract)

    hits = client.portal.call(_fetch_vocab_hits, smid)
    channels_terms = {(h[0], h[1]) for h in hits}
    assert ("analysis", "code_swarm") in channels_terms
    assert ("judgment", "hyde") in channels_terms


def test_shadow_validation_navigable_without_messages_row(client):
    # El punto del hallazgo de conv_uuid/shadow_message_id: una fila de
    # shadow es navegable a su conversación aunque `messages` (la tabla
    # real de mensajes, escrita fire-and-forget por _memory.save_message())
    # todavía no tenga el mensaje guardado — no hay FK a `messages`.
    from shadow_validation import run_shadow_validation
    smid = str(uuid.uuid4())
    fake_conv_uuid = str(uuid.uuid4())  # UUID que casi seguro no existe en `conversations`
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis="x", judgment=None,
        degradation_reason=None, raw_text="x",
    )
    client.portal.call(run_shadow_validation, fake_conv_uuid, smid, "jekyll", contract)
    row = client.portal.call(_fetch_shadow_message, smid)
    assert row is not None
    assert row[0] == fake_conv_uuid  # navegable por conv_uuid, sin depender de `messages`


def test_shadow_validation_degraded_message_still_gets_row(client):
    # El caso que el spec marca como el más grave si se pierde: JSON
    # truncado, sin claims recuperables, sin términos de vocabulario.
    from shadow_validation import run_shadow_validation
    smid = str(uuid.uuid4())
    contract = ContractResult(
        contract_parsed=False, claims=[], analysis="texto truncado sin nada reconocible",
        judgment=None, degradation_reason="JSON no parsea", raw_text="texto truncado sin nada reconocible",
    )
    client.portal.call(run_shadow_validation, "conv-fake-uuid-4", smid, "kimi", contract)

    row = client.portal.call(_fetch_shadow_message, smid)
    assert row is not None
    assert bool(row[2]) is False  # contract_parsed
    assert row[3] == "JSON no parsea"  # degradation_reason
    verdicts = client.portal.call(_fetch_claim_verdicts, smid)
    hits = client.portal.call(_fetch_vocab_hits, smid)
    # cursor.fetchall() de aiomysql devuelve una tupla, no una lista.
    assert list(verdicts) == []
    assert list(hits) == []


def test_shadow_validation_leaves_validated_at_null_when_worker_dies_mid_run(client):
    # El caso que shadow_messages.validated_at existe para hacer visible:
    # si el proceso muere (acá simulado con una excepción real dentro del
    # sweep de vocabulario) DESPUÉS de insertar la fila pero ANTES de
    # completarla, validated_at queda NULL para siempre — esa ausencia ES
    # la métrica de pérdida, sin contador aparte (spec, sección 3).
    import shadow_validation
    from unittest.mock import patch

    smid = str(uuid.uuid4())
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis="texto cualquiera",
        judgment=None, degradation_reason=None, raw_text="...",
    )
    with patch.object(
        shadow_validation.governance_vocab_sweep, "sweep",
        side_effect=RuntimeError("worker murió acá, simulado"),
    ):
        try:
            client.portal.call(
                shadow_validation.run_shadow_validation,
                "conv-fake-uuid-5", smid, "jekyll", contract,
            )
        except RuntimeError:
            pass  # esperado — lo que importa es el estado que quedó en DB

    row = client.portal.call(_fetch_shadow_message, smid)
    assert row is not None  # la fila SÍ se insertó (al encolar, antes del crash)
    assert row[-1] is None  # validated_at — nunca se completó


def test_shadow_validation_skips_when_conv_uuid_is_none(client):
    from shadow_validation import run_shadow_validation
    smid = str(uuid.uuid4())
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis="x", judgment=None,
        degradation_reason=None, raw_text="x",
    )
    client.portal.call(run_shadow_validation, None, smid, "jekyll", contract)
    row = client.portal.call(_fetch_shadow_message, smid)
    assert row is None  # sin conv_uuid no hay a qué mensaje navegar, no se encola


def test_chat_endpoint_enqueues_shadow_validation(client):
    import http_client
    from auth.jwt import create_access_token
    from tests.test_chat_contract_wrapper import _FakePostClient, _FakeResponse

    token = create_access_token("test-shadow-e2e-user", "test-shadow-e2e-tenant", "operator")
    fake = _FakePostClient(_FakeResponse({
        "choices": [{"message": {"content":
            '{"claim": [], "analysis": "no hay nada que afirmar", "judgment": null}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post(
            "/api/chat",
            json={"message": "hola shadow", "facet": "jekyll"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        http_client._client = original
    assert resp.status_code == 200
    assert resp.json()["contract_degraded"] is False
