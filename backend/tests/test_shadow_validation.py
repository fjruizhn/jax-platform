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

NOTA DE ALCANCE (review de la tarea, ver el addendum de review en
task-5-report.md): se agregó
`test_shadow_validation_leaves_validated_at_null_when_context_load_fails_before_the_insert`
(crash simulado ANTES del insert, en la carga de config de gobernanza —
distinto del crash ya cubierto dentro de la región protegida) y se
renombró `test_chat_endpoint_enqueues_shadow_validation` a
`test_chat_endpoint_does_not_break_when_shadow_validation_is_enqueued`
porque el nombre original implicaba que ejercitaba el camino de
escritura a DB, cosa que no puede: el `user_id` no numérico del token
hace que `conv_uuid` quede `None` de forma estructural (ver el
docstring del test).
"""
import json
import uuid

from api.chat import ContractResult


def _grounding():
    import governance_context
    import grounding as governance_grounding
    ctx, _, _ = governance_context.validation_context()
    return governance_grounding.build_snapshot(ctx)


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
    client.portal.call(run_shadow_validation, "conv-fake-uuid", smid, "jekyll", contract, _grounding())

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
    client.portal.call(run_shadow_validation, "conv-fake-uuid-2", smid, "jekyll", contract, _grounding())

    verdicts = client.portal.call(_fetch_claim_verdicts, smid)
    assert len(verdicts) == 1
    predicate, status, args = verdicts[0]
    assert predicate == "CAPABILITY_AVAILABLE"
    # Sin evidence_pointer la autoridad es INFERIDO: se ofreció grounding y
    # no citó (spec §4.1 paso 4).
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
    client.portal.call(run_shadow_validation, "conv-fake-uuid-3", smid, "jax_local", contract, _grounding())

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
    client.portal.call(run_shadow_validation, fake_conv_uuid, smid, "jekyll", contract, _grounding())
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
    client.portal.call(run_shadow_validation, "conv-fake-uuid-4", smid, "kimi", contract, _grounding())

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
                "conv-fake-uuid-5", smid, "jekyll", contract, _grounding(),
            )
        except RuntimeError:  # fail-soft: captura el RuntimeError inyectado a proposito por el test (side_effect) para poder inspeccionar el estado post-crash; tipo acotado, no bare except
            pass  # esperado — lo que importa es el estado que quedó en DB

    row = client.portal.call(_fetch_shadow_message, smid)
    assert row is not None  # la fila SÍ se insertó (al encolar, antes del crash)
    assert row[-1] is None  # validated_at — nunca se completó


def test_shadow_validation_leaves_validated_at_null_when_context_load_fails_before_the_insert(client):
    # Distinto del test de arriba: acá el crash simulado está en
    # _validation_context() (carga de config estática de gobernanza:
    # load_vocabulary()/load_predicates()/load_validation_context(), que
    # pueden lanzar por YAML mal formado, el guard fail-closed propio de
    # loaders.py, o config.toml de las_manos ilegible). Si esa llamada
    # ocurriera ANTES del insert a shadow_messages, un crash acá dejaría
    # CERO fila, no una fila con validated_at NULL — la pérdida
    # completamente invisible que este mecanismo existe para prevenir. El
    # test de arriba (sweep de vocabulario) no puede detectar esta clase
    # de falla porque su punto de patch ya está DENTRO de la región
    # protegida (después del insert) — este necesita su propio punto de
    # patch, antes.
    import shadow_validation
    from unittest.mock import patch

    smid = str(uuid.uuid4())
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis="texto cualquiera",
        judgment=None, degradation_reason=None, raw_text="...",
    )
    with patch.object(
        shadow_validation, "_validation_context",
        side_effect=RuntimeError("carga de config de gobernanza murió acá, simulado"),
    ):
        try:
            client.portal.call(
                shadow_validation.run_shadow_validation,
                "conv-fake-uuid-6", smid, "jekyll", contract, _grounding(),
            )
        except RuntimeError:  # fail-soft: mismo patron: captura el RuntimeError inyectado a proposito para inspeccionar el estado post-crash
            pass  # esperado — lo que importa es el estado que quedó en DB

    row = client.portal.call(_fetch_shadow_message, smid)
    assert row is not None  # la fila SÍ se insertó — el insert corre ANTES de _validation_context()
    assert row[-1] is None  # validated_at — nunca se completó


def test_shadow_validation_clamps_overlong_facet_so_insert_succeeds(client):
    # Defensa en profundidad del finding 1 de la revisión final: api/chat.py
    # ya rechaza facets desconocidas con 400 antes de llegar acá, pero
    # run_shadow_validation es invocable por cualquier otro caller futuro.
    # shadow_messages.facet es VARCHAR(30) (db/migrations.py) — sin el
    # clamp en _insert_shadow_message, este INSERT (el primero de la
    # función, antes de tocar la config de gobernanza) lanzaría
    # "Data too long" y la fila de la garantía fail-closed nunca se
    # crearía: cero fila, no una fila con validated_at NULL.
    from shadow_validation import run_shadow_validation
    smid = str(uuid.uuid4())
    overlong_facet = "x" * 100
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis="x", judgment=None,
        degradation_reason=None, raw_text="x",
    )
    client.portal.call(
        run_shadow_validation, "conv-fake-uuid-overlong-facet", smid, overlong_facet, contract, _grounding(),
    )
    row = client.portal.call(_fetch_shadow_message, smid)
    assert row is not None
    assert row[1] == overlong_facet[:30]
    assert row[-1] is not None  # validated_at — el worker corrió completo


def test_shadow_validation_skips_when_conv_uuid_is_none(client):
    from shadow_validation import run_shadow_validation
    smid = str(uuid.uuid4())
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis="x", judgment=None,
        degradation_reason=None, raw_text="x",
    )
    client.portal.call(run_shadow_validation, None, smid, "jekyll", contract, _grounding())
    row = client.portal.call(_fetch_shadow_message, smid)
    assert row is None  # sin conv_uuid no hay a qué mensaje navegar, no se encola


def test_chat_endpoint_does_not_break_when_shadow_validation_is_enqueued(client):
    # OJO — esto NO prueba que la fila de shadow_messages se haya escrito.
    # El user_id del token es no numérico a propósito (mismo patrón que
    # test_chat_contract_wrapper.py: "así aislamos el único llamado
    # saliente que nos importa"), lo que hace que `int(user_id)` falle en
    # chat(), `conv_uuid` quede None, y run_shadow_validation() retorne
    # de inmediato sin tocar la DB (ver
    # test_shadow_validation_skips_when_conv_uuid_is_none) —
    # estructuralmente no puede ejercitar el camino de escritura, con
    # cualquier timing. Lo que este test sí prueba: que agregar
    # `background_tasks.add_task(run_shadow_validation, ...)` al handler
    # no rompe el endpoint (200, contrato no degradado). El camino de
    # escritura real (insert + validación + validated_at) ya está cubierto
    # de punta a punta por test_shadow_validation_writes_message_row_and_sets_validated_at,
    # que llama a run_shadow_validation() directo — sin depender del
    # timing de BackgroundTasks dentro de TestClient, que no es
    # determinístico (ver nota original más abajo).
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


def test_chat_endpoint_survives_shadow_validation_import_failure(client):
    # Finding 4 de la revisión final: el import diferido de
    # shadow_validation (agregado en Task 5 para evitar un ciclo de
    # imports) corre DESPUÉS de que el turno de chat ya tuvo éxito — el
    # mensaje del asistente ya se guardó y se transmitió por WebSocket.
    # Un subsistema de solo-medición nunca debe poder tumbar una respuesta
    # que ya se completó. Acá simulamos ese fallo forzando a que el
    # import de shadow_validation lance (sys.modules[nombre] = None hace
    # que Python levante ImportError al importarlo) y confirmamos que el
    # endpoint igual responde 200 con el cuerpo correcto.
    import sys
    from unittest.mock import patch

    import http_client
    from auth.jwt import create_access_token
    from tests.test_chat_contract_wrapper import _FakePostClient, _FakeResponse

    token = create_access_token("test-shadow-import-fail-user", "test-shadow-import-fail-tenant", "operator")
    fake = _FakePostClient(
        _FakeResponse({
            "choices": [{"message": {"content":
                '{"claim": [], "analysis": "sobrevivio al fallo de encolado", "judgment": null}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }),
        authorize_response=_FakeResponse({"allowed": True, "reason": "OK"}),
    )
    original = http_client._client
    http_client._client = fake
    try:
        with patch.dict(sys.modules, {"shadow_validation": None}):
            resp = client.post(
                "/api/chat",
                json={"message": "hola shadow import fail", "facet": "jekyll"},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        http_client._client = original
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contract_degraded"] is False
    assert "sobrevivio al fallo de encolado" in body["response"]
