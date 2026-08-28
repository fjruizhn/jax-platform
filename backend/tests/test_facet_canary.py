"""Sonda de facets.

NINGUN test de este archivo hace una llamada real a un proveedor:
_invoke_facet esta parcheado en todos. Ver "Riesgo de costo" en el plan
-- el 2026-08-24, correr pytest disparo 11 dispatches reales a produccion
por un archivo con codigo a nivel de modulo y nombre descubrible."""
import asyncio
import inspect
import logging
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
    verde con el bug vivo.

    Ronda de correccion 1 (Hallazgo 1): las aserciones de abajo comparan
    contra el LITERAL ("config_error"/"provider_error"), no contra la
    constante (fh.OUTCOME_CONFIG_ERROR/fh.OUTCOME_PROVIDER_ERROR). La
    guarda mecanica de la Task 3.5 compara CONJUNTOS (OUTCOMES), y un
    conjunto es invariante bajo permutacion: si alguien transpone los
    VALORES de dos constantes (p.ej. OUTCOME_CONFIG_ERROR = "unsupported_transport"
    por error de copy-paste), el frozenset queda identico, esa guarda pasa
    verde, y un assert contra la constante tambien pasaria verde porque
    compara constante contra constante -- el test cuya razon de existir es
    que dos valores no se confundan quedaria ciego a que se confundieran.
    Contra el literal, ese mismo bug lo detecta."""
    from api.chat import ModelDispatchConfigError

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
    assert recorded == ["config_error"]

    # Caso 2: falla DEL PROVEEDOR -- misma forma, outcome distinto.
    recorded.clear()
    async def proveedor_caido(*a, **k):
        raise RuntimeError("502 Bad Gateway")
    monkeypatch.setattr(chat_mod, "_invoke_facet_dispatch", proveedor_caido)
    with pytest.raises(RuntimeError):
        asyncio.run(chat_mod._invoke_facet("thot", _config(), "u", "h",
                                           source="canary_rebind"))
    assert recorded == ["provider_error"]


def test_outcome_unsupported_transport_no_se_confunde_con_config_error(monkeypatch):
    """Ronda de correccion 1, Hallazgo 1 (pin adicional): la unica
    permutacion que sobrevive a la guarda de conjuntos de la Task 3.5 es
    intercambiar los VALORES de dos constantes -- 'unsupported_transport'
    no aparecia en ningun literal de esta suite hasta ahora, asi que un
    copy-paste que lo pusiera donde va 'config_error' (o viceversa) no lo
    habria detectado nada. Este test fija el otro extremo de esa
    permutacion con su propio literal."""
    import facet_health as fh
    assert fh.OUTCOME_UNSUPPORTED_TRANSPORT == "unsupported_transport"
    assert fh.OUTCOME_CONFIG_ERROR == "config_error"
    assert fh.OUTCOME_UNSUPPORTED_TRANSPORT != fh.OUTCOME_CONFIG_ERROR


def test_source_constants_pertenecen_a_SOURCES_con_literales(monkeypatch):
    """Hallazgo 3: 'canary_periodic' era un literal suelto en
    facet_canary.py, sin constante ni guarda que lo atara a
    facet_health.SOURCES -- el mismo problema que la Task 3.5 cerro para
    `outcome`, un casillero al lado sin cerrar. Si SOURCE_CANARY_PERIODIC
    divergiera del valor real en SOURCES, record_facet_health lanzaria
    ValueError DESDE ADENTRO del except de probe_facet, abortando el
    barrido entero en vez de degradar una fila."""
    import facet_health as fh
    assert fh.SOURCE_CHAT == "chat"
    assert fh.SOURCE_CANARY_PERIODIC == "canary_periodic"
    assert fh.SOURCE_CANARY_REBIND == "canary_rebind"
    assert fh.SOURCES == {"chat", "canary_periodic", "canary_rebind"}
    assert facet_canary.SOURCE_CANARY_PERIODIC in fh.SOURCES


def test_invoke_facet_default_source_pertenece_a_SOURCES():
    """Hallazgo 6, ronda de corrección 2: `_invoke_facet` (api/chat.py)
    tenía `source: str = "chat"` como literal suelto, sin atar al
    `SOURCE_CHAT` de facet_health.py -- mismo problema que el hallazgo 3,
    un escalón más grave: si `SOURCE_CHAT` divergiera del default de
    chat.py, `record_facet_health` lanzaría `ValueError` FUERA del `try`
    del envoltorio (ver líneas 907-924 de api/chat.py), así que sube al
    endpoint como HTTP 502 en CADA turno de chat real -- con el LLM ya
    generado y pagado, y sin fila en axioma_usage. El hallazgo 3 solo
    abortaba un barrido de la sonda; este rompe el chat real.

    inspect.signature() lee el default REAL en runtime (no una copia a
    mano del valor esperado), y la aserción usa el literal "chat" (mismo
    criterio del hallazgo 1: literal en la aserción, no la constante)."""
    import facet_health as fh
    sig = inspect.signature(chat_mod._invoke_facet)
    default_source = sig.parameters["source"].default
    assert default_source == "chat"
    assert default_source in fh.SOURCES


def test_probe_facet_NO_registra_cuando_invoke_facet_lanza(monkeypatch):
    """El corazon de la opcion B (diseno 2026-08-28).

    Cuando _invoke_facet lanza, YA registro el evento clasificado
    (provider_error / config_error / ...) antes de re-lanzar -- es un
    envoltorio total, propiedad protegida por
    tests/test_policy_invoke_facet_envoltorio.py. Un probe_error de
    probe_facet seria una SEGUNDA fila para la misma causa, ~800us mas
    nueva, y el lector (jacobs/facet_health.py, repo jax) toma MAX(ts):
    ganaria la generica y la alerta diria "la sonda fallo" en vez de la
    causa accionable.

    El valor de retorno NO cambia: probe_facet sigue devolviendo
    'probe_error' para que probe_all pueda contar sondeos fallidos sin
    tocar la DB."""
    async def boom(*a, **k):
        raise RuntimeError("el proveedor devolvio 502")
    monkeypatch.setattr(facet_canary, "_invoke_facet", boom)
    recorded = []
    async def fake_record(facet, outcome, source, detail=None):
        recorded.append((facet, outcome)); return True
    monkeypatch.setattr(facet_canary, "record_facet_health", fake_record)

    out = asyncio.run(facet_canary.probe_facet("thot", _config(), "canary_periodic"))
    assert out == "probe_error"
    assert recorded == [], f"probe_facet escribio de mas: {recorded}"


def test_probe_after_rebind_SI_registra_cuando_falla_antes_de_invocar(monkeypatch):
    """La otra mitad: aca probe_error SI es el unico evento.

    probe_after_rebind puede fallar ANTES de llegar a _invoke_facet
    (invalidate_facet_cache, _load_config), y en ese camino nadie mas
    escribio. Sin este registro el fallo quedaria solo en el journal.
    Este test existe para que la Task 2 no se lleve puesta esa mitad."""
    def explota():
        raise RuntimeError("config.toml ilegible")
    monkeypatch.setattr(facet_canary, "_load_config", explota)
    monkeypatch.setattr(facet_canary, "invalidate_facet_cache", lambda k: True)
    monkeypatch.setattr(facet_canary, "CANARY_INTERVAL_SECONDS", 3600)
    recorded = []
    async def fake_record(facet, outcome, source, detail=None):
        recorded.append((facet, outcome, source)); return True
    monkeypatch.setattr(facet_canary, "record_facet_health", fake_record)

    out = asyncio.run(facet_canary.probe_after_rebind("thot"))
    assert out == "probe_error"
    assert recorded == [("thot", "probe_error", "canary_rebind")]


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


def test_intervalo_no_positivo_deshabilita_la_sonda_sin_silencio(monkeypatch, caplog):
    """Hallazgo 4: CANARY_INTERVAL_SECONDS <= 0 es un kill switch (mismo
    patron que FACET_CACHE_TTL_SECONDS en facet_resolver.py) -- tiene que
    apagar la sonda, y tiene que dejar rastro, no apagarse en silencio.

    El guard anti-pytest se prueba aparte (test_el_loop_NO_EJECUTA...) y
    corta ANTES de llegar a este chequeo bajo pytest real -- por eso acá
    se parchea _running_under_pytest para poder ejercitar la rama de
    abajo sin que el guard de arriba la tape. probe_all sigue espiado
    (y con timeout) por si el apagado no funcionara y el loop entrara
    igual: que este test falle legible, no que se cuelgue."""
    monkeypatch.setattr(facet_canary, "_running_under_pytest", lambda: False)
    monkeypatch.setattr(facet_canary, "CANARY_INTERVAL_SECONDS", 0)

    llamadas = []
    async def espia(source=facet_canary.SOURCE_CANARY_PERIODIC):
        llamadas.append(source)
        return []
    monkeypatch.setattr(facet_canary, "probe_all", espia)

    async def _run():
        await asyncio.wait_for(facet_canary.start_facet_canary(), timeout=5)

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run())

    assert llamadas == [], "CANARY_INTERVAL_SECONDS<=0 no deshabilito la sonda"
    assert any(
        "CANARY_INTERVAL_SECONDS" in r.getMessage() and "deshabilitada" in r.getMessage()
        for r in caplog.records
    ), "el apagado no dejo rastro en el log -- se apago en silencio"
