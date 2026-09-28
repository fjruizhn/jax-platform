"""Ejecutor programado del catálogo de modelos -- revisado tras varias
rondas de auditoría adversarial, la última el 2026-09-28.

`catalogo_modelos_ejecutor.py` corre `model_catalog.sync_all()` fuera del
click de un superadmin. La mayoría de estos tests son PUROS -- no piden
`client` ni tocan la base real: el propio `sync_all()` (incluida la consulta
real de "nuevos desde la marca", que desde la cuarta auditoría adversarial
vive DENTRO de `model_catalog.sync_all()`, no acá) ya está cubierto por
test_model_catalog_sync_all.py y test_model_catalog_facetas_en_riesgo.py. Lo
que se prueba acá es la capa de arriba: código de salida (0/1/3, MAJOR-1),
el candado ocupado (MAJOR-2(c)), el resumen, el envío propio de Telegram, y
el dedupe (que exige un envío CONFIRMADO antes de marcar algo como
avisado). La excepción, al final del archivo: un puñado de tests SÍ tocan la
base -- `_correr()` calculando la marca de arranque cuando no hay ninguna
guardada todavía."""
import asyncio
import json
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

def _sin_aviso(monkeypatch, estado_aviso="sin_problemas"):
    """Aísla main() del envío real de avisos -- eso lo prueban los tests de
    dedupe y de _enviar_telegram, más abajo. `_avisar()` devuelve un estado
    (MAJOR-1, cuarta auditoría adversarial, 2026-09-28) que main() usa para
    decidir entre el código de salida 3 y 1 -- por default acá se simula
    "sin_problemas" (no importa para el código de salida, sólo importa
    cuando `ok=False`)."""
    async def _nada(resultado):
        return estado_aviso
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
    _sin_aviso(monkeypatch, estado_aviso="fallo")

    assert ejecutor.main() != 0


# --------------------------------------------------------------------------
# MAJOR-1 (cuarta auditoría adversarial, 2026-09-28): el código de salida es
# 3 SOLO cuando hubo problemas Y el aviso quedó resuelto (avisado o
# deduplicado con razón) -- 1 en cualquier otro desenlace.
# --------------------------------------------------------------------------

def test_main_sale_3_cuando_hay_problemas_y_el_aviso_se_confirmo(monkeypatch):
    async def _correr():
        return _resultado_con_problemas()
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch, estado_aviso="avisado")

    assert ejecutor.main() == 3


def test_main_sale_3_cuando_hay_problemas_y_ya_estaban_deduplicados(monkeypatch):
    async def _correr():
        return _resultado_con_problemas()
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch, estado_aviso="dedupeado")

    assert ejecutor.main() == 3


def test_main_sale_1_cuando_hay_problemas_y_el_aviso_no_se_pudo_confirmar(monkeypatch):
    async def _correr():
        return _resultado_con_problemas()
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch, estado_aviso="fallo")

    assert ejecutor.main() == 1


def test_main_sale_1_si_avisar_revento_sin_dejar_estado_de_aviso(monkeypatch):
    """Si `_avisar()` revienta, `_ciclo()` lo atrapa (fail-soft) y deja
    `_estado_aviso_problemas="fallo"` (punto B, quinta auditoría
    adversarial, 2026-09-28) -- main() no puede asumir "avisado" cuando en
    realidad ni siquiera se pudo confirmar el intento, tiene que salir 1."""
    async def _correr():
        return _resultado_con_problemas()
    monkeypatch.setattr(ejecutor, "_correr", _correr)

    async def _avisar_que_revienta(resultado):
        raise RuntimeError("boom")
    monkeypatch.setattr(ejecutor, "_avisar", _avisar_que_revienta)

    assert ejecutor.main() == 1


def test_main_sale_1_si_avisar_revienta_aunque_ok_sea_verdadero(monkeypatch):
    """Punto B (quinta auditoría adversarial, 2026-09-28): un catálogo sano
    (`ok=True`) cuyo `_avisar()` revienta (p. ej. `_guardar_estado` sin
    permisos, disco lleno) NO puede salir 0 -- eso escondería que ni
    siquiera se pudo confiar en dejar el estado de dedupe/marca al día."""
    async def _correr():
        return _resultado_ok()
    monkeypatch.setattr(ejecutor, "_correr", _correr)

    async def _avisar_que_revienta(resultado):
        raise OSError("disco lleno")
    monkeypatch.setattr(ejecutor, "_avisar", _avisar_que_revienta)

    assert ejecutor.main() == 1


# --------------------------------------------------------------------------
# MAJOR-2(c) (cuarta auditoría adversarial, 2026-09-28): el candado contra
# syncs concurrentes -- `code == "sync_en_curso"` UNA vez no es un problema
# (sale 0, sin aviso); a partir de la SEGUNDA corrida consecutiva, avisa y
# sale 1.
# --------------------------------------------------------------------------

def _resultado_sync_en_curso():
    return {
        "ok": False, "code": "sync_en_curso", "providers": [], "enrich": {},
        "providers_fallidos": [], "providers_saltados": [], "enrich_fallido": False,
        "nuevos": {}, "facetas_en_riesgo": [],
    }


def test_main_sale_0_si_el_candado_esta_ocupado_por_primera_vez(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))

    async def _correr():
        return _resultado_sync_en_curso()
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch)

    llamado = []
    async def _enviar_no_deberia_llamarse(mensaje):
        llamado.append(mensaje)
        return True
    monkeypatch.setattr(ejecutor, "_enviar_telegram", _enviar_no_deberia_llamarse)

    assert ejecutor.main() == 0
    assert llamado == []


def test_main_sale_1_y_avisa_desde_la_segunda_corrida_seguida_con_candado_ocupado(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))

    async def _correr():
        return _resultado_sync_en_curso()
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch)

    llamadas = []
    async def _enviar(mensaje):
        llamadas.append(mensaje)
        return True
    monkeypatch.setattr(ejecutor, "_enviar_telegram", _enviar)

    assert ejecutor.main() == 0  # primera vez -- sin aviso todavía
    assert llamadas == []

    assert ejecutor.main() == 1  # segunda vez SEGUIDA -- avisa y sale 1
    assert len(llamadas) == 1
    assert "candado" in llamadas[0].lower()


def test_main_corta_la_racha_de_candado_ocupado_si_el_sync_corre_de_verdad(monkeypatch, tmp_path):
    """Si el candado se libera y un sync REAL corre en el medio, el contador
    de corridas consecutivas se corta -- un candado ocupado más adelante
    vuelve a contar desde 1, no arrastra la racha vieja."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))

    async def _correr_ocupado():
        return _resultado_sync_en_curso()
    async def _correr_ok():
        return _resultado_ok()

    llamadas = []
    async def _enviar(mensaje):
        llamadas.append(mensaje)
        return True
    monkeypatch.setattr(ejecutor, "_enviar_telegram", _enviar)
    _sin_aviso(monkeypatch)

    monkeypatch.setattr(ejecutor, "_correr", _correr_ocupado)
    assert ejecutor.main() == 0  # candado ocupado, 1ra vez

    monkeypatch.setattr(ejecutor, "_correr", _correr_ok)
    assert ejecutor.main() == 0  # corrió de verdad -- corta la racha

    monkeypatch.setattr(ejecutor, "_correr", _correr_ocupado)
    assert ejecutor.main() == 0  # candado ocupado otra vez, pero es la 1ra de una racha NUEVA
    assert llamadas == []  # nunca llegó a la segunda consecutiva


def test_ciclo_no_avisa_nada_de_problemas_si_el_candado_esta_ocupado(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))

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


def test_resumen_de_una_corrida_saltada_no_se_confunde_con_un_sync(monkeypatch, capsys):
    """Hallado en producción el 2026-09-27 al desplegar #167: una corrida que
    el gate de configuración saltó ("todavía no toca") imprimía la MISMA línea
    que un sync real sano (`ok=True ... nuevos={}`), y el `logger.info` que lo
    explica no se emite (el ejecutor no configura logging, a propósito: el
    INFO de httpx llevaría la URL de Telegram con el token del bot). En el
    journal, saltada y sincronizada eran indistinguibles."""
    for code in (ejecutor._CODIGO_PROGRAMADO_NO_TOCA, ejecutor._CODIGO_PROGRAMADO_APAGADO):
        async def _correr(code=code):
            return ejecutor._resultado_sin_tocar_nada(code)
        monkeypatch.setattr(ejecutor, "_correr", _correr)
        _sin_aviso(monkeypatch)

        assert ejecutor.main() == 0
        salida = capsys.readouterr().out
        assert f"code={code}" in salida
        assert "sin sincronizar" in salida


def test_resumen_de_un_sync_real_no_dice_sin_sincronizar(monkeypatch, capsys):
    async def _correr():
        return _resultado_con_problemas(providers_fallidos=["openai"])
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch)

    ejecutor.main()
    salida = capsys.readouterr().out
    assert "code=sync_con_errores" in salida
    assert "sin sincronizar" not in salida


def test_resumen_de_un_sync_sano_no_lleva_code_ni_sin_sincronizar(monkeypatch, capsys):
    """El caso con el que se confundía la corrida saltada: un sync real sano
    no trae `code`, así que su línea no puede llevar `code=` ni
    "sin sincronizar"."""
    async def _correr():
        return {
            "ok": True, "providers": [], "enrich": {}, "providers_fallidos": [],
            "providers_saltados": [], "enrich_fallido": False, "nuevos": {},
            "facetas_en_riesgo": [],
        }
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch)

    assert ejecutor.main() == 0
    salida = capsys.readouterr().out
    assert "ok=True" in salida
    assert "code=" not in salida
    assert "sin sincronizar" not in salida


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
        # copia -- `_ciclo()` agrega `_estado_aviso_problemas` al MISMO
        # dict DESPUÉS de este llamado (MAJOR-1, cuarta auditoría
        # adversarial, 2026-09-28); comparar contra una copia tomada ACÁ
        # evita que esa mutación posterior invalide la aserción de abajo.
        llamadas.append(dict(resultado))
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


def test_debe_avisar_reloj_retrocedido_es_true():
    """Punto A (quinta auditoría adversarial, 2026-09-28): si `notificado_en`
    queda DESPUÉS de `ahora` (el reloj del sistema saltó hacia atrás),
    `ahora - notificado_en` da negativo -- sin el chequeo explícito, eso es
    siempre menor que la ventana y se leería como "todavía dentro de la
    ventana, no reavisar" cuando en realidad no hay nada confiable contra
    qué comparar."""
    entrada = {"firma": "firma-x", "notificado_en": 2_000_000.0}
    assert ejecutor._debe_avisar(entrada, "firma-x", ahora=1_000_000.0) is True


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
# "nuevos" no es un acumulador de pendientes en el archivo de estado -- la
# fuente de verdad es la BASE (`model.created_at` comparado contra una
# MARCA). Desde la cuarta auditoría adversarial (2026-09-28, MINOR-2), la
# consulta real corre DENTRO de `model_catalog.sync_all()`; `_correr()` sólo
# agrega `marca_usada` (con qué arrancó esta corrida) al resultado que ya
# trae `marca_corte`/`nuevos_desde_marca`/`marca_retrocedio`. Regla UNIFORME
# en `_avisar()` (MINOR-4, sin caso especial para la primera corrida): la
# marca avanza a `marca_corte` salvo que HAYA nuevos Y el envío falle -- ahí
# se re-persiste `marca_usada` (con qué arrancó ESTA corrida), que en una
# corrida normal es un no-op y en el arranque es lo que evita perder un
# modelo que ya quedó insertado en `model`.
# --------------------------------------------------------------------------

def test_avisar_bootstrap_sin_nuevos_fija_la_marca_sin_avisar(monkeypatch, tmp_path):
    """Arranque (sin archivo de estado todavía) pero nada nuevo en la
    ventana -- se fija la marca en `marca_corte` sin mandar nada a
    Telegram."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 10:00:00"
    resultado["marca_usada"] = "2026-09-27 09:59:59"
    resultado["marca_retrocedio"] = False
    resultado["nuevos_desde_marca"] = {}
    _correr_async(ejecutor._avisar(resultado))

    assert llamadas == []
    estado = json.loads(ejecutor._ruta_estado().read_text())
    assert estado["nuevos_marca"] == "2026-09-27 10:00:00"


def test_avisar_bootstrap_con_nuevos_avisa_como_cualquier_otra_corrida(monkeypatch, tmp_path):
    """MINOR-4: lo que entra en la corrida de arranque (p. ej. un modelo
    nuevo recién sincronizado) SÍ se avisa -- no hay caso especial que lo
    calle."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 10:05:00"
    resultado["marca_usada"] = "2026-09-27 10:00:00"
    resultado["marca_retrocedio"] = False
    resultado["nuevos_desde_marca"] = {"anthropic": ["claude-opus-5-nuevo"]}
    _correr_async(ejecutor._avisar(resultado))

    assert len(llamadas) == 1
    assert "claude-opus-5-nuevo" in llamadas[0]
    estado = json.loads(ejecutor._ruta_estado().read_text())
    assert estado["nuevos_marca"] == "2026-09-27 10:05:00"


def test_avisar_nuevos_avanza_la_marca_solo_si_telegram_confirma(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 11:00:00"
    resultado["marca_usada"] = "2026-09-27 10:00:00"
    resultado["marca_retrocedio"] = False
    resultado["nuevos_desde_marca"] = {"anthropic": ["claude-opus-5-nuevo"]}
    _correr_async(ejecutor._avisar(resultado))

    assert len(llamadas) == 1
    assert "claude-opus-5-nuevo" in llamadas[0]
    estado = json.loads(ejecutor._ruta_estado().read_text())
    assert estado["nuevos_marca"] == "2026-09-27 11:00:00"


def test_avisar_nuevos_si_el_envio_falla_repersiste_la_marca_usada(monkeypatch, tmp_path):
    """Si el envío falla, la marca NO avanza a `marca_corte` -- se
    re-persiste `marca_usada` (la que ya estaba, en una corrida normal; la
    de arranque, en la primera) para que la corrida siguiente reintente
    desde el MISMO punto de partida y no pierda el modelo que ya quedó
    insertado en `model`."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [False])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 12:00:00"
    resultado["marca_usada"] = "2026-09-27 11:00:00"
    resultado["marca_retrocedio"] = False
    resultado["nuevos_desde_marca"] = {"anthropic": ["claude-opus-5-nuevo"]}
    _correr_async(ejecutor._avisar(resultado))

    assert len(llamadas) == 1  # lo intentó
    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert estado["nuevos_marca"] == "2026-09-27 11:00:00"  # se queda en el punto de partida, no avanza


def test_avisar_sin_nuevos_avanza_la_marca_sin_avisar(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 13:00:00"
    resultado["marca_usada"] = "2026-09-27 12:00:00"
    resultado["marca_retrocedio"] = False
    resultado["nuevos_desde_marca"] = {}
    _correr_async(ejecutor._avisar(resultado))

    assert llamadas == []
    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert estado["nuevos_marca"] == "2026-09-27 13:00:00"


def test_avisar_nuevos_reintenta_desde_la_marca_usada_tras_un_fallo(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [False, True])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 14:00:00"
    resultado["marca_usada"] = "2026-09-27 13:00:00"
    resultado["marca_retrocedio"] = False
    resultado["nuevos_desde_marca"] = {"anthropic": ["claude-opus-5-nuevo"]}
    _correr_async(ejecutor._avisar(resultado))  # falla

    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert estado["nuevos_marca"] == "2026-09-27 13:00:00"

    _correr_async(ejecutor._avisar(resultado))  # reintento, mismo contenido -- ahora confirma

    assert len(llamadas) == 2
    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert estado["nuevos_marca"] == "2026-09-27 14:00:00"


def test_avisar_nuevos_si_el_envio_revienta_la_marca_no_avanza(monkeypatch, tmp_path):
    """Un crash a mitad del envío (SIGKILL visto desde afuera como una
    excepción sin control) no pierde ni ensucia nada: como la marca sólo
    avanza al CONFIRMAR, sigue apuntando a la última confirmada y la
    corrida siguiente recalcula desde ahí."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))

    async def _enviar_que_revienta(mensaje):
        raise RuntimeError("el proceso murió a mitad del POST")
    monkeypatch.setattr(ejecutor, "_enviar_telegram", _enviar_que_revienta)

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2026-09-27 16:00:00"
    resultado["marca_usada"] = "2026-09-27 15:00:00"
    resultado["marca_retrocedio"] = False
    resultado["nuevos_desde_marca"] = {"anthropic": ["claude-opus-5-nuevo"]}

    with pytest.raises(RuntimeError):
        _correr_async(ejecutor._avisar(resultado))

    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert "nuevos_marca" not in estado


# --------------------------------------------------------------------------
# MINOR-3 (cuarta auditoría adversarial, 2026-09-28): `marca_retrocedio` --
# `model_catalog.sync_all()` ya detectó que `NOW()` dio antes que la marca
# guardada. `_avisar()` avisa la anomalía y re-fija la marca de todos
# modos.
# --------------------------------------------------------------------------

def test_avisar_marca_retrocedida_avisa_la_anomalia_y_refija_la_marca(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = _mock_envio(monkeypatch, [True])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2020-01-01 00:00:00"
    resultado["marca_usada"] = "2026-09-27 10:00:00"
    resultado["marca_retrocedio"] = True
    resultado["nuevos_desde_marca"] = {}
    _correr_async(ejecutor._avisar(resultado))

    assert len(llamadas) == 1
    assert "reloj" in llamadas[0].lower() or "zona" in llamadas[0].lower()
    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert estado["nuevos_marca"] == "2020-01-01 00:00:00"


def test_avisar_marca_retrocedida_refija_aunque_el_aviso_de_la_anomalia_falle(monkeypatch, tmp_path):
    """El aviso de la anomalía es best-effort -- si Telegram tampoco
    confirma ESE mensaje, la marca se re-fija IGUAL (quedarse comparando
    contra una marca "del futuro" para siempre es peor)."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    _mock_envio(monkeypatch, [False])

    resultado = _resultado_ok()
    resultado["marca_corte"] = "2020-01-01 00:00:00"
    resultado["marca_usada"] = "2026-09-27 10:00:00"
    resultado["marca_retrocedio"] = True
    resultado["nuevos_desde_marca"] = {}
    _correr_async(ejecutor._avisar(resultado))

    estado = ejecutor._cargar_estado(ejecutor._ruta_estado())
    assert estado["nuevos_marca"] == "2020-01-01 00:00:00"


# --------------------------------------------------------------------------
# MINOR-5 (cuarta auditoría adversarial, 2026-09-28): truncar el mensaje de
# Telegram, con "y N más".
# --------------------------------------------------------------------------

def test_mensaje_nuevos_corto_no_se_trunca():
    mensaje = ejecutor._mensaje_nuevos({"anthropic": ["claude-opus-5-nuevo"]})
    assert mensaje == "Catálogo de modelos: modelos nuevos detectados -- anthropic: claude-opus-5-nuevo."
    assert "más" not in mensaje


def test_mensaje_nuevos_largo_se_trunca_con_y_n_mas():
    nuevos = {f"provider-{i}": [f"modelo-{i}"] for i in range(400)}  # de sobra para pasar 4000 caracteres
    mensaje = ejecutor._mensaje_nuevos(nuevos)

    assert len(mensaje) <= ejecutor.LIMITE_TELEGRAM + 50  # margen para "(y N más)."
    assert "más)." in mensaje
    assert "provider-0: modelo-0" in mensaje  # el primero siempre entra


def test_enviar_telegram_trunca_cualquier_mensaje_que_pase_el_limite(monkeypatch):
    monkeypatch.setenv(ejecutor.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(ejecutor.TELEGRAM_CHAT_ID_ENV, "-100999")
    fake = _FakePostClient(respuesta=_FakePostResponse(200, {"ok": True}))
    original = http_client._client
    http_client._client = fake
    mensaje_gigante = "x" * (ejecutor.LIMITE_TELEGRAM + 5000)
    try:
        _correr_async(ejecutor._enviar_telegram(mensaje_gigante))
    finally:
        http_client._client = original

    enviado = fake.calls[0][1]["data"]["text"]
    # Punto C (quinta auditoría adversarial, 2026-09-28): el largo EXACTO,
    # no un margen -- el sufijo tiene que quedar DENTRO del límite
    # declarado, no sumado por encima.
    assert len(enviado) == ejecutor.LIMITE_TELEGRAM
    assert enviado.endswith("… (mensaje recortado)")


def test_enviar_telegram_el_mensaje_recortado_no_pasa_el_limite_ni_un_caracter(monkeypatch):
    """Punto C: contra el largo exacto del límite -- si el recorte sumara el
    sufijo por encima (`mensaje[:LIMITE] + sufijo`), este test lo agarra en
    el peor caso posible: un mensaje apenas un caracter más largo que el
    límite."""
    monkeypatch.setenv(ejecutor.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(ejecutor.TELEGRAM_CHAT_ID_ENV, "-100999")
    fake = _FakePostClient(respuesta=_FakePostResponse(200, {"ok": True}))
    original = http_client._client
    http_client._client = fake
    mensaje = "y" * (ejecutor.LIMITE_TELEGRAM + 1)
    try:
        _correr_async(ejecutor._enviar_telegram(mensaje))
    finally:
        http_client._client = original

    enviado = fake.calls[0][1]["data"]["text"]
    assert len(enviado) == ejecutor.LIMITE_TELEGRAM


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
# Si `model_catalog`/`db.connection`/`http_client`/`redaccion` (o algo que
# ellos importen) revienta al cargarse, este módulo tiene que poder cargarse
# igual -- si no, `main()` nunca llega a correr y no hay quien avise. Se
# verifica por AST: el import real (no la mención en un comentario/
# docstring) no puede estar a nivel de módulo. MAJOR-1 (cuarta auditoría
# adversarial, 2026-09-28) sumó `http_client`/`redaccion` a esta regla --
# antes vivían arriba a propósito, pero eso significaba que un .venv roto
# tumbaba el módulo ENTERO con un traceback sin control (ver el docstring
# del módulo).
# --------------------------------------------------------------------------

def test_ningun_import_no_stdlib_esta_a_nivel_de_modulo():
    import ast

    fuente = Path(ejecutor.__file__).read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    prohibidos = ("model_catalog", "db.connection", "http_client", "redaccion")
    for nodo in arbol.body:  # SOLO nivel de módulo -- ast.walk también entraría a las funciones
        if isinstance(nodo, ast.Import):
            nombres = [n.name for n in nodo.names]
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            nombres = [nodo.module]
        else:
            continue
        for nombre in nombres:
            assert not any(nombre == p or nombre.startswith(f"{p}.") for p in prohibidos), (
                f"{nombre} se importa a nivel de módulo -- tiene que quedar dentro de una función"
            )


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
# MAJOR-1 (cuarta auditoría adversarial, 2026-09-28): `close_http_client` se
# resuelve DENTRO del try de main() -- si `http_client` (.venv roto) no se
# puede importar, main() devuelve 1 de forma controlada en vez de que el
# intérprete muera con un traceback sin que nada de este módulo decida
# nada.
# --------------------------------------------------------------------------

def test_main_resuelve_close_http_client_de_verdad_si_seguia_en_none(monkeypatch):
    """Si nada lo parcheó todavía (simulando el arranque real del proceso,
    sin la fixture autouse de este archivo), main() hace el import de
    verdad. `_ciclo()` se mockea A PROPÓSITO: la función real que
    `main()` resuelve queda apuntando al `close_http_client` VERDADERO de
    `http_client.py` -- si se dejara correr `_ciclo()` de verdad, cerraría
    el `httpx.AsyncClient` global compartido con TODA la suite (el mismo
    bug de orden de ejecución que rompió test_dashboard_http_pooling.py en
    una ronda anterior). Lo único que importa acá es que main() haya hecho
    la resolución -- no hace falta ejecutar el cierre real para probarlo."""
    monkeypatch.setattr(ejecutor, "close_http_client", None)

    async def _ciclo_que_no_cierra_nada_de_verdad():
        return _resultado_ok()
    monkeypatch.setattr(ejecutor, "_ciclo", _ciclo_que_no_cierra_nada_de_verdad)

    assert ejecutor.main() == 0
    assert ejecutor.close_http_client is not None
    assert callable(ejecutor.close_http_client)


def test_main_sale_1_si_no_se_puede_importar_http_client(monkeypatch):
    """.venv roto: `import http_client` revienta -- main() lo atrapa y
    devuelve 1 en vez de dejar propagar la excepción fuera del proceso."""
    monkeypatch.setattr(ejecutor, "close_http_client", None)

    import builtins
    import_original = builtins.__import__

    def _import_que_revienta_para_http_client(nombre, *args, **kwargs):
        if nombre == "http_client":
            raise ImportError("simulando .venv roto")
        return import_original(nombre, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", _import_que_revienta_para_http_client)

    assert ejecutor.main() == 1


# --------------------------------------------------------------------------
# `_correr()` calcula la marca de ARRANQUE con una consulta real a la base
# cuando no hay ninguna guardada todavía -- la consulta de "nuevos desde la
# marca" en sí ya está cubierta por test_model_catalog_sync_all.py, del lado
# de `model_catalog.sync_all()` (MINOR-2, cuarta auditoría adversarial,
# 2026-09-28). `close_pool` se neutraliza en los dos tests: el fixture
# `client` es de ALCANCE DE SESIÓN (`tests/conftest.py`) y comparte el pool
# de ESTE loop con TODO el resto de la suite -- cerrarlo de verdad tumbó 34
# tests de otros archivos la primera vez que se corrió esta ronda (chat,
# adjuntos, facet wiring, shadow validation), todos ajenos al catálogo de
# modelos. Mismo criterio que `_sin_cerrar_el_cliente_http_real` (arriba,
# para `close_http_client`) aplicado al pool de DB.
# --------------------------------------------------------------------------

def _fake_sync_all_marca(**esperado_marca_nuevos):
    """`model_catalog.sync_all()` real -- mockeada acá para no pegarle a
    proveedores reales -- ahora acepta `marca_nuevos`; el fake lo recibe y
    lo devuelve reflejado en la respuesta, como haría la función real, para
    poder verificar QUÉ marca le pasó `_correr()`. `on_progreso=None`
    (2026-09-27, `catalogo_sync_registro.correr_sync_registrado()` siempre
    lo pasa ahora): se acepta e ignora, estos tests son sobre la marca, no
    sobre el avance."""
    llamadas = []

    async def _fake(marca_nuevos=None, on_progreso=None, on_terminar=None):
        llamadas.append(marca_nuevos)
        resultado = {
            "ok": True, "providers": [], "enrich": {}, "providers_fallidos": [],
            "providers_saltados": [], "enrich_fallido": False, "nuevos": {},
            "facetas_en_riesgo": [], "nuevos_desde_marca": {},
            "marca_corte": "2026-09-28 00:00:00", "marca_retrocedio": False,
        }
        if on_terminar is not None:
            await on_terminar(resultado)
        return resultado
    return _fake, llamadas


def _forzar_toca(monkeypatch):
    """Estos tests son sobre el cálculo de la MARCA, no sobre el gate de
    configuración (2026-09-27) -- se fuerza `toca_correr` a verdadero para
    que sigan probando exactamente lo que probaban antes de que `_correr()`
    aprendiera a consultarlo, sin depender de qué haya en
    `catalogo_sync_ejecucion` por otros tests de la misma sesión de DB."""
    import catalogo_sync_config
    monkeypatch.setattr(catalogo_sync_config, "toca_correr", lambda *a, **k: True)


def test_correr_sin_marca_guardada_la_calcula_con_now_de_la_base_antes_del_sync(client, monkeypatch, tmp_path):
    """MINOR-4 (cuarta auditoría adversarial, 2026-09-28): arranque (sin
    archivo de estado todavía) -- `_correr()` calcula `marca_usada` con
    `NOW()` de la BASE (no el reloj de este proceso) ANTES de llamar a
    `sync_all()`, y se la pasa como `marca_nuevos`."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    _forzar_toca(monkeypatch)

    import model_catalog
    import db.connection as db_connection

    fake, llamadas = _fake_sync_all_marca()
    monkeypatch.setattr(model_catalog, "sync_all", fake)

    async def _no_cerrar_el_pool_compartido():
        pass
    monkeypatch.setattr(db_connection, "close_pool", _no_cerrar_el_pool_compartido)

    resultado = client.portal.call(ejecutor._correr)
    _borrar_ejecucion_de_registro(client, resultado)

    assert len(llamadas) == 1
    marca_pasada = llamadas[0]
    assert isinstance(marca_pasada, str) and marca_pasada  # se calculó algo, no None
    assert resultado["marca_usada"] == marca_pasada


def test_correr_con_marca_guardada_la_reusa_sin_tocar_la_base_para_calcularla(client, monkeypatch, tmp_path):
    """Con una marca YA guardada, `_correr()` no necesita la consulta extra
    de `NOW()` -- usa directo lo que hay en el archivo de estado."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    ejecutor._guardar_estado(ejecutor._ruta_estado(), {"nuevos_marca": "2026-09-27 08:00:00"})
    _forzar_toca(monkeypatch)

    import model_catalog
    import db.connection as db_connection

    fake, llamadas = _fake_sync_all_marca()
    monkeypatch.setattr(model_catalog, "sync_all", fake)

    async def _no_cerrar_el_pool_compartido():
        pass
    monkeypatch.setattr(db_connection, "close_pool", _no_cerrar_el_pool_compartido)

    resultado = client.portal.call(ejecutor._correr)
    _borrar_ejecucion_de_registro(client, resultado)

    assert llamadas == ["2026-09-27 08:00:00"]
    assert resultado["marca_usada"] == "2026-09-27 08:00:00"


def _borrar_ejecucion_de_registro(client, resultado):
    """Estos tests corren `_correr()` real contra la base compartida de la
    sesión -- `correr_sync_registrado()` (2026-09-27) deja una fila en
    `catalogo_sync_ejecucion`; se borra para no ensuciar la retención ni la
    marca de "última exitosa" que otros tests de `catalogo_sync_registro`
    puedan medir."""
    ejecucion_id = resultado.get("ejecucion_id")
    if ejecucion_id is None:
        return

    async def _borrar():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM catalogo_sync_ejecucion WHERE id=%s", (ejecucion_id,))
            await conn.commit()
    client.portal.call(_borrar)


def test_correr_no_agrega_marca_usada_si_el_candado_esta_ocupado(client, monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    _forzar_toca(monkeypatch)

    import model_catalog
    import db.connection as db_connection

    async def _fake_sync_en_curso(marca_nuevos=None, on_progreso=None, on_terminar=None):
        return {
            "ok": False, "code": "sync_en_curso", "providers": [], "enrich": {},
            "providers_fallidos": [], "providers_saltados": [], "enrich_fallido": False,
            "nuevos": {}, "facetas_en_riesgo": [],
        }
    monkeypatch.setattr(model_catalog, "sync_all", _fake_sync_en_curso)

    async def _no_cerrar_el_pool_compartido():
        pass
    monkeypatch.setattr(db_connection, "close_pool", _no_cerrar_el_pool_compartido)

    resultado = client.portal.call(ejecutor._correr)

    assert resultado["code"] == "sync_en_curso"
    assert "marca_usada" not in resultado

    # correr_sync_registrado() reservó una fila, y como sync_all() devolvió
    # 'sync_en_curso' pese a que reservar_ejecucion() no vio nada corriendo
    # (carrera inesperada simulada por este fake), la marcó 'error' -- se
    # limpia para no ensuciar la sesión compartida.
    async def _borrar_error_reciente():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM catalogo_sync_ejecucion WHERE origen='programado' AND estado='error' "
                    "AND iniciado_en >= NOW() - INTERVAL 60 SECOND")
            await conn.commit()
    client.portal.call(_borrar_error_reciente)


# --------------------------------------------------------------------------
# Gate de configuración (2026-09-27, pedido de Fernando): el timer pasa a
# OnCalendar=hourly y `_correr()` decide, leyendo `catalogo_sync_config`, si
# de verdad toca -- apagado o "todavía no toca" no llaman a NINGÚN proveedor
# ni tocan ningún estado (ver `_CODIGOS_GATE_CERRADO`).
# --------------------------------------------------------------------------

async def _fijar_config(habilitado, cada_valor, cada_unidad):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE catalogo_sync_config SET habilitado=%s, cada_valor=%s, cada_unidad=%s, "
                "actualizado_por=NULL, actualizado_en=NULL WHERE id=1",
                (habilitado, cada_valor, cada_unidad),
            )
        await conn.commit()


@pytest.fixture
def config_sync_original(client):
    """Restaura la fila única de `catalogo_sync_config` (compartida por toda
    la sesión de tests) al valor sembrado al terminar."""
    yield
    client.portal.call(_fijar_config, True, 6, "horas")


def _sin_llamadas_a_sync_all(monkeypatch):
    import model_catalog
    llamadas = []

    async def _no_deberia_llamarse(marca_nuevos=None, on_progreso=None, on_terminar=None):
        llamadas.append(1)
        return {"ok": True}
    monkeypatch.setattr(model_catalog, "sync_all", _no_deberia_llamarse)
    return llamadas


@pytest.mark.parametrize("code", ["programado_apagado", "programado_no_toca"])
def test_main_sale_0_cuando_el_gate_decide_no_correr(monkeypatch, code):
    """Mismo patrón que el resto de los tests de código de salida de este
    archivo (`_correr` mockeado, sin DB real): un resultado con un código de
    `_CODIGOS_GATE_CERRADO` sale 0, y -- a diferencia de `ok=True` a secas --
    NUNCA llama a `_avisar()` (se probaría por separado si hiciera falta,
    acá alcanza con que `main()` no dependa de que se haya llamado)."""
    async def _correr():
        return ejecutor._resultado_sin_tocar_nada(code)
    monkeypatch.setattr(ejecutor, "_correr", _correr)

    llamado = []

    async def _avisar_no_deberia_llamarse(resultado):
        llamado.append(1)
        return "sin_problemas"
    monkeypatch.setattr(ejecutor, "_avisar", _avisar_no_deberia_llamarse)

    assert ejecutor.main() == 0
    assert llamado == []


def test_correr_apagado_no_llama_a_ningun_proveedor(client, monkeypatch, tmp_path, config_sync_original):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    client.portal.call(_fijar_config, False, 6, "horas")
    llamadas = _sin_llamadas_a_sync_all(monkeypatch)

    resultado = client.portal.call(ejecutor._correr)

    assert llamadas == []
    assert resultado["ok"] is True
    assert resultado["code"] == "programado_apagado"


def test_correr_no_toca_todavia_no_llama_a_ningun_proveedor(client, monkeypatch, tmp_path, config_sync_original):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    client.portal.call(_fijar_config, True, 6, "horas")
    import catalogo_sync_config
    monkeypatch.setattr(catalogo_sync_config, "toca_correr", lambda *a, **k: False)
    llamadas = _sin_llamadas_a_sync_all(monkeypatch)

    resultado = client.portal.call(ejecutor._correr)

    assert resultado["ok"] is True
    assert resultado["code"] == "programado_no_toca"
    assert llamadas == []


def test_correr_toca_llama_a_sync_all(client, monkeypatch, tmp_path, config_sync_original):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    client.portal.call(_fijar_config, True, 6, "horas")
    _forzar_toca(monkeypatch)

    import model_catalog
    llamadas = []

    async def _fake(marca_nuevos=None, on_progreso=None, on_terminar=None):
        llamadas.append(marca_nuevos)
        resultado = {
            "ok": True, "providers": [], "enrich": {}, "providers_fallidos": [],
            "providers_saltados": [], "enrich_fallido": False, "nuevos": {},
            "facetas_en_riesgo": [], "nuevos_desde_marca": {},
            "marca_corte": "2026-09-28 00:00:00", "marca_retrocedio": False,
        }
        if on_terminar is not None:
            await on_terminar(resultado)
        return resultado
    monkeypatch.setattr(model_catalog, "sync_all", _fake)

    resultado = client.portal.call(ejecutor._correr)
    _borrar_ejecucion_de_registro(client, resultado)

    assert len(llamadas) == 1
    assert resultado.get("code") != "programado_apagado"
    assert resultado.get("code") != "programado_no_toca"


def test_correr_config_ilegible_propaga_la_excepcion(client, monkeypatch, tmp_path, config_sync_original):
    """Config ilegible (DB caída, tabla corrupta) es un FALLO real -- no se
    puede callar. `_correr()` deja propagar la excepción tal cual; es
    `_ciclo()` (probado aparte) quien la convierte en salida 1."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    import catalogo_sync_config

    async def _revienta(cur):
        raise RuntimeError("catalogo_sync_config sin fila id=1")
    monkeypatch.setattr(catalogo_sync_config, "leer_config", _revienta)

    with pytest.raises(RuntimeError):
        client.portal.call(ejecutor._correr)


def test_correr_no_toca_con_una_corrida_manual_reciente(client, monkeypatch, tmp_path, config_sync_original):
    """"Contando también las manuales" (pedido de Fernando): una corrida
    MANUAL exitosa reciente hace que el gate diga "todavía no toca", igual
    que si hubiera sido programada."""
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    client.portal.call(_fijar_config, True, 6, "horas")

    async def _insertar_ok_manual_reciente():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # UTC_TIMESTAMP(), no NOW() (MINOR-6): catalogo_sync_ejecucion
                # guarda sus fechas en UTC; la sesión de MariaDB de esta app
                # corre en CST (6h detrás) -- un NOW() acá haría ver esta
                # corrida como "de hace 6 horas" y rompería justo lo que este
                # test quiere probar ("reciente" -> no toca).
                await cur.execute(
                    "INSERT INTO catalogo_sync_ejecucion (origen, estado, pasos_total, iniciado_en, terminado_en) "
                    "VALUES ('manual', 'ok', 9, UTC_TIMESTAMP(), UTC_TIMESTAMP())")
                eid = cur.lastrowid
            await conn.commit()
        return eid
    eid = client.portal.call(_insertar_ok_manual_reciente)

    llamadas = _sin_llamadas_a_sync_all(monkeypatch)
    try:
        resultado = client.portal.call(ejecutor._correr)
        assert resultado["code"] == "programado_no_toca"
        assert llamadas == []
    finally:
        client.portal.call(_borrar_ejecucion, eid)


async def _borrar_ejecucion(ejecucion_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM catalogo_sync_ejecucion WHERE id=%s", (ejecucion_id,))
        await conn.commit()
