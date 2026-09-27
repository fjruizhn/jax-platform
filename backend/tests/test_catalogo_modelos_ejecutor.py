"""Ejecutor programado del catálogo de modelos (2026-09-27, revisado tras la
auditoría adversarial del commit d549335 -- A-1/A-2/A-5).

`catalogo_modelos_ejecutor.py` corre `model_catalog.sync_all()` fuera del
click de un superadmin. Estos tests son PUROS -- ninguno pide `client` ni
toca la base real: el propio `sync_all()` ya está cubierto por
test_model_catalog_sync_all.py y test_model_catalog_facetas_en_riesgo.py. Lo
que se prueba acá es la capa de arriba: código de salida, resumen, el envío
propio de Telegram, y el dedupe (que ahora exige un envío CONFIRMADO antes
de marcar algo como avisado).
"""
import asyncio
import json
from pathlib import Path

import http_client
import catalogo_modelos_ejecutor as ejecutor


def _resultado_ok():
    return {
        "ok": True, "providers": [], "enrich": {}, "providers_fallidos": [],
        "providers_saltados": [], "enrich_fallido": False, "nuevos": {},
        "facetas_en_riesgo": [],
    }


def _resultado_con_problemas(**overrides):
    base = {
        "ok": False, "providers": [], "enrich": {}, "providers_fallidos": [],
        "providers_saltados": ["anthropic"], "enrich_fallido": False, "nuevos": {},
        "facetas_en_riesgo": [], "code": "sync_con_errores",
    }
    base.update(overrides)
    return base


def _correr_async(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# main(): código de salida
# --------------------------------------------------------------------------

def _sin_aviso(monkeypatch):
    """Aísla main() del envío real de avisos -- eso lo prueban los tests de
    dedupe y de _enviar_telegram, más abajo."""
    async def _nada(resultado):
        return None
    monkeypatch.setattr(ejecutor, "_avisar", _nada)


def test_main_sale_0_cuando_ok(monkeypatch):
    async def _correr():
        return _resultado_ok()
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch)

    assert ejecutor.main() == 0


def test_main_sale_distinto_de_cero_cuando_hay_problemas(monkeypatch):
    async def _correr():
        return _resultado_con_problemas()
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch)

    assert ejecutor.main() != 0


def test_main_imprime_un_resumen_con_los_campos_clave(monkeypatch, capsys):
    async def _correr():
        return _resultado_con_problemas(providers_fallidos=["openai"])
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch)

    ejecutor.main()
    salida = capsys.readouterr().out
    assert "ok=False" in salida
    assert "openai" in salida
    assert "anthropic" in salida


def test_main_no_enmascara_el_codigo_de_salida_si_el_aviso_falla(monkeypatch):
    """Principio: un aviso roto (Telegram caído, disco lleno) NO puede
    convertir un job con problemas en un job que sale 0, NI puede
    propagarse fuera de main() como excepción sin control."""
    async def _correr():
        return _resultado_con_problemas()
    monkeypatch.setattr(ejecutor, "_correr", _correr)

    async def _avisar_que_revienta(resultado):
        raise RuntimeError("Telegram no responde")
    monkeypatch.setattr(ejecutor, "_avisar", _avisar_que_revienta)

    try:
        codigo = ejecutor.main()
    except RuntimeError:
        raise AssertionError("un fallo de aviso no puede propagarse fuera de main()")
    assert codigo != 0


# --------------------------------------------------------------------------
# A-2: sync_all()/_correr() reventando de una forma inesperada
# --------------------------------------------------------------------------

def test_main_avisa_y_sale_distinto_de_cero_si_correr_revienta(monkeypatch):
    """DB caída al conectar, import roto -- lo que sea que ni el propio
    try/except de sync_all() cubre. Antes esto salía en rojo MUDO."""
    async def _correr_roto():
        raise ConnectionRefusedError("DB caída de verdad")
    monkeypatch.setattr(ejecutor, "_correr", _correr_roto)

    llamadas = []

    async def _fake_enviar(mensaje):
        llamadas.append(mensaje)
        return True
    monkeypatch.setattr(ejecutor, "_enviar_telegram", _fake_enviar)

    codigo = ejecutor.main()

    assert codigo != 0
    assert len(llamadas) == 1
    assert "vigilante" in llamadas[0].lower()
    assert "ConnectionRefusedError" in llamadas[0]


def test_main_si_el_aviso_de_crash_tambien_falla_sigue_saliendo_en_rojo(monkeypatch, capsys):
    async def _correr_roto():
        raise RuntimeError("boom")
    monkeypatch.setattr(ejecutor, "_correr", _correr_roto)

    async def _enviar_que_revienta(mensaje):
        raise RuntimeError("Telegram también está caído")
    monkeypatch.setattr(ejecutor, "_enviar_telegram", _enviar_que_revienta)

    try:
        codigo = ejecutor.main()
    except RuntimeError:
        raise AssertionError("un fallo del aviso de crash no puede propagarse fuera de main()")
    assert codigo != 0
    assert "ok=False" in capsys.readouterr().out


def test_main_imprime_ok_true_no_avisa_nada_de_crash_en_el_camino_sano(monkeypatch):
    """Control negativo: el camino sano no pasa por el manejo de A-2."""
    async def _correr():
        return _resultado_ok()
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    llamadas = []

    async def _avisar(resultado):
        llamadas.append(resultado)
    monkeypatch.setattr(ejecutor, "_avisar", _avisar)

    assert ejecutor.main() == 0
    assert llamadas == [_resultado_ok()]


# --------------------------------------------------------------------------
# A-5: envío propio de Telegram (sin importar jacobs.reaper)
# --------------------------------------------------------------------------

class _FakePostResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakePostClient:
    def __init__(self, respuesta=None, excepcion=None):
        self._respuesta = respuesta
        self._excepcion = excepcion
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._excepcion:
            raise self._excepcion
        return self._respuesta


def test_modulo_no_importa_jacobs_reaper():
    """A-5: el import híbrido frágil que la auditoría marcó no puede volver
    -- ni como `from jacobs.reaper import ...` ni como `import jacobs`.
    Se verifica por AST (ast.Import/ImportFrom), no por texto: el módulo
    SÍ puede (y debe, en su docstring) seguir explicando en prosa por qué ya
    no se usa `jacobs.reaper` -- lo que no puede volver es la sentencia de
    import real."""
    import ast

    fuente = Path(ejecutor.__file__).read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    nombres_importados = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres_importados += [n.name for n in nodo.names]
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            nombres_importados.append(nodo.module)
    assert not any(n == "jacobs" or n.startswith("jacobs.") for n in nombres_importados), nombres_importados


def test_enviar_telegram_usa_el_cliente_http_compartido_y_confirma_ok(monkeypatch):
    monkeypatch.setenv(ejecutor.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(ejecutor.TELEGRAM_CHAT_ID_ENV, "-100999")
    fake = _FakePostClient(respuesta=_FakePostResponse(200, {"ok": True, "result": {"message_id": 1}}))
    original = http_client._client
    http_client._client = fake
    try:
        resultado = _correr_async(ejecutor._enviar_telegram("hola"))
    finally:
        http_client._client = original

    assert resultado is True
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert "123456:token-de-prueba" in url  # va en el PATH, como la API real de Telegram
    assert kwargs["data"]["chat_id"] == "-100999"
    assert kwargs["data"]["text"] == "hola"


def test_enviar_telegram_devuelve_false_si_telegram_no_confirma(monkeypatch):
    monkeypatch.setenv(ejecutor.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(ejecutor.TELEGRAM_CHAT_ID_ENV, "-100999")
    fake = _FakePostClient(respuesta=_FakePostResponse(200, {"ok": False, "description": "chat not found"}))
    original = http_client._client
    http_client._client = fake
    try:
        resultado = _correr_async(ejecutor._enviar_telegram("hola"))
    finally:
        http_client._client = original

    assert resultado is False


def test_enviar_telegram_devuelve_false_si_la_red_falla(monkeypatch):
    monkeypatch.setenv(ejecutor.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(ejecutor.TELEGRAM_CHAT_ID_ENV, "-100999")
    fake = _FakePostClient(excepcion=ConnectionError("refused"))
    original = http_client._client
    http_client._client = fake
    try:
        resultado = _correr_async(ejecutor._enviar_telegram("hola"))
    finally:
        http_client._client = original

    assert resultado is False


def test_enviar_telegram_devuelve_false_sin_variables_de_entorno(monkeypatch):
    monkeypatch.delenv(ejecutor.TELEGRAM_TOKEN_ENV, raising=False)
    monkeypatch.delenv(ejecutor.TELEGRAM_CHAT_ID_ENV, raising=False)
    fake = _FakePostClient(respuesta=_FakePostResponse(200, {"ok": True}))
    original = http_client._client
    http_client._client = fake
    try:
        resultado = _correr_async(ejecutor._enviar_telegram("hola"))
    finally:
        http_client._client = original

    assert resultado is False
    assert fake.calls == []  # nunca debería intentar la llamada HTTP


def test_enviar_telegram_nunca_loguea_el_token_del_bot(monkeypatch, caplog):
    """El token viaja en el PATH de la URL -- str(excepción) de httpx lo
    incluiría tal cual si no se redactara con el secreto EXPLÍCITO."""
    token = "123456:token-secreto-de-verdad"
    monkeypatch.setenv(ejecutor.TELEGRAM_TOKEN_ENV, token)
    monkeypatch.setenv(ejecutor.TELEGRAM_CHAT_ID_ENV, "-100999")
    fake = _FakePostClient(excepcion=RuntimeError(
        f"fallo contra https://api.telegram.org/bot{token}/sendMessage"))
    original = http_client._client
    http_client._client = fake
    caplog.set_level("WARNING")
    try:
        resultado = _correr_async(ejecutor._enviar_telegram("hola"))
    finally:
        http_client._client = original

    assert resultado is False
    assert token not in caplog.text


# --------------------------------------------------------------------------
# A-1: dedupe -- sólo se marca avisado un envío CONFIRMADO
# --------------------------------------------------------------------------

def test_debe_avisar_primera_vez_es_true():
    assert ejecutor._debe_avisar(None, "firma-x", ahora=1000.0) is True


def test_debe_avisar_misma_firma_dentro_de_la_ventana_es_false():
    entrada = {"firma": "firma-x", "notificado_en": 1000.0}
    assert ejecutor._debe_avisar(entrada, "firma-x", ahora=1000.0 + 60) is False


def test_debe_avisar_firma_distinta_es_true_aunque_sea_reciente():
    entrada = {"firma": "firma-x", "notificado_en": 1000.0}
    assert ejecutor._debe_avisar(entrada, "firma-y", ahora=1000.0 + 60) is True


def test_debe_avisar_misma_firma_pasada_la_ventana_es_true():
    entrada = {"firma": "firma-x", "notificado_en": 1000.0}
    ahora = 1000.0 + ejecutor.VENTANA_REAVISO_SEGUNDOS + 1
    assert ejecutor._debe_avisar(entrada, "firma-x", ahora=ahora) is True


def _mock_envio(monkeypatch, resultados):
    """`resultados` es una lista de bool -- cada llamada a `_enviar_telegram`
    consume el siguiente valor, en orden."""
    llamadas = []
    cola = list(resultados)

    async def _fake(mensaje):
        llamadas.append(mensaje)
        return cola.pop(0) if cola else False
    monkeypatch.setattr(ejecutor, "_enviar_telegram", _fake)
    return llamadas


def test_avisar_no_marca_como_avisado_un_envio_que_fallo(monkeypatch, tmp_path):
    """A-1, el hallazgo central: si Telegram no confirma, el estado NO se
    actualiza -- la corrida siguiente con el MISMO problema reintenta, en
    vez de darlo por avisado."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [False, False])

    resultado = _resultado_con_problemas()
    _correr_async(ejecutor._avisar(resultado))
    _correr_async(ejecutor._avisar(resultado))  # mismo problema, el envío anterior falló

    assert len(llamadas) == 2  # reintentó las dos veces
    # Nada se confirmó nunca: el archivo de estado puede directamente no
    # existir todavía (nada cambió para persistir) -- `_cargar_estado`
    # devuelve {} en ese caso, igual que si existiera sin la clave.
    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert "problemas" not in estado


def test_avisar_marca_como_avisado_solo_cuando_el_envio_confirma(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True])

    resultado = _resultado_con_problemas()
    _correr_async(ejecutor._avisar(resultado))
    _correr_async(ejecutor._avisar(resultado))  # mismo problema, ya confirmado -> no reavisa

    assert len(llamadas) == 1


def test_avisar_repite_cuando_el_problema_cambia(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True, True])

    _correr_async(ejecutor._avisar(_resultado_con_problemas(providers_saltados=["anthropic"])))
    _correr_async(ejecutor._avisar(_resultado_con_problemas(providers_saltados=["ollama"])))

    assert len(llamadas) == 2


def test_avisar_reavisa_pasada_la_ventana_aunque_el_problema_sea_el_mismo(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True, True])

    ahora = [1_000_000.0]
    monkeypatch.setattr(ejecutor.time, "time", lambda: ahora[0])

    resultado = _resultado_con_problemas()
    _correr_async(ejecutor._avisar(resultado))
    ahora[0] += ejecutor.VENTANA_REAVISO_SEGUNDOS + 1
    _correr_async(ejecutor._avisar(resultado))

    assert len(llamadas) == 2


def test_avisar_sin_problemas_no_manda_nada_y_limpia_el_estado(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True, True])

    _correr_async(ejecutor._avisar(_resultado_con_problemas()))
    assert len(llamadas) == 1

    _correr_async(ejecutor._avisar(_resultado_ok()))
    assert len(llamadas) == 1  # nada nuevo: ok=True no es un problema

    # el MISMO problema de antes vuelve a aparecer -> se vuelve a avisar,
    # porque el estado de "ya avisado" se limpió cuando el catálogo sanó.
    _correr_async(ejecutor._avisar(_resultado_con_problemas()))
    assert len(llamadas) == 2


# --------------------------------------------------------------------------
# A-1: "nuevos" como acumulador persistente (no firma+ventana)
# --------------------------------------------------------------------------

def test_avisar_nuevos_persiste_como_pendiente_si_el_envio_falla(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    _mock_envio(monkeypatch, [False])

    resultado = _resultado_ok()
    resultado["nuevos"] = {"anthropic": ["claude-opus-5-nuevo"]}
    _correr_async(ejecutor._avisar(resultado))

    estado = json.loads(ejecutor._ruta_estado().read_text())
    assert estado["nuevos_pendientes"] == {"anthropic": ["claude-opus-5-nuevo"]}


def test_avisar_nuevos_reintenta_aunque_sync_all_ya_no_lo_reporte(monkeypatch, tmp_path):
    """El caso central de A-1: `sync_provider_models` ya insertó el modelo
    en `model` en la corrida anterior, así que la corrida SIGUIENTE ya NO lo
    va a traer en `resultado["nuevos"]` -- pero como el aviso de antes
    falló, tiene que seguir intentando avisarlo."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [False, False])

    primero = _resultado_ok()
    primero["nuevos"] = {"anthropic": ["claude-opus-5-nuevo"]}
    _correr_async(ejecutor._avisar(primero))

    segundo = _resultado_ok()
    segundo["nuevos"] = {}  # ya no aparece como nuevo: sync_all() ya lo vio antes
    _correr_async(ejecutor._avisar(segundo))

    assert len(llamadas) == 2
    assert "claude-opus-5-nuevo" in llamadas[1]  # el segundo intento lo sigue mencionando
    estado = json.loads(ejecutor._ruta_estado().read_text())
    assert estado["nuevos_pendientes"] == {"anthropic": ["claude-opus-5-nuevo"]}


def test_avisar_nuevos_se_limpian_cuando_el_envio_confirma(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    _mock_envio(monkeypatch, [False, True])

    resultado = _resultado_ok()
    resultado["nuevos"] = {"anthropic": ["claude-opus-5-nuevo"]}
    _correr_async(ejecutor._avisar(resultado))  # falla, queda pendiente

    otro = _resultado_ok()
    _correr_async(ejecutor._avisar(otro))  # ahora sale bien

    estado = json.loads(ejecutor._ruta_estado().read_text())
    assert "nuevos_pendientes" not in estado


def test_avisar_nuevos_acumula_lo_pendiente_con_lo_de_otra_corrida(monkeypatch, tmp_path):
    """Dos modelos nuevos aparecidos en corridas DISTINTAS, con el envío
    fallando las dos veces, tienen que terminar juntos en el mismo
    pendiente -- ninguno se pisa al otro."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [False, False])

    primero = _resultado_ok()
    primero["nuevos"] = {"anthropic": ["modelo-1"]}
    _correr_async(ejecutor._avisar(primero))

    segundo = _resultado_ok()
    segundo["nuevos"] = {"anthropic": ["modelo-2"]}
    _correr_async(ejecutor._avisar(segundo))

    estado = json.loads(ejecutor._ruta_estado().read_text())
    assert estado["nuevos_pendientes"] == {"anthropic": ["modelo-1", "modelo-2"]}
    assert "modelo-1" in llamadas[1] and "modelo-2" in llamadas[1]


def test_avisar_nuevos_no_repite_si_ya_se_avisaron_y_no_hay_nada_pendiente(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True, True])

    resultado = _resultado_ok()
    resultado["nuevos"] = {"anthropic": ["claude-opus-5-nuevo"]}
    _correr_async(ejecutor._avisar(resultado))
    assert len(llamadas) == 1

    # misma corrida (mismo `nuevos`) otra vez -- en la práctica `sync_all()`
    # ya no lo reportaría, pero incluso si lo hiciera, ya fue avisado y
    # confirmado: no hay nada pendiente que reintentar.
    otro = _resultado_ok()
    _correr_async(ejecutor._avisar(otro))
    assert len(llamadas) == 1


def test_avisar_persiste_el_estado_como_json_legible(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    _mock_envio(monkeypatch, [True])

    _correr_async(ejecutor._avisar(_resultado_con_problemas()))

    ruta = ejecutor._ruta_estado()
    assert ruta.is_file()
    estado = json.loads(ruta.read_text(encoding="utf-8"))
    assert "problemas" in estado
    assert "firma" in estado["problemas"]
    assert "notificado_en" in estado["problemas"]
