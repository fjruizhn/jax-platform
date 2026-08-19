"""
Wrapper de contrato {claim/analysis/judgment} en chat.py (REFORMAS-v3
Fase 2 Sub-proyecto 2). _parse_contract_response es puro — sin red, sin
DB — se testea con texto crudo simulando lo que devolvería cada
faceta, incluido el truncamiento real de Kimi (488 bytes).

_invoke_facet devuelve tuple[str, UsageInfo | None] (firma de
infra/facetas-bloque-d, ya mergeada a master). is_canned se deriva en
el call site del endpoint como "usage is None" — ver
test_chat_endpoint_marks_contract_degraded_on_truncated_json más abajo.
"""
from api.chat import _parse_contract_response


def test_parse_contract_valid_json_with_claims():
    raw = '{"claim": [{"predicate": "CAPABILITY_AVAILABLE", "args": {"name": "code_swarm"}}], "analysis": "revisé el catálogo", "judgment": "está disponible"}'
    result = _parse_contract_response(raw)
    assert result.contract_parsed is True
    assert result.claims == [{"predicate": "CAPABILITY_AVAILABLE", "args": {"name": "code_swarm"}}]
    assert result.analysis == "revisé el catálogo"
    assert result.judgment == "está disponible"
    assert result.degradation_reason is None


def test_parse_contract_valid_json_no_claims():
    raw = '{"claim": [], "analysis": "no hay nada que afirmar acá", "judgment": null}'
    result = _parse_contract_response(raw)
    assert result.contract_parsed is True
    assert result.claims == []
    assert result.judgment is None


def test_parse_contract_strips_markdown_fence():
    raw = '```json\n{"claim": [], "analysis": "ok", "judgment": null}\n```'
    result = _parse_contract_response(raw)
    assert result.contract_parsed is True
    assert result.analysis == "ok"


def test_parse_contract_truncated_json_degrades():
    # Simula el truncamiento real de Kimi a 488 bytes: JSON cortado a
    # mitad de un claim.
    raw = '{"claim": [{"predicate": "CAPABILITY_AVAI'
    result = _parse_contract_response(raw)
    assert result.contract_parsed is False
    assert result.claims == []
    assert result.analysis == raw
    assert result.judgment is None
    assert result.degradation_reason is not None
    assert result.raw_text == raw


def test_parse_contract_not_a_json_object_degrades():
    raw = '["no", "es", "un", "objeto"]'
    result = _parse_contract_response(raw)
    assert result.contract_parsed is False
    assert "objeto" in result.degradation_reason


def test_parse_contract_missing_analysis_key_degrades():
    raw = '{"claim": []}'
    result = _parse_contract_response(raw)
    assert result.contract_parsed is False
    assert "analysis" in result.degradation_reason


def test_parse_contract_malformed_claim_entry_degrades():
    raw = '{"claim": [{"predicate": "X"}], "analysis": "ok"}'
    result = _parse_contract_response(raw)
    assert result.contract_parsed is False


def test_parse_contract_overlong_predicate_degrades():
    # Finding 2 de la revisión final: predicate se escribe en
    # shadow_claim_verdicts.predicate VARCHAR(50) (db/migrations.py). Sin
    # este chequeo, un predicate más largo hacía que el INSERT lanzara
    # "Data too long" a mitad del loop de claims dentro de
    # run_shadow_validation, abortando el resto de claims Y los vocab
    # hits de ESE mensaje — una pérdida sesgada hacia los modelos que
    # peor siguen el contrato (justo lo que el spec prohíbe). La solución
    # correcta es tratarlo como el resto de los casos de claim mal
    # formado de esta función: degradar el mensaje completo acá, ANTES de
    # que llegue a shadow_validation.py.
    overlong_predicate = "P" * 51
    raw = (
        '{"claim": [{"predicate": "' + overlong_predicate + '", "args": {}}], '
        '"analysis": "ok", "judgment": null}'
    )
    result = _parse_contract_response(raw)
    assert result.contract_parsed is False
    assert result.claims == []
    assert result.degradation_reason is not None
    assert "50" in result.degradation_reason


def test_parse_contract_predicate_at_exactly_50_chars_is_accepted():
    # Caso límite: 50 caracteres exactos calzan en VARCHAR(50), no debe
    # degradar.
    predicate_50 = "P" * 50
    raw = (
        '{"claim": [{"predicate": "' + predicate_50 + '", "args": {}}], '
        '"analysis": "ok", "judgment": null}'
    )
    result = _parse_contract_response(raw)
    assert result.contract_parsed is True
    assert result.claims == [{"predicate": predicate_50, "args": {}}]


def test_parse_contract_plain_text_not_json_degrades():
    raw = "esto no es json en absoluto, es texto libre normal"
    result = _parse_contract_response(raw)
    assert result.contract_parsed is False
    assert result.analysis == raw


def test_contract_prompt_suffix_mentions_the_three_keys():
    # Verifica el contenido de la constante en aislamiento. La conexión
    # real al system_prompt dentro de _invoke_facet se cubre por
    # test_jax_local_system_prompt_states_resolved_model (ya existente en
    # test_facet_model_wiring.py, sigue pasando tras el wiring) y por
    # test_chat_endpoint_marks_contract_degraded_on_truncated_json más
    # abajo, que ejercita el endpoint completo.
    from api.chat import _CONTRACT_PROMPT_SUFFIX
    assert "claim" in _CONTRACT_PROMPT_SUFFIX
    assert "analysis" in _CONTRACT_PROMPT_SUFFIX
    assert "judgment" in _CONTRACT_PROMPT_SUFFIX


def test_build_display_response_valid_contract_no_judgment():
    from api.chat import _build_display_response
    from api.chat import ContractResult
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis="mi análisis",
        judgment=None, degradation_reason=None, raw_text="...",
    )
    text, degraded = _build_display_response(contract)
    assert text == "mi análisis"
    assert degraded is False


def test_build_display_response_valid_contract_with_judgment():
    from api.chat import _build_display_response
    from api.chat import ContractResult
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis="mi análisis",
        judgment="mi conclusión", degradation_reason=None, raw_text="...",
    )
    text, degraded = _build_display_response(contract)
    assert "mi análisis" in text
    assert "mi conclusión" in text
    assert degraded is False


def test_build_display_response_degraded_shows_raw_text():
    from api.chat import _build_display_response
    from api.chat import ContractResult
    contract = ContractResult(
        contract_parsed=False, claims=[], analysis="texto crudo truncado",
        judgment=None, degradation_reason="JSON no parsea", raw_text="texto crudo truncado",
    )
    text, degraded = _build_display_response(contract)
    assert text == "texto crudo truncado"
    assert degraded is True


import http_client
from unittest.mock import AsyncMock, patch


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        pass


class _FakePostClient:
    def __init__(self, response):
        self._response = response

    async def post(self, url, **kwargs):
        return self._response


def test_chat_endpoint_marks_contract_degraded_on_truncated_json(client):
    from auth.jwt import create_access_token
    # ids no numéricos a propósito (mismo patrón que test_facet_model_wiring.py):
    # chat.py solo toca el camino de memoria semántica cuando user_id/tenant_id
    # parsean a int — así aislamos el único llamado saliente que nos importa.
    token = create_access_token("test-contract-user", "test-contract-tenant", "operator")
    fake = _FakePostClient(_FakeResponse({
        "choices": [{"message": {"content": '{"claim": [{"predicate": "CAPABILITY_AVAI'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post(
            "/api/chat",
            json={"message": "hola", "facet": "jekyll"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        http_client._client = original
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contract_degraded"] is True
    assert body["response"].startswith('{"claim"')


def test_chat_endpoint_contract_not_degraded_on_valid_json(client):
    """Complemento del test de arriba: cuando la faceta SÍ devuelve el
    contrato bien formado, contract_degraded debe ser False y la respuesta
    mostrada es analysis (+judgment), no el JSON crudo — prueba que
    is_canned=False (llamada real) efectivamente dispara el parseo, y que
    el parseo exitoso no degrada."""
    from auth.jwt import create_access_token
    token = create_access_token("test-contract-user-2", "test-contract-tenant-2", "operator")
    fake = _FakePostClient(_FakeResponse({
        "choices": [{"message": {"content": (
            '{"claim": [], "analysis": "mi analisis real", "judgment": "mi conclusion real"}'
        )}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post(
            "/api/chat",
            json={"message": "hola", "facet": "jekyll"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        http_client._client = original
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contract_degraded"] is False
    assert "mi analisis real" in body["response"]
    assert "mi conclusion real" in body["response"]


async def _call_invoke_facet_jax_local_identity_question():
    from api.chat import _invoke_facet
    config = {"personalities": {"jax_local": {"system_prompt": "Sos JAX."}}}
    return await _invoke_facet("jax_local", config, "some-user", "que modelo sos", None)


def test_invoke_facet_identity_question_returns_usage_none(client):
    """Camino usage=None (pregunta de identidad de modelo): la respuesta
    enlatada de _model_identity_reply nunca pasa por un transporte real,
    así que _invoke_facet debe devolver usage=None — la señal que el
    endpoint usa (is_canned = usage is None) para nunca llamar a
    _parse_contract_response sobre una respuesta enlatada que no es JSON.

    _invoke_facet hace una consulta real a facet_models (get_pool()), así
    que corre via client.portal.call — mismo patrón que
    test_facet_model_wiring.py — para compartir el loop/pool de la sesión
    del fixture `client` en vez de abrir uno propio (un event loop propio
    deja el pool de aiomysql atado a un loop que se cierra al terminar el
    test, envenenando la conexión para el resto de la suite)."""
    from api.chat import _parse_contract_response

    text, usage = client.portal.call(_call_invoke_facet_jax_local_identity_question)
    assert usage is None
    is_canned = usage is None
    # Confirma que, aplicando la regla del endpoint (parsear solo si
    # not is_canned), esta respuesta jamás pasaría por el parser.
    contract = _parse_contract_response(text) if not is_canned else None
    assert contract is None
