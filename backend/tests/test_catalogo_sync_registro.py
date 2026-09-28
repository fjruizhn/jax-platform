"""Registro de ejecuciones del sync del catálogo (2026-09-27, pedido de
Fernando: avance real + última actualización siempre visible).

`catalogo_sync_ejecucion` es compartida por TODA la sesión de tests (misma
base persistente que el resto del catálogo) -- cada test limpia lo que
inserta por su cuenta, nunca asume que la tabla está vacía."""
import asyncio
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
            terminado = "UTC_TIMESTAMP()" if estado != "corriendo" else "NULL"
            await cur.execute(
                f"INSERT INTO catalogo_sync_ejecucion "
                f"(origen, estado, pasos_total, iniciado_en, terminado_en) "
                f"VALUES (%s, %s, %s, UTC_TIMESTAMP() - INTERVAL %s SECOND, {terminado})",
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
#
# MAJOR-A (tercera ronda de la auditoría adversarial, 2026-09-27): el
# criterio ya NO es un latido propio (columna retirada del esquema) -- es
# el candado de trabajo REAL de model_catalog.sync_all() (`IS_FREE_LOCK`)
# más un margen de gracia desde `iniciado_en`. Los tests de acá abajo
# sostienen ese candado de verdad (GET_LOCK/RELEASE_LOCK en una conexión
# propia, server-wide -- MariaDB lo ve igual desde cualquier otra conexión)
# en vez de simular un timestamp de latido.
# --------------------------------------------------------------------------

async def _marcar_huerfanas():
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await registro.marcar_huerfanas_interrumpidas(cur)
        await conn.commit()


async def _con_candado_de_trabajo_sostenido(coro_dentro):
    """Toma el MISMO candado que `model_catalog.sync_all()` sostiene durante
    todo un sync real, en una conexión propia, corre `coro_dentro()` mientras
    lo sostiene, y lo suelta al final pase lo que pase adentro -- así un
    `marcar_huerfanas_interrumpidas()` corrido desde OTRA conexión (server-wide,
    no por conexión) ve el candado ocupado, tal como vería el candado real de
    una corrida viva."""
    from model_catalog import _NOMBRE_CANDADO_SYNC
    pool = await _pool()
    async with pool.acquire() as conn_candado:
        async with conn_candado.cursor() as cur_candado:
            await cur_candado.execute("SELECT GET_LOCK(%s, 5)", (_NOMBRE_CANDADO_SYNC,))
            (obtenido,) = await cur_candado.fetchone()
            assert obtenido == 1, "no se pudo tomar el candado de trabajo para la prueba"
            try:
                return await coro_dentro()
            finally:
                await cur_candado.execute("SELECT RELEASE_LOCK(%s)", (_NOMBRE_CANDADO_SYNC,))


def test_marcar_huerfanas_no_toca_una_fila_con_el_candado_de_trabajo_sostenido(
        client, limpiar_catalogo_sync_ejecucion, monkeypatch):
    """Escenario 1 de MAJOR-A: un paso de sync más lento que el margen de
    gracia (acá, el margen se baja a 1s para no esperar de verdad los 60s de
    producción) NO puede marcar "caída" una corrida viva -- mientras el
    candado de trabajo siga sostenido, no importa cuánto lleve `iniciado_en`
    en el pasado. Esto es EXACTAMENTE lo que reventaba con el criterio de
    latido de la ronda anterior (un paso lento sin estampar latido se veía
    igual que un proceso muerto)."""
    monkeypatch.setattr(registro, "MARGEN_GRACIA_SEGUNDOS", 1)

    async def _dentro():
        eid = await _insertar_ejecucion("programado", "corriendo", iniciado_hace_segundos=5)
        await asyncio.sleep(1.2)  # supera el margen (1s) -- la fila "parece" vieja
        await _marcar_huerfanas()  # "GET /sync/estado" en paralelo mientras el candado sigue tomado
        return eid

    eid = client.portal.call(functools.partial(_con_candado_de_trabajo_sostenido, _dentro))
    limpiar_catalogo_sync_ejecucion.append(eid)

    _origen, estado, *_resto = client.portal.call(_fila, eid)
    assert estado == "corriendo"


def test_marcar_huerfanas_interrumpe_una_fila_con_el_candado_libre_y_vencida(
        client, limpiar_catalogo_sync_ejecucion, monkeypatch):
    """Escenario 2 de MAJOR-A: candado libre (nadie lo sostiene -- el proceso
    que lo tenía murió) Y ya pasó el margen desde `iniciado_en` -> huérfana de
    verdad, se marca 'error'."""
    monkeypatch.setattr(registro, "MARGEN_GRACIA_SEGUNDOS", 1)
    eid = client.portal.call(_insertar_ejecucion, "programado", "corriendo", 5)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(_marcar_huerfanas)

    _origen, estado, _paso, _pt, _detalle, terminado_en, resultado = client.portal.call(_fila, eid)
    assert estado == "error"
    assert terminado_en is not None
    assert resultado is not None
    assert "interrumpida" in json.loads(resultado)["error"]


def test_marcar_huerfanas_no_toca_una_fila_recien_reservada_dentro_del_margen(
        client, limpiar_catalogo_sync_ejecucion):
    """Escenario 3 de MAJOR-A: fila recién reservada (candado de trabajo
    TODAVÍA libre -- `sync_all()` no lo tomó todavía, es la ventana real
    entre `reservar_ejecucion()` y que `ejecutar_reservada()` llegue a llamar
    a `sync_all()`) pero `iniciado_en` sigue DENTRO del margen de gracia (acá,
    el margen de producción de 60s de sobra) -- no se toca."""
    eid = client.portal.call(_insertar_ejecucion, "programado", "corriendo", 0)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(_marcar_huerfanas)

    _origen, estado, *_resto = client.portal.call(_fila, eid)
    assert estado == "corriendo"


def test_marcar_huerfanas_resuelve_igual_una_fila_sin_ninguna_nocion_de_latido(
        client, limpiar_catalogo_sync_ejecucion, monkeypatch):
    """Escenario 4 de MAJOR-A: una fila 'corriendo' vieja, creada sin ningún
    dato de latido (la columna ya no existe en el esquema -- `_insertar_ejecucion`
    de este archivo nunca la escribió), se resuelve con el MISMO criterio que
    cualquier otra: candado libre + vencida -> interrumpida, sin ningún caso
    especial para "filas de antes de este cambio". No hay forma de distinguir
    una fila "legacy" de una nueva porque ya no hay ninguna columna que las
    diferencie -- eso es la prueba en sí misma."""
    monkeypatch.setattr(registro, "MARGEN_GRACIA_SEGUNDOS", 1)
    eid = client.portal.call(
        _insertar_ejecucion, "programado", "corriendo", registro.RETENCION_DIAS * 86400)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(_marcar_huerfanas)

    _origen, estado, *_resto = client.portal.call(_fila, eid)
    assert estado == "error"


def test_marcar_huerfanas_no_toca_filas_ya_terminadas(client, limpiar_catalogo_sync_ejecucion):
    eid = client.portal.call(
        _insertar_ejecucion, "manual", "ok", (registro.MARGEN_GRACIA_SEGUNDOS + 60))
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

    origen, estado, paso_actual, pasos_total, _detalle, _term, _res = client.portal.call(
        _fila, ejecucion_id)
    assert origen == "manual"
    assert estado == "corriendo"
    assert paso_actual == 0
    assert pasos_total == 9


def test_reservar_ejecucion_devuelve_sync_en_curso_si_ya_hay_una_fila_corriendo(client, limpiar_catalogo_sync_ejecucion):
    eid_existente = client.portal.call(_insertar_ejecucion, "programado", "corriendo", 0, 5)
    limpiar_catalogo_sync_ejecucion.append(eid_existente)

    ejecucion_id, en_curso = client.portal.call(_reservar, "manual", 1, 9)

    assert ejecucion_id is None
    assert en_curso is not None
    assert en_curso["code"] == "sync_en_curso"
    assert en_curso["ok"] is False


def test_reservar_ejecucion_limpia_huerfanas_antes_de_decidir(client, limpiar_catalogo_sync_ejecucion, monkeypatch):
    """Una fila 'corriendo' huérfana (candado de trabajo libre y vencida) no
    puede bloquear una reserva nueva para siempre -- reservar_ejecucion la
    interrumpe ella misma antes de mirar si hay algo corriendo."""
    monkeypatch.setattr(registro, "MARGEN_GRACIA_SEGUNDOS", 1)
    eid_huerfana = client.portal.call(
        _insertar_ejecucion, "programado", "corriendo", 5)
    limpiar_catalogo_sync_ejecucion.append(eid_huerfana)

    ejecucion_id, en_curso = client.portal.call(_reservar, "manual", 1, 9)

    assert en_curso is None
    assert ejecucion_id is not None
    limpiar_catalogo_sync_ejecucion.append(ejecucion_id)

    _origen, estado_huerfana, *_resto = client.portal.call(_fila, eid_huerfana)
    assert estado_huerfana == "error"


def test_reservar_ejecucion_get_lock_null_levanta_runtime_error(client, monkeypatch):
    """MINOR-5: GET_LOCK devolviendo NULL es un error real de MariaDB, no
    'ocupado' -- reservar_ejecucion no puede confundir los dos."""
    class _CursorFalso:
        async def execute(self, *_a, **_k):
            return None

        async def fetchone(self):
            return (None,)  # GET_LOCK -> NULL

    with pytest.raises(RuntimeError, match="GET_LOCK"):
        client.portal.call(functools.partial(
            registro.reservar_ejecucion, _CursorFalso(), object(),
            origen="manual", iniciado_por=None, pasos_total=9))


def test_dos_reservas_concurrentes_reales_solo_una_queda_corriendo(client, limpiar_catalogo_sync_ejecucion):
    """MINOR-8: sin fakes -- dos `reservar_ejecucion()` REALES, contra la
    base real, disparadas al mismo tiempo (`asyncio.gather`). El candado de
    gate (`GET_LOCK`) tiene que serializarlas: una gana la fila 'corriendo',
    la otra ve 'sync_en_curso' -- nunca las dos con una fila propia, nunca
    dos filas 'corriendo' a la vez."""
    async def _dos_reservas():
        pool = await _pool()
        async with pool.acquire() as conn_a, pool.acquire() as conn_b:
            async with conn_a.cursor() as cur_a, conn_b.cursor() as cur_b:
                return await asyncio.gather(
                    registro.reservar_ejecucion(cur_a, conn_a, origen="manual", iniciado_por=None, pasos_total=9),
                    registro.reservar_ejecucion(cur_b, conn_b, origen="programado", iniciado_por=None, pasos_total=9),
                )

    (id_a, en_curso_a), (id_b, en_curso_b) = client.portal.call(_dos_reservas)

    ganadores = [eid for eid in (id_a, id_b) if eid is not None]
    perdedores = [ec for ec in (en_curso_a, en_curso_b) if ec is not None]
    assert len(ganadores) == 1, (id_a, id_b)
    assert len(perdedores) == 1
    assert perdedores[0]["code"] == "sync_en_curso"
    limpiar_catalogo_sync_ejecucion.append(ganadores[0])

    async def _contar_corriendo():
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM catalogo_sync_ejecucion WHERE id=%s AND estado='corriendo'",
                                  (ganadores[0],))
                (n,) = await cur.fetchone()
                return n
    assert client.portal.call(_contar_corriendo) == 1


# --------------------------------------------------------------------------
# actualizar_progreso / finalizar_ejecucion
# --------------------------------------------------------------------------

def test_actualizar_progreso_escribe_paso_actual_y_detalle(client, limpiar_catalogo_sync_ejecucion):
    eid = client.portal.call(_insertar_ejecucion, "manual", "corriendo", 0, 9)
    limpiar_catalogo_sync_ejecucion.append(eid)

    client.portal.call(registro.actualizar_progreso, eid, 3, 9, "gemini")

    _origen, estado, paso_actual, pasos_total, detalle_paso, _term, _res = client.portal.call(
        _fila, eid)
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

    fila = client.portal.call(_fila, eid)
    resultado = fila[6]
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


def test_ultima_actualizacion_exitosa_ignora_con_problemas_y_error_mas_recientes(client, limpiar_catalogo_sync_ejecucion):
    """MINOR-8: el 'ok' es el MÁS VIEJO de los tres a propósito -- si alguien
    quitara el `WHERE estado='ok'` de la consulta, este test tendría que
    fallar (devolvería el 'error', más nuevo, no el 'ok'). MINOR-1 (tercera
    ronda): se compara contra `iniciado_en`, no `terminado_en` -- la función
    ahora selecciona `iniciado_en`.

    Offsets de 0/1/2 SEGUNDOS, no 20/10/1 (como en la ronda anterior): esta
    tabla es compartida por TODA la sesión de tests (ver el docstring del
    módulo), y `ultima_actualizacion_exitosa()` mira el 'ok' MÁS RECIENTE de
    TODA la tabla, no sólo de este test. Con un offset de 20s, cualquier otro
    archivo que insertara una fila 'ok' en esos 20 segundos (muy probable en
    una corrida de ~2500 tests) le ganaba a `eid_ok` y este test fallaba por
    una carrera real, no por un defecto -- medido: `test_catalogo_modelos_ejecutor.py`
    corre antes (alfabético) e inserta filas 'ok' propias. Con offsets de
    0-2s la ventana de colisión se reduce a, como mucho, un empate exacto al
    segundo con OTRO test insertando en el mismísimo instante -- igual de
    posible que antes de este cambio, pero mucho menos probable."""
    eid_error = client.portal.call(_insertar_ejecucion, "manual", "error", 0)
    limpiar_catalogo_sync_ejecucion.append(eid_error)
    eid_problemas = client.portal.call(_insertar_ejecucion, "manual", "con_problemas", 1)
    limpiar_catalogo_sync_ejecucion.append(eid_problemas)
    eid_ok = client.portal.call(_insertar_ejecucion, "manual", "ok", 2)
    limpiar_catalogo_sync_ejecucion.append(eid_ok)

    ultima = client.portal.call(_ultima_exitosa)
    assert ultima is not None

    async def _iniciado_en(eid):
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT iniciado_en FROM catalogo_sync_ejecucion WHERE id=%s", (eid,))
                (valor,) = await cur.fetchone()
                return valor

    iniciado_en_ok = client.portal.call(_iniciado_en, eid_ok)
    assert ultima == iniciado_en_ok


def test_ultima_actualizacion_exitosa_es_none_sin_ninguna_ok(client):
    """No borra nada -- sólo verifica que la función no explota cuando la
    consulta no encuentra 'ok' entre las corridas de otros tests de la
    sesión: si HAY alguna 'ok' vieja, esto sólo confirma que el tipo de
    retorno es correcto (datetime o None), no que sea None literal."""
    ultima = client.portal.call(_ultima_exitosa)
    assert ultima is None or hasattr(ultima, "year")


def test_ultima_actualizacion_exitosa_ignora_una_fila_ok_con_iniciado_en_en_el_futuro(
        client, limpiar_catalogo_sync_ejecucion, caplog):
    """MINOR-7 (tercera ronda de la auditoría adversarial, 2026-09-27): una
    fila 'ok' con `iniciado_en` en el futuro (reloj/zona horaria adelantados)
    no puede ser la "última actualización exitosa" -- se ignora, y se avisa
    por log en vez de fallar en silencio.

    `eid_pasado` con offset 0 (no 30s, ver el comentario de
    test_ultima_actualizacion_exitosa_ignora_con_problemas_y_error_mas_recientes
    sobre por qué un offset chico reduce la ventana de colisión con otros
    tests de la misma sesión de DB)."""
    async def _insertar_en_el_futuro():
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO catalogo_sync_ejecucion "
                    "(origen, estado, pasos_total, iniciado_en, terminado_en) "
                    "VALUES ('manual', 'ok', 5, UTC_TIMESTAMP() + INTERVAL 1 DAY, UTC_TIMESTAMP() + INTERVAL 1 DAY)"
                )
                return cur.lastrowid

    eid_futuro = client.portal.call(_insertar_en_el_futuro)
    limpiar_catalogo_sync_ejecucion.append(eid_futuro)
    eid_pasado = client.portal.call(_insertar_ejecucion, "manual", "ok", 0)
    limpiar_catalogo_sync_ejecucion.append(eid_pasado)

    import logging
    with caplog.at_level(logging.WARNING, logger="catalogo_sync_registro"):
        ultima = client.portal.call(_ultima_exitosa)

    async def _iniciado_en(eid):
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT iniciado_en FROM catalogo_sync_ejecucion WHERE id=%s", (eid,))
                (valor,) = await cur.fetchone()
                return valor

    assert ultima == client.portal.call(_iniciado_en, eid_pasado)
    assert any("futuro" in m for m in caplog.messages)


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
    eid = client.portal.call(_insertar_ejecucion, "manual", "corriendo", 0, 5)
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


def test_limpiar_ejecuciones_viejas_conserva_como_mucho_retencion_filas(client):
    """MINOR-8: RETENCION_FILAS (200) es un tope de CANTIDAD, no sólo de
    edad -- se siembran RETENCION_FILAS + 5 filas 'ok' recientes (todas
    dentro de RETENCION_DIAS, así que el criterio de edad no las tocaría) y
    se verifica que sólo sobreviven las RETENCION_FILAS más nuevas."""
    ids = []
    try:
        for i in range(registro.RETENCION_FILAS + 5):
            # Segundos crecientes hacia atrás: el primero insertado es el
            # más viejo, el último el más nuevo -- misma correlación id/edad
            # que en producción (AUTO_INCREMENT en orden de inserción).
            eid = client.portal.call(
                _insertar_ejecucion, "manual", "ok", (registro.RETENCION_FILAS + 5 - i))
            ids.append(eid)

        client.portal.call(_limpiar_viejas)

        sobrevivientes = [eid for eid in ids if client.portal.call(_fila, eid) is not None]
        assert len(sobrevivientes) == registro.RETENCION_FILAS
        # Los que sobreviven son los ÚLTIMOS insertados (los más nuevos), no
        # un subconjunto arbitrario.
        assert sobrevivientes == ids[-registro.RETENCION_FILAS:]
    finally:
        for eid in ids:
            client.portal.call(_borrar_ejecucion, eid)


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

    eid_existente = client.portal.call(_insertar_ejecucion, "programado", "corriendo", 0, 5)
    limpiar_catalogo_sync_ejecucion.append(eid_existente)

    resultado = client.portal.call(functools.partial(registro.correr_sync_registrado, origen="manual"))

    assert resultado["code"] == "sync_en_curso"
    assert resultado["ejecucion_id"] is None
    assert llamadas == []
