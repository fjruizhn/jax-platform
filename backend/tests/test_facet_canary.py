"""Sonda de facets.

NINGUN test de este archivo hace una llamada real a un proveedor:
_invoke_facet esta parcheado en todos. Ver "Riesgo de costo" en el plan
-- el 2026-08-24, correr pytest disparo 11 dispatches reales a produccion
por un archivo con codigo a nivel de modulo y nombre descubrible."""
import asyncio
import pytest
from jax_engine import facet_canary
from api import chat as chat_mod


def _config():
    return {"personalities": {
        "jax_local": {}, "hyde": {}, "jekyll": {},
        "hipatia": {}, "thot": {}, "ada": {}, "kimi": {}}}


def test_canary_facets_excluye_hyde_y_no_filtra_por_transporte():
    facets = facet_canary.canary_facets(_config())
    assert "hyde" not in facets          # chat() lo corta antes del dispatch
    assert "kimi" in facets              # DEBE sondearse: reporta
                                         # unsupported_transport, no invisible
    assert set(facets) == {"jax_local", "jekyll", "hipatia",
                           "thot", "ada", "kimi"}


def test_canary_message_no_dispara_el_cortocircuito_de_identidad():
    """Trampa real: _is_model_identity_question() cortocircuitea ANTES del
    dispatch y devuelve una respuesta enlatada. Si CANARY_MESSAGE pareciera
    una pregunta de identidad, la sonda reportaria `ok` SIN haber tocado al
    proveedor -- el detector mintiendo en verde."""
    assert chat_mod._is_model_identity_question(facet_canary.CANARY_MESSAGE) is False


def test_la_sonda_pasa_POR_el_gate_y_no_lo_saltea(monkeypatch):
    """La sonda invoca _invoke_facet -- la MISMA funcion del chat real --
    y por lo tanto pasa por el gate. Si en vez de eso resolviera el facet
    por su cuenta y llamara al proveedor directo, reportaria sano mientras
    el gate deniega a todos los usuarios reales.

    (El outcome exacto de una denegacion se verifica en Task 3, donde vive
    la instrumentacion. Aca se verifica el camino.)"""
    llamadas = []
    async def espia(facet, config, user_id, message,
                    semantic_context=None, *, source="chat"):
        llamadas.append((facet, source))
        return "⚠️ acceso no autorizado", None
    monkeypatch.setattr(facet_canary, "_invoke_facet", espia)

    out = asyncio.run(facet_canary.probe_facet("thot", _config(), "canary_periodic"))

    assert llamadas == [("thot", "canary_periodic")]
    assert out is None      # invoco; el outcome real lo registro _invoke_facet


def test_probe_facet_NUNCA_devuelve_ok(monkeypatch):
    """Un `return "ok"` sobre "no lanzo excepcion" seria un SEGUNDO lugar
    decidiendo que es sano -- y una denegacion del gate retorna
    normalmente, asi que reportaria verde sobre el fallo que esta ronda
    cierra."""
    async def denegado(*a, **k): return "⚠️ acceso no autorizado", None
    monkeypatch.setattr(facet_canary, "_invoke_facet", denegado)

    out = asyncio.run(facet_canary.probe_facet("thot", _config(), "canary_periodic"))
    assert out != "ok"
    assert out is None


def test_la_sonda_distingue_config_error_de_provider_error(monkeypatch):
    """La sonda es donde esta distincion se materializa mas seguido: la
    sonda por rebinding existe justamente para el escenario de un binding
    recien aprobado, que es cuando aparece un max_tokens_param sin sembrar.

    DOS casos reales, uno por cada outcome. NO uno solo asumiendo que el
    otro sale por simetria: si la clasificacion se rompiera en una sola
    direccion -- por ejemplo un `except Exception` puesto antes del
    especifico, que se lo come -- un test que solo cubra un lado pasa
    verde con el bug vivo."""
    from api.chat import ModelDispatchConfigError
    import facet_health as fh

    recorded = []
    async def fake_record(facet, outcome, source, detail=None):
        recorded.append(outcome); return True

    # Caso 1: falla NUESTRA -- fila del catalogo mal sembrada.
    monkeypatch.setattr(chat_mod, "record_facet_health", fake_record)
    async def config_roto(*a, **k):
        raise ModelDispatchConfigError("max_tokens_param es NULL")
    monkeypatch.setattr(chat_mod, "_invoke_facet_dispatch", config_roto)
    with pytest.raises(ModelDispatchConfigError):
        asyncio.run(chat_mod._invoke_facet("thot", _config(), "u", "h",
                                           source="canary_rebind"))
    assert recorded == [fh.OUTCOME_CONFIG_ERROR]

    # Caso 2: falla DEL PROVEEDOR -- misma forma, outcome distinto.
    recorded.clear()
    async def proveedor_caido(*a, **k):
        raise RuntimeError("502 Bad Gateway")
    monkeypatch.setattr(chat_mod, "_invoke_facet_dispatch", proveedor_caido)
    with pytest.raises(RuntimeError):
        asyncio.run(chat_mod._invoke_facet("thot", _config(), "u", "h",
                                           source="canary_rebind"))
    assert recorded == [fh.OUTCOME_PROVIDER_ERROR]


def test_probe_facet_registra_probe_error_si_falla_antes_de_invocar(monkeypatch):
    async def boom(*a, **k): raise RuntimeError("no pude leer config")
    monkeypatch.setattr(facet_canary, "_invoke_facet", boom)
    recorded = []
    async def fake_record(facet, outcome, source, detail=None):
        recorded.append((facet, outcome)); return True
    monkeypatch.setattr(facet_canary, "record_facet_health", fake_record)

    out = asyncio.run(facet_canary.probe_facet("thot", _config(), "canary_periodic"))
    assert out == "probe_error"
    assert recorded == [("thot", "probe_error")]


def test_el_loop_NO_EJECUTA_NINGUNA_SONDA_bajo_pytest(monkeypatch):
    """Regla 3 del "Riesgo de costo".

    Prueba el EFECTO, no el detector: verificar que
    _running_under_pytest() devuelve True probaria que la funcion sabe
    donde esta, no que el loop se abstiene. Lo que puede costar plata es
    que probe_all corra -- eso es lo que se asserta.

    Si esta proteccion se rompe, correr la suite dispara sondas pagas
    contra los 4 facets: exactamente el accidente del 2026-08-24."""
    llamadas = []
    async def espia(source="canary_periodic"):
        llamadas.append(source)
        return []
    monkeypatch.setattr(facet_canary, "probe_all", espia)

    # Si el guard fallara, start_facet_canary() entraria en `while True` y
    # este test colgaria en vez de fallar. El timeout lo convierte en un
    # fallo legible.
    async def _run():
        await asyncio.wait_for(facet_canary.start_facet_canary(), timeout=5)

    asyncio.run(_run())

    assert llamadas == [], (
        "start_facet_canary() ejecuto sondas bajo pytest -- son llamadas "
        "PAGAS a proveedores reales")


def test_running_under_pytest_detecta_el_entorno():
    """Complemento del anterior: el detector en si. Por separado, para que
    quede claro cual de los dos prueba que -- si este pasa y el otro falla,
    el guard existe pero no se esta aplicando."""
    assert facet_canary._running_under_pytest() is True
