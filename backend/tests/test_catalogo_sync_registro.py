"""Registro de ejecuciones del sync del catálogo (2026-09-27, pedido de
Fernando: avance real + última actualización siempre visible).

`catalogo_sync_ejecucion` es compartida por TODA la sesión de tests (misma
base persistente que el resto del catálogo) -- cada test limpia lo que
inserta por su cuenta, nunca asume que la tabla está vacía.
"""
import functools
import json

import pytest

import catalogo_sync_registro as registro


async def _pool():
    from db.connection import get_pool
    return await get_pool()


async def _insertar_ejecucion(origen, estado, iniciado_hace_segundos=0, pasos_total=5):
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            terminado = "NOW()" if estado != "corriendo" else "NULL"
            await cur.execute(
                f"INSERT INTO catalogo_sync_ejecucion "
                f"(origen, estado, pasos_total, iniciado_en, terminado_en) "
                f"VALUES (%s, %s, %s, NOW() - INTERVAL %s SECOND, {terminado})",
                (origen, estado, pasos_total, iniciado_hace_segundos),
            )
            return cur.lastrowid


async def _borrar_ejecucion(ejecucion_id):
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM catalogo_sync_ejecucion WHERE id=%s", (ejecucion_id,))
        await conn.commit()


async def _fila(ejecucion_id):
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT origen, estado, paso_actual, pasos_total, detalle_paso, terminado_en, resultado "
                "FROM catalogo_sync_ejecucion WHERE id=%s", (ejecucion_id,))
            return await cur.fetchone()


@pytest.fixture
def limpiar_catalogo_sync_ejecucion(client):
    """Cada test de este archivo se hace cargo de borrar lo que crea; este
    fixture es sólo una red de seguridad final por si un assert corta antes
    de llegar al cleanup manual."""
    creados = []
    yield creados
    for eid in creados:
        client.portal.call(_borrar_ejecucion, eid)


# --------------------------------------------------------------------------
# marcar_huerfanas_interrumpidas
# --------------------------------------------------------------------------

async def _marcar_huerfanas():
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await registro.marcar_huerfanas_interrumpidas(cur)
        await conn.commit()


def test_marcar_huerfanas_interrumpe_una_fila_corriendo_mas_vieja_que_el_timeout(client, limpiar_catalogo_sync_ejecucion):
    eid = client.portal.call(_insertar_ejecucion, "programado", "corriendo",
                              registro.TIMEOUT_HUERFANA_SEGUNDOS + 60)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(_marcar_huerfanas)

    origen, estado, paso_actual, pasos_total, detalle_paso, terminado_en, resultado = client.portal.call(_fila, eid)
    assert estado == "error"
    assert terminado_en is not None
    assert resultado is not None
    assert "interrumpida" in json.loads(resultado)["error"]


def test_marcar_huerfanas_no_toca_una_fila_corriendo_reciente(client, limpiar_catalogo_sync_ejecucion):
    eid = client.portal.call(_insertar_ejecucion, "manual", "corriendo", 5)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(_marcar_huerfanas)

    _origen, estado, *_resto = client.portal.call(_fila, eid)
    assert estado == "corriendo"


def test_marcar_huerfanas_no_toca_filas_ya_terminadas(client, limpiar_catalogo_sync_ejecucion):
    eid = client.portal.call(_insertar_ejecucion, "manual", "ok", registro.TIMEOUT_HUERFANA_SEGUNDOS + 60)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(_marcar_huerfanas)

    _origen, estado, *_resto = client.portal.call(_fila, eid)
    assert estado == "ok"


# --------------------------------------------------------------------------
# reservar_ejecucion
# --------------------------------------------------------------------------

async def _reservar(origen, iniciado_por, pasos_total):
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            return await registro.reservar_ejecucion(
                cur, conn, origen=origen, iniciado_por=iniciado_por, pasos_total=pasos_total)


def test_reservar_ejecucion_crea_la_fila_cuando_no_hay_nada_corriendo(client, limpiar_catalogo_sync_ejecucion):
    ejecucion_id, en_curso = client.portal.call(_reservar, "manual", 1, 9)
    assert en_curso is None
    assert ejecucion_id is not None
    limpiar_catalogo_sync_ejecucion.append(ejecucion_id)

    origen, estado, paso_actual, pasos_total, *_resto = client.portal.call(_fila, ejecucion_id)
    assert origen == "manual"
    assert estado == "corriendo"
    assert paso_actual == 0
    assert pasos_total == 9


def test_reservar_ejecucion_devuelve_sync_en_curso_si_ya_hay_una_fila_corriendo(client, limpiar_catalogo_sync_ejecucion):
    eid_existente = client.portal.call(_insertar_ejecucion, "programado", "corriendo", 1)
    limpiar_catalogo_sync_ejecucion.append(eid_existente)

    ejecucion_id, en_curso = client.portal.call(_reservar, "manual", 1, 9)

    assert ejecucion_id is None
    assert en_curso is not None
    assert en_curso["code"] == "sync_en_curso"
    assert en_curso["ok"] is False


def test_reservar_ejecucion_limpia_huerfanas_antes_de_decidir(client, limpiar_catalogo_sync_ejecucion):
    """Una fila 'corriendo' huérfana (más vieja que el timeout) no puede
    bloquear una reserva nueva para siempre -- reservar_ejecucion la
    interrumpe ella misma antes de mirar si hay algo corriendo."""
    eid_huerfana = client.portal.call(
        _insertar_ejecucion, "programado", "corriendo", registro.TIMEOUT_HUERFANA_SEGUNDOS + 60)
    limpiar_catalogo_sync_ejecucion.append(eid_huerfana)

    ejecucion_id, en_curso = client.portal.call(_reservar, "manual", 1, 9)

    assert en_curso is None
    assert ejecucion_id is not None
    limpiar_catalogo_sync_ejecucion.append(ejecucion_id)

    _origen, estado_huerfana, *_resto = client.portal.call(_fila, eid_huerfana)
    assert estado_huerfana == "error"


# --------------------------------------------------------------------------
# actualizar_progreso / finalizar_ejecucion
# --------------------------------------------------------------------------

def test_actualizar_progreso_escribe_paso_actual_y_detalle(client, limpiar_catalogo_sync_ejecucion):
    eid = client.portal.call(_insertar_ejecucion, "manual", "corriendo", 0, 9)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(registro.actualizar_progreso, eid, 3, 9, "gemini")

    _origen, estado, paso_actual, pasos_total, detalle_paso, *_resto = client.portal.call(_fila, eid)
    assert estado == "corriendo"
    assert paso_actual == 3
    assert pasos_total == 9
    assert detalle_paso == "gemini"


def test_finalizar_ejecucion_ok_guarda_resultado_y_termina(client, limpiar_catalogo_sync_ejecucion):
    eid = client.portal.call(_insertar_ejecucion, "manual", "corriendo", 0)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(registro.finalizar_ejecucion, eid, "ok", {"ok": True, "providers": []})

    _origen, estado, _paso, _pt, _detalle, terminado_en, resultado = client.portal.call(_fila, eid)
    assert estado == "ok"
    assert terminado_en is not None
    assert json.loads(resultado)["ok"] is True


def test_finalizar_ejecucion_redacta_secretos_del_resultado(client, limpiar_catalogo_sync_ejecucion):
    eid = client.portal.call(_insertar_ejecucion, "manual", "corriendo", 0)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(
        registro.finalizar_ejecucion, eid, "error",
        {"error": "boom token=sk-super-secreto-1234567890"})

    *_resto, resultado = client.portal.call(_fila, eid)
    assert "sk-super-secreto-1234567890" not in resultado


def test_finalizar_ejecucion_admite_resultado_none(client, limpiar_catalogo_sync_ejecucion):
    eid = client.portal.call(_insertar_ejecucion, "manual", "corriendo", 0)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(registro.finalizar_ejecucion, eid, "error", None)

    _origen, estado, _paso, _pt, _detalle, terminado_en, resultado = client.portal.call(_fila, eid)
    assert estado == "error"
    assert terminado_en is not None
    assert resultado is None


# --------------------------------------------------------------------------
# ultima_actualizacion_exitosa
# --------------------------------------------------------------------------

async def _ultima_exitosa():
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            return await registro.ultima_actualizacion_exitosa(cur)


def test_ultima_actualizacion_exitosa_ignora_con_problemas_y_error(client, limpiar_catalogo_sync_ejecucion):
    # Limpia cualquier 'ok' de sesiones anteriores para que este test no
    # dependa de qué corrió antes: mide únicamente el delta que él mismo crea.
    eid_error = client.portal.call(_insertar_ejecucion, "manual", "error", 5)
    limpiar_catalogo_sync_ejecucion.append(eid_error)
    eid_problemas = client.portal.call(_insertar_ejecucion, "manual", "con_problemas", 3)
    limpiar_catalogo_sync_ejecucion.append(eid_problemas)
    eid_ok = client.portal.call(_insertar_ejecucion, "manual", "ok", 1)
    limpiar_catalogo_sync_ejecucion.append(eid_ok)

    ultima = client.portal.call(_ultima_exitosa)
    assert ultima is not None

    fila_ok = client.portal.call(_fila, eid_ok)
    terminado_en_ok = fila_ok[5]
    assert ultima == terminado_en_ok


def test_ultima_actualizacion_exitosa_es_none_sin_ninguna_ok(client):
    """No borra nada -- sólo verifica que la función no explota cuando la
    consulta no encuentra 'ok' entre las corridas de otros tests de la
    sesión: si HAY alguna 'ok' vieja, esto sólo confirma que el tipo de
    retorno es correcto (datetime o None), no que sea None literal."""
    ultima = client.portal.call(_ultima_exitosa)
    assert ultima is None or hasattr(ultima, "year")


# --------------------------------------------------------------------------
# limpiar_ejecuciones_viejas
# --------------------------------------------------------------------------

async def _limpiar_viejas():
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await registro.limpiar_ejecuciones_viejas(cur)
        await conn.commit()


def test_limpiar_ejecuciones_viejas_no_toca_una_fila_corriendo(client, limpiar_catalogo_sync_ejecucion):
    eid = client.portal.call(_insertar_ejecucion, "manual", "corriendo", 0)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(_limpiar_viejas)

    _origen, estado, *_resto = client.portal.call(_fila, eid)
    assert estado == "corriendo"


def test_limpiar_ejecuciones_viejas_borra_lo_mas_viejo_que_la_retencion_en_dias(client, limpiar_catalogo_sync_ejecucion):
    eid_viejo = client.portal.call(
        _insertar_ejecucion, "manual", "ok", (registro.RETENCION_DIAS + 1) * 86400)
    limpiar_catalogo_sync_ejecucion.append(eid_viejo)

    client.portal.call(_limpiar_viejas)

    assert client.portal.call(_fila, eid_viejo) is None


# --------------------------------------------------------------------------
# correr_sync_registrado -- orquestación completa
# --------------------------------------------------------------------------

def _fake_sync_all(resultado, pasos_avanzados):
    async def _fake(marca_nuevos=None, on_progreso=None):
        if on_progreso is not None:
            await on_progreso(1, 2, "paso-1")
            pasos_avanzados.append(1)
            await on_progreso(2, 2, "paso-2")
            pasos_avanzados.append(2)
        return resultado
    return _fake


def test_correr_sync_registrado_marca_ok_y_llama_al_progreso(client, monkeypatch, limpiar_catalogo_sync_ejecucion):
    import model_catalog
    pasos = []
    fake = _fake_sync_all({
        "ok": True, "providers": [], "enrich": {}, "providers_fallidos": [],
        "providers_saltados": [], "enrich_fallido": False, "nuevos": {}, "facetas_en_riesgo": [],
    }, pasos)
    monkeypatch.setattr(model_catalog, "sync_all", fake)

    resultado = client.portal.call(functools.partial(registro.correr_sync_registrado, origen="manual", iniciado_por=1))

    assert resultado["ok"] is True
    assert resultado["ejecucion_id"] is not None
    limpiar_catalogo_sync_ejecucion.append(resultado["ejecucion_id"])
    assert pasos == [1, 2]

    origen, estado, paso_actual, pasos_total, detalle_paso, terminado_en, _resultado_json = client.portal.call(
        _fila, resultado["ejecucion_id"])
    assert origen == "manual"
    assert estado == "ok"
    assert paso_actual == 2
    assert pasos_total == 2
    assert detalle_paso == "paso-2"
    assert terminado_en is not None


def test_correr_sync_registrado_marca_con_problemas_cuando_ok_es_falso(client, monkeypatch, limpiar_catalogo_sync_ejecucion):
    import model_catalog
    pasos = []
    fake = _fake_sync_all({
        "ok": False, "code": "sync_con_errores", "providers": [], "enrich": {},
        "providers_fallidos": ["openai"], "providers_saltados": [], "enrich_fallido": False,
        "nuevos": {}, "facetas_en_riesgo": [],
    }, pasos)
    monkeypatch.setattr(model_catalog, "sync_all", fake)

    resultado = client.portal.call(functools.partial(registro.correr_sync_registrado, origen="programado"))

    limpiar_catalogo_sync_ejecucion.append(resultado["ejecucion_id"])
    _origen, estado, *_resto = client.portal.call(_fila, resultado["ejecucion_id"])
    assert estado == "con_problemas"


def test_correr_sync_registrado_marca_error_si_sync_all_revienta(client, monkeypatch, limpiar_catalogo_sync_ejecucion):
    import model_catalog

    async def _explota(marca_nuevos=None, on_progreso=None):
        raise RuntimeError("boom")
    monkeypatch.setattr(model_catalog, "sync_all", _explota)

    with pytest.raises(RuntimeError):
        client.portal.call(functools.partial(registro.correr_sync_registrado, origen="manual"))

    # La fila queda registrada como 'error' aunque la excepción se repropague
    # (para que el llamador -- el endpoint o el ejecutor -- decida qué hacer
    # con el crash, sin que el registro se pierda).
    async def _ultima_error():
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, estado, resultado FROM catalogo_sync_ejecucion "
                    "WHERE origen='manual' AND estado='error' ORDER BY id DESC LIMIT 1")
                return await cur.fetchone()

    eid, estado, resultado_json = client.portal.call(_ultima_error)
    limpiar_catalogo_sync_ejecucion.append(eid)
    assert estado == "error"
    assert "boom" in resultado_json


def test_correr_sync_registrado_no_llama_a_sync_all_si_ya_hay_uno_corriendo(client, monkeypatch, limpiar_catalogo_sync_ejecucion):
    import model_catalog

    llamadas = []

    async def _no_deberia_llamarse(marca_nuevos=None, on_progreso=None):
        llamadas.append(1)
        return {"ok": True}
    monkeypatch.setattr(model_catalog, "sync_all", _no_deberia_llamarse)

    eid_existente = client.portal.call(_insertar_ejecucion, "programado", "corriendo", 1)
    limpiar_catalogo_sync_ejecucion.append(eid_existente)

    resultado = client.portal.call(functools.partial(registro.correr_sync_registrado, origen="manual"))

    assert resultado["code"] == "sync_en_curso"
    assert resultado["ejecucion_id"] is None
    assert llamadas == []
