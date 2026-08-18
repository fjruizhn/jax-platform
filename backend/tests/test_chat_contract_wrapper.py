"""
Wrapper de contrato {claim/analysis/judgment} en chat.py (REFORMAS-v3
Fase 2 Sub-proyecto 2). _parse_contract_response es puro — sin red, sin
DB — se testea con texto crudo simulando lo que devolvería cada
faceta, incluido el truncamiento real de Kimi (488 bytes).

NOTA DE ALCANCE (ver task-3-report.md): este task quedó BLOQUEADO en el
Step 13 (wiring del endpoint chat()) porque el brief asume que
_invoke_facet ya devuelve tuple[str, UsageInfo | None] — esa firma
existe solo en la rama infra/facetas-bloque-d (commit 448a707), que NO
es ancestro de esta rama (SP2 está basada en master). Este archivo solo
cubre las piezas puras que no dependen de esa señal faltante:
_parse_contract_response y _build_display_response. No hay test de
_CONTRACT_PROMPT_SUFFIX conectado al system_prompt real (no wireado) ni
test de integración del endpoint (Step 14) — ver reporte para detalle.
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


def test_parse_contract_plain_text_not_json_degrades():
    raw = "esto no es json en absoluto, es texto libre normal"
    result = _parse_contract_response(raw)
    assert result.contract_parsed is False
    assert result.analysis == raw


def test_contract_prompt_suffix_mentions_the_three_keys():
    # Solo verifica el contenido de la constante en aislamiento. NO
    # verifica que esté conectada a ningún system_prompt real — esa
    # conexión (Step 7b del brief) está bloqueada, ver nota de alcance
    # arriba y task-3-report.md.
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
