"""Ejecutor programado del catálogo de modelos (2026-09-27, revisado tras la
auditoría adversarial del commit d549335 -- A-1/A-2/A-5).

`catalogo_modelos_ejecutor.py` corre `model_catalog.sync_all()` fuera del
click de un superadmin. La mayoría de estos tests son PUROS -- no piden
`client` ni tocan la base real: el propio `sync_all()` ya está cubierto por
test_model_catalog_sync_all.py y test_model_catalog_facetas_en_riesgo.py. Lo
que se prueba acá es la capa de arriba: código de salida, resumen, el envío
propio de Telegram, y el dedupe (que ahora exige un envío CONFIRMADO antes
de marcar algo como avisado). La excepción, al final del archivo (tercera
auditoría adversarial, 2026-09-27): la consulta real de "nuevos desde la
marca" (`_nuevos_desde_marca`/`_correr`) SÍ toca la base -- es la única forma
honesta de probar una consulta SQL."""
import asyncio
import json
import uuid
from pathlib import Path

import pytest

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


@pytest.fixture(autouse=True)
def _sin_cerrar_el_cliente_http_real(monkeypatch):
    """MINOR-8 (segunda auditoría adversarial, 2026-09-27): `_ciclo()` cierra
    el cliente HTTP compartido (`http_client.close_http_client()`) al final
    de CADA `main()` -- real, si nadie lo mockea. Hallazgo real: sin este
    autouse, `ejecutor.main()` en un test de código de salida (que no le
    interesa nada de HTTP) cerraba de VERDAD el `httpx.AsyncClient` global
    que otro test (ajeno a este archivo, p. ej.
    test_dashboard_http_pooling.py) esperaba encontrar ya creado -- rompía
    ESE test según el orden de ejecución de la suite completa, sin que
    ningún test de ESTE archivo fallara nunca por sí solo. Ningún test de
    código de salida de este archivo necesita que el cierre sea real; los
    dos que sí lo verifican (`test_main_cierra_el_cliente_http_al_final_*`)
    ponen su PROPIO mock, que pisa este default sin problema."""
    async def _no_cerrar_nada():
        return None
    monkeypatch.setattr(ejecutor, "close_http_client", _no_cerrar_nada)


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


# --------------------------------------------------------------------------
# Punto 5 (tercera auditoría adversarial, 2026-09-27): el candado contra
# syncs concurrentes -- `code == "sync_en_curso"` no es un problema, no se
# avisa y sale 0.
# --------------------------------------------------------------------------

def _resultado_sync_en_curso():
    return {
        "ok": False, "code": "sync_en_curso", "providers": [], "enrich": {},
        "providers_fallidos": [], "providers_saltados": [], "enrich_fallido": False,
        "nuevos": {}, "facetas_en_riesgo": [],
    }


def test_main_sale_0_si_el_candado_esta_ocupado(monkeypatch):
    async def _correr():
        return _resultado_sync_en_curso()
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch)

    assert ejecutor.main() == 0


def test_ciclo_no_avisa_nada_si_el_candado_esta_ocupado(monkeypatch):
    async def _correr():
        return _resultado_sync_en_curso()
    monkeypatch.setattr(ejecutor, "_correr", _correr)

    llamado = []

    async def _avisar(resultado):
        llamado.append(resultado)
    monkeypatch.setattr(ejecutor, "_avisar", _avisar)

    ejecutor.main()
    assert llamado == []


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
# Tercera auditoría adversarial (2026-09-27): "nuevos" ya NO es un acumulador
# de pendientes en el archivo de estado -- la fuente de verdad es la BASE
# (`model.created_at` comparado contra una MARCA guardada en el archivo de
# estado). `_correr()` es quien calcula, con una consulta real a la base,
# los tres campos que `_avisar()` recibe ya resueltos en `resultado`:
# `marca_corte`, `marca_previa_era_none` y `nuevos_desde_marca` -- ver
# `_nuevos_desde_marca`/`_correr` en el módulo, y
# test_model_catalog_ejecutor_nuevos_desde_marca.py para la consulta real
# contra la base. Acá se prueba SÓLO la decisión de `_avisar()` sobre esos
# tres campos ya calculados, sin tocar la base -- mismo criterio "puro" que
# el resto de este archivo.
# --------------------------------------------------------------------------

def test_avisar_primera_corrida_fija_la_marca_sin_avisar(monkeypatch, tmp_path):
    """Sin marca previa no hay nada contra qué comparar -- se fija la marca
    en `marca_corte` SIN mandar nada a Telegram (no hay que avisar
    retroactivamente de todo lo que ya estaba en la base)."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 10:00:00.000000"
    resultado["marca_previa_era_none"] = True
    resultado["nuevos_desde_marca"] = {}
    _correr_async(ejecutor._avisar(resultado))

    assert llamadas == []
    estado = json.loads(ejecutor._ruta_estado().read_text())
    assert estado["nuevos_marca"] == "2026-09-27 10:00:00.000000"


def test_avisar_nuevos_avanza_la_marca_solo_si_telegram_confirma(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 11:00:00.000000"
    resultado["marca_previa_era_none"] = False
    resultado["nuevos_desde_marca"] = {"anthropic": ["claude-opus-5-nuevo"]}
    _correr_async(ejecutor._avisar(resultado))

    assert len(llamadas) == 1
    assert "claude-opus-5-nuevo" in llamadas[0]
    estado = json.loads(ejecutor._ruta_estado().read_text())
    assert estado["nuevos_marca"] == "2026-09-27 11:00:00.000000"


def test_avisar_nuevos_no_avanza_la_marca_si_el_envio_falla(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [False])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 12:00:00.000000"
    resultado["marca_previa_era_none"] = False
    resultado["nuevos_desde_marca"] = {"anthropic": ["claude-opus-5-nuevo"]}
    _correr_async(ejecutor._avisar(resultado))

    assert len(llamadas) == 1  # lo intentó
    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert "nuevos_marca" not in estado  # no confirmó -- no avanza


def test_avisar_sin_nuevos_no_avanza_la_marca_ni_avisa(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 13:00:00.000000"
    resultado["marca_previa_era_none"] = False
    resultado["nuevos_desde_marca"] = {}
    _correr_async(ejecutor._avisar(resultado))

    assert llamadas == []
    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert "nuevos_marca" not in estado


def test_avisar_nuevos_reintenta_con_la_misma_marca_tras_un_fallo(monkeypatch, tmp_path):
    """Si el envío falla, la marca se queda igual -- la corrida siguiente
    (que en la práctica recalcularía `nuevos_desde_marca` desde la MISMA
    marca vieja, ver `_correr`) reintenta con lo mismo en cuanto se le
    vuelva a pasar."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [False, True])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 14:00:00.000000"
    resultado["marca_previa_era_none"] = False
    resultado["nuevos_desde_marca"] = {"anthropic": ["claude-opus-5-nuevo"]}
    _correr_async(ejecutor._avisar(resultado))  # falla

    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert "nuevos_marca" not in estado

    _correr_async(ejecutor._avisar(resultado))  # reintento, mismo contenido -- ahora confirma

    assert len(llamadas) == 2
    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert estado["nuevos_marca"] == "2026-09-27 14:00:00.000000"


def test_avisar_nuevos_si_el_envio_revienta_la_marca_no_avanza(monkeypatch, tmp_path):
    """Un crash a mitad del envío (SIGKILL visto desde afuera como una
    excepción sin control) no pierde ni ensucia nada: como la marca sólo
    avanza al CONFIRMAR, sigue apuntando a la última confirmada y la
    corrida siguiente recalcula desde ahí -- ya no hace falta un archivo de
    "pendientes" separado (tercera auditoría adversarial, 2026-09-27)."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))

    async def _enviar_que_revienta(mensaje):
        raise RuntimeError("el proceso murió a mitad del POST")
    monkeypatch.setattr(ejecutor, "_enviar_telegram", _enviar_que_revienta)

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 16:00:00.000000"
    resultado["marca_previa_era_none"] = False
    resultado["nuevos_desde_marca"] = {"anthropic": ["claude-opus-5-nuevo"]}

    with pytest.raises(RuntimeError):
        _correr_async(ejecutor._avisar(resultado))

    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert "nuevos_marca" not in estado


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


def test_enviar_telegram_devuelve_false_si_el_cuerpo_no_es_un_objeto(monkeypatch):
    """MAJOR-4: `resp.json()` puede parsear bien y no ser un dict (una
    lista, un número) -- `.get('ok')` reventaría sin este chequeo."""
    monkeypatch.setenv(ejecutor.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(ejecutor.TELEGRAM_CHAT_ID_ENV, "-100999")
    fake = _FakePostClient(respuesta=_FakePostResponse(200, [1, 2, 3]))
    original = http_client._client
    http_client._client = fake
    try:
        resultado = _correr_async(ejecutor._enviar_telegram("hola"))
    finally:
        http_client._client = original

    assert resultado is False


# --------------------------------------------------------------------------
# MINOR-7 (segunda auditoría adversarial, 2026-09-27): estado JSON válido
# pero que no es un objeto -- se trata como {}.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("contenido", ["[1, 2, 3]", '"solo un string"', "42", "true", "null"])
def test_cargar_estado_json_valido_pero_no_dict_es_vacio(tmp_path, contenido):
    ruta = tmp_path / "estado.json"
    ruta.write_text(contenido, encoding="utf-8")
    assert ejecutor._cargar_estado(ruta) == {}


# --------------------------------------------------------------------------
# MAJOR-2 (segunda auditoría adversarial, 2026-09-27): si `model_catalog`
# (o algo que él importa) revienta al cargarse, este módulo tiene que poder
# cargarse igual -- si no, `main()` nunca llega a correr y no hay quien
# avise. Se verifica por AST: el import real (no la mención en un
# comentario/docstring) no puede estar a nivel de módulo.
# --------------------------------------------------------------------------

def test_model_catalog_y_db_connection_no_se_importan_a_nivel_de_modulo():
    import ast

    fuente = Path(ejecutor.__file__).read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    for nodo in arbol.body:  # SOLO nivel de módulo -- ast.walk también entraría a las funciones
        if isinstance(nodo, ast.Import):
            nombres = [n.name for n in nodo.names]
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            nombres = [nodo.module]
        else:
            continue
        assert "model_catalog" not in nombres, "model_catalog se importa a nivel de módulo"
        assert not any(n == "db.connection" or n.startswith("db.connection.") for n in nombres), \
            "db.connection se importa a nivel de módulo"


# --------------------------------------------------------------------------
# MINOR-8 (segunda auditoría adversarial, 2026-09-27): un solo asyncio.run
# -- sync + aviso + cierre del cliente HTTP en el mismo loop.
# --------------------------------------------------------------------------

def test_main_cierra_el_cliente_http_al_final_del_ciclo_sano(monkeypatch):
    llamadas = []

    async def _correr():
        return _resultado_ok()

    async def _avisar(resultado):
        pass

    async def _cerrar():
        llamadas.append("cerrado")
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    monkeypatch.setattr(ejecutor, "_avisar", _avisar)
    monkeypatch.setattr(ejecutor, "close_http_client", _cerrar)

    assert ejecutor.main() == 0
    assert llamadas == ["cerrado"]


def test_main_cierra_el_cliente_http_al_final_incluso_si_correr_revienta(monkeypatch):
    llamadas = []

    async def _correr_roto():
        raise RuntimeError("boom")

    async def _enviar(mensaje):
        return True

    async def _cerrar():
        llamadas.append("cerrado")
    monkeypatch.setattr(ejecutor, "_correr", _correr_roto)
    monkeypatch.setattr(ejecutor, "_enviar_telegram", _enviar)
    monkeypatch.setattr(ejecutor, "close_http_client", _cerrar)

    assert ejecutor.main() == 1
    assert llamadas == ["cerrado"]


# --------------------------------------------------------------------------
# Punto 2 (tercera auditoría adversarial, 2026-09-27): `_nuevos_desde_marca`
# es la consulta real contra `model.created_at` -- estos SÍ tocan la base
# (ver el docstring del módulo).
# --------------------------------------------------------------------------

async def _sembrar_modelo_con_created_at(provider_id, model_id, created_at):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO model (provider_id, model_id, status, source, source_checked_at, created_at) "
                "VALUES (%s, %s, 'available', 'manual', NOW(), %s)",
                (provider_id, model_id, created_at),
            )
        await conn.commit()


async def _borrar_modelo(provider_id, model_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM model WHERE provider_id=%s AND model_id=%s", (provider_id, model_id))
        await conn.commit()


async def _consultar_nuevos_desde_marca(marca, corte):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            return await ejecutor._nuevos_desde_marca(cur, marca, corte)


def test_nuevos_desde_marca_sin_marca_previa_devuelve_vacio(client):
    """Primera corrida (sin marca guardada todavía): no hay nada contra qué
    comparar -- `_correr()`/`_avisar()` fijan la marca sin avisar
    retroactivamente, así que acá la consulta ni se ejecuta."""
    resultado = client.portal.call(_consultar_nuevos_desde_marca, None, "2026-09-27 23:59:59")
    assert resultado == {}


def test_nuevos_desde_marca_incluye_solo_lo_creado_en_la_ventana(client):
    # `model.provider_id` es FK contra `provider` -- se usa un provider_id
    # REAL ya sembrado (zhipu), con model_id sintético propio (uuid) para no
    # pisar filas de otros tests en la base de sesión compartida.
    provider_id = "zhipu"
    sufijo = uuid.uuid4().hex[:8]
    model_id_antes = f"test-marca-antes-{sufijo}"
    model_id_dentro = f"test-marca-dentro-{sufijo}"
    model_id_despues = f"test-marca-despues-{sufijo}"
    client.portal.call(_sembrar_modelo_con_created_at, provider_id, model_id_antes, "2026-09-27 09:00:00")
    client.portal.call(_sembrar_modelo_con_created_at, provider_id, model_id_dentro, "2026-09-27 10:30:00")
    client.portal.call(_sembrar_modelo_con_created_at, provider_id, model_id_despues, "2026-09-27 12:00:00")
    try:
        resultado = client.portal.call(
            _consultar_nuevos_desde_marca, "2026-09-27 10:00:00", "2026-09-27 11:00:00")
        assert resultado == {provider_id: [model_id_dentro]}
    finally:
        for model_id in (model_id_antes, model_id_dentro, model_id_despues):
            client.portal.call(_borrar_modelo, provider_id, model_id)


def test_nuevos_desde_marca_agrupa_por_proveedor(client):
    provider_a, provider_b = "zhipu", "moonshot"
    sufijo = uuid.uuid4().hex[:8]
    model_a = f"test-marca-a-{sufijo}"
    model_b = f"test-marca-b-{sufijo}"
    client.portal.call(_sembrar_modelo_con_created_at, provider_a, model_a, "2026-09-27 10:30:00")
    client.portal.call(_sembrar_modelo_con_created_at, provider_b, model_b, "2026-09-27 10:31:00")
    try:
        resultado = client.portal.call(
            _consultar_nuevos_desde_marca, "2026-09-27 10:00:00", "2026-09-27 11:00:00")
        assert resultado == {provider_a: [model_a], provider_b: [model_b]}
    finally:
        client.portal.call(_borrar_modelo, provider_a, model_a)
        client.portal.call(_borrar_modelo, provider_b, model_b)


def test_correr_primera_vez_fija_marca_previa_era_none(client, monkeypatch, tmp_path):
    """Extremo a extremo de `_correr()` (sin archivo de estado todavía):
    `sync_all()` se mockea para no pegarle a proveedores reales, pero la
    consulta de `_nuevos_desde_marca` SÍ es la real.

    `close_pool` se neutraliza a propósito: el fixture `client` es de
    ALCANCE DE SESIÓN (`tests/conftest.py`) y comparte el pool de ESTE loop
    con TODO el resto de la suite -- cerrarlo de verdad acá tumbó 34 tests
    de otros archivos la primera vez que se corrió esta ronda (chat,
    adjuntos, facet wiring, shadow validation), todos ajenos al catálogo de
    modelos. Mismo criterio que `_sin_cerrar_el_cliente_http_real` (arriba,
    para `close_http_client`) aplicado al pool de DB."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))

    import model_catalog
    import db.connection as db_connection

    async def _fake_sync_all():
        return {
            "ok": True, "providers": [], "enrich": {}, "providers_fallidos": [],
            "providers_saltados": [], "enrich_fallido": False, "nuevos": {},
            "facetas_en_riesgo": [],
        }
    monkeypatch.setattr(model_catalog, "sync_all", _fake_sync_all)

    async def _no_cerrar_el_pool_compartido():
        pass
    monkeypatch.setattr(db_connection, "close_pool", _no_cerrar_el_pool_compartido)

    resultado = client.portal.call(ejecutor._correr)

    assert resultado["marca_previa_era_none"] is True
    assert resultado["nuevos_desde_marca"] == {}
    assert isinstance(resultado["marca_corte"], str) and resultado["marca_corte"]
