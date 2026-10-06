"""El chat muestra SOLO el juicio, no el análisis interno del modelo.

Decisión de Fernando (2026-10-05): el usuario veía el razonamiento en tercera
persona («Fernando me saluda…»). Si el juicio viene vacío se muestra el
análisis, para no dejar la respuesta vacía.
"""
import pytest

from api.chat import ContractResult


def _contract(analysis, judgment):
    return ContractResult(
        contract_parsed=True, claims=[], analysis=analysis, judgment=judgment,
        degradation_reason=None, raw_text="...",
    )


def test_narrative_con_juicio_muestra_solo_el_juicio():
    from api.governed_chat import _narrative
    texto = _narrative(_contract("Fernando me saluda, debo responder", "Hola Fernando"))
    assert texto == "Hola Fernando"
    assert "Fernando me saluda" not in texto


@pytest.mark.parametrize("vacio", [None, "", "   "])
def test_narrative_sin_juicio_cae_al_analisis(vacio):
    from api.governed_chat import _narrative
    assert _narrative(_contract("solo analisis", vacio)) == "solo analisis"


def test_build_display_response_con_juicio_no_muestra_analisis():
    from api.chat import _build_display_response
    texto, degradada = _build_display_response(_contract("razonamiento interno", "conclusion"))
    assert "razonamiento interno" not in texto
    assert "conclusion" in texto
    assert degradada is False


@pytest.mark.parametrize("vacio", [None, "", "  "])
def test_build_display_response_sin_juicio_cae_al_analisis(vacio):
    from api.chat import _build_display_response
    texto, _ = _build_display_response(_contract("solo analisis", vacio))
    assert texto == "solo analisis"
