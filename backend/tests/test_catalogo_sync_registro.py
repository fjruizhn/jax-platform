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
    una corrida viva.

    MINOR-1 (cuarta ronda de la auditoría adversarial, 2026-09-28): el
    nombre pasa por `nombre_candado()` -- el mismo que usa la producción --
    para tomar el candado CALIFICADO con la base actual; si se tomara el
    nombre sin calificar, `IS_FREE_LOCK()` sobre el calificado lo vería
    libre igual, y esta prueba dejaría de probar lo que dice probar."""
    from model_catalog import _NOMBRE_CANDADO_SYNC, nombre_candado
    pool = await _pool()
    async with pool.acquire() as conn_candado:
        async with conn_candado.cursor() as cur_candado:
            candado = await nombre_candado(cur_candado, _NOMBRE_CANDADO_SYNC)
            await cur_candado.execute("SELECT GET_LOCK(%s, 5)", (candado,))
            (obtenido,) = await cur_candado.fetchone()
            assert obtenido == 1, "no se pudo tomar el candado de trabajo para la prueba"
            try:
                return await coro_dentro()
            finally:
                await cur_candado.execute("SELECT RELEASE_LOCK(%s)", (candado,))


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


def test_finalizar_ejecucion_devuelve_true_cuando_cierra_de_verdad(client, limpiar_catalogo_sync_ejecucion):
    """MINOR-2 (quinta ronda de la auditoría adversarial, 2026-09-28): el
    valor de retorno es lo que `ejecutar_reservada()` usa para decidir si
    hace falta su red de seguridad -- se prueba el caso normal explícito,
    no sólo el degradado de abajo."""
    eid = client.portal.call(_insertar_ejecucion, "manual", "corriendo", 0)
    limpiar_catalogo_sync_ejecucion.append(eid)

    cerro = client.portal.call(registro.finalizar_ejecucion, eid, "ok", {"ok": True})
    assert cerro is True


def test_finalizar_ejecucion_sobre_una_fila_ya_cerrada_por_fuera_no_la_pisa(
        client, limpiar_catalogo_sync_ejecucion):
    """MINOR-2: si la fila YA no está 'corriendo' (alguien más la cerró --
    p.ej. `marcar_huerfanas_interrumpidas()`, u otra llamada a
    `finalizar_ejecucion()`), esta llamada no la pisa: devuelve `False` y
    el estado/resultado que ya tenía queda intacto."""
    eid = client.portal.call(_insertar_ejecucion, "manual", "corriendo", 0)
    limpiar_catalogo_sync_ejecucion.append(eid)

    # Cerrada "por fuera" -- no a través de finalizar_ejecucion, para que la
    # sonda de abajo pruebe el `WHERE estado='corriendo'` de esta función y
    # no otra cosa.
    async def _cerrar_por_fuera():
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE catalogo_sync_ejecucion SET estado='error', terminado_en=UTC_TIMESTAMP(), "
                    "resultado=%s WHERE id=%s",
                    (json.dumps({"error": "cerrada por fuera"}), eid),
                )
            await conn.commit()

    client.portal.call(_cerrar_por_fuera)

    cerro = client.portal.call(registro.finalizar_ejecucion, eid, "ok", {"ok": True, "no": "debería verse"})
    assert cerro is False

    _origen, estado, _paso, _pt, _detalle, terminado_en, resultado = client.portal.call(_fila, eid)
    assert estado == "error"  # sigue como la dejó "afuera" -- NO "ok"
    assert json.loads(resultado)["error"] == "cerrada por fuera"


# --------------------------------------------------------------------------
# ultima_actualizacion_exitosa
# --------------------------------------------------------------------------

async def _ultima_exitosa():
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            return await registro.ultima_actualizacion_exitosa(cur)


async def _vaciar_catalogo_sync_ejecucion():
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM catalogo_sync_ejecucion")
        await conn.commit()


@pytest.fixture
def tabla_catalogo_sync_ejecucion_vacia(client):
    """MINOR-2 (cuarta ronda de la auditoría adversarial, 2026-09-28): el
    aislamiento de esta tabla entre tests era "por probabilidad" -- filas
    'ok' que dejan los POST de test_admin_models_endpoints.py (sin limpiar,
    la tabla es de TODA la sesión) podían ganarle a la fila que un test de
    ESTE archivo espera que sea "la más reciente". No es una carrera de
    verdad (pytest corre en serie, un test a la vez): es que la tabla no
    empieza vacía cuando un test asume que sí. Se vacía al INICIO -- no
    hace falta al final, cada test de este archivo ya limpia lo que crea
    (`limpiar_catalogo_sync_ejecucion`)."""
    client.portal.call(_vaciar_catalogo_sync_ejecucion)
    yield


def test_ultima_actualizacion_exitosa_ignora_con_problemas_y_error_mas_recientes(
        client, limpiar_catalogo_sync_ejecucion, tabla_catalogo_sync_ejecucion_vacia):
    """MINOR-8: el 'ok' es el MÁS VIEJO de los tres a propósito -- si alguien
    quitara el `WHERE estado='ok'` de la consulta, este test tendría que
    fallar (devolvería el 'error', más nuevo, no el 'ok'). MINOR-1 (tercera
    ronda): se compara contra `iniciado_en`, no `terminado_en` -- la función
    ahora selecciona `iniciado_en`."""
    eid_ok = client.portal.call(_insertar_ejecucion, "manual", "ok", 20)
    limpiar_catalogo_sync_ejecucion.append(eid_ok)
    eid_problemas = client.portal.call(_insertar_ejecucion, "manual", "con_problemas", 10)
    limpiar_catalogo_sync_ejecucion.append(eid_problemas)
    eid_error = client.portal.call(_insertar_ejecucion, "manual", "error", 1)
    limpiar_catalogo_sync_ejecucion.append(eid_error)

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


def test_ultima_actualizacion_exitosa_es_none_sin_ninguna_ok(client, tabla_catalogo_sync_ejecucion_vacia):
    """Con la tabla vacía de verdad (fixture MINOR-2), esto ya puede afirmar
    `None` a secas -- antes tenía que tolerar "alguna 'ok' vieja de otro
    test" y sólo confirmaba el tipo de retorno."""
    ultima = client.portal.call(_ultima_exitosa)
    assert ultima is None


def test_ultima_actualizacion_exitosa_ignora_una_fila_ok_con_iniciado_en_en_el_futuro(
        client, limpiar_catalogo_sync_ejecucion, tabla_catalogo_sync_ejecucion_vacia, caplog):
    """MINOR-7 (tercera ronda de la auditoría adversarial, 2026-09-27): una
    fila 'ok' con `iniciado_en` en el futuro (reloj/zona horaria adelantados)
    no puede ser la "última actualización exitosa" -- se ignora, y se avisa
    por log en vez de fallar en silencio."""
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
    eid_pasado = client.portal.call(_insertar_ejecucion, "manual", "ok", 30)
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
    """MAJOR-1 (cuarta ronda de la auditoría adversarial, 2026-09-28): el
    fake TIENE que aceptar y llamar `on_terminar` -- es lo que ahora cierra
    la fila (dentro del "candado", que acá no existe de verdad, pero el
    CONTRATO del callback sí se ejercita). Sin esto, `ejecutar_reservada()`
    depende de su red de seguridad (cerrada=False) para cerrar la fila --
    lo que estos tests de orquestación siguen verificando igual (miran el
    estado final de la fila), pero el camino PRIMARIO quedaría sin probar."""
    async def _fake(marca_nuevos=None, on_progreso=None, on_terminar=None):
        if on_progreso is not None:
            await on_progreso(1, 2, "paso-1")
            pasos_avanzados.append(1)
            await on_progreso(2, 2, "paso-2")
            pasos_avanzados.append(2)
        if on_terminar is not None:
            await on_terminar(resultado)
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

    async def _explota(marca_nuevos=None, on_progreso=None, on_terminar=None):
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

    async def _no_deberia_llamarse(marca_nuevos=None, on_progreso=None, on_terminar=None):
        llamadas.append(1)
        return {"ok": True}
    monkeypatch.setattr(model_catalog, "sync_all", _no_deberia_llamarse)

    eid_existente = client.portal.call(_insertar_ejecucion, "programado", "corriendo", 0, 5)
    limpiar_catalogo_sync_ejecucion.append(eid_existente)

    resultado = client.portal.call(functools.partial(registro.correr_sync_registrado, origen="manual"))

    assert resultado["code"] == "sync_en_curso"
    assert resultado["ejecucion_id"] is None
    assert llamadas == []


def test_on_terminar_que_revienta_la_red_de_seguridad_cierra_igual(
        client, monkeypatch, limpiar_catalogo_sync_ejecucion):
    """MINOR-2 (quinta ronda de la auditoría adversarial, 2026-09-28): el
    `on_terminar` que arma `ejecutar_reservada()` llama a
    `finalizar_ejecucion()` -- si ESA llamada revienta (una falla real de
    DB, por ejemplo), `sync_all()` la atrapa fail-soft (mismo criterio que
    `on_progreso`) y sigue -- `cerrada` queda en `False`. La red de
    seguridad de `ejecutar_reservada()` (el `if not cerrada:` de después)
    tiene que cerrar la fila igual, con una SEGUNDA llamada a
    `finalizar_ejecucion()` que esta vez sí funciona."""
    import model_catalog

    llamadas = []
    finalizar_real = registro.finalizar_ejecucion

    async def _finalizar_revienta_la_primera_vez(ejecucion_id, estado, resultado):
        llamadas.append((ejecucion_id, estado))
        if len(llamadas) == 1:
            raise RuntimeError("boom -- la escritura de cierre revienta la primera vez")
        return await finalizar_real(ejecucion_id, estado, resultado)

    monkeypatch.setattr(registro, "finalizar_ejecucion", _finalizar_revienta_la_primera_vez)

    async def _sync_all_con_on_terminar_fail_soft(marca_nuevos=None, on_progreso=None, on_terminar=None):
        resultado = {
            "ok": True, "providers": [], "enrich": {}, "providers_fallidos": [],
            "providers_saltados": [], "enrich_fallido": False, "nuevos": {}, "facetas_en_riesgo": [],
        }
        if on_terminar is not None:
            try:
                await on_terminar(resultado)
            except Exception:  # fail-soft: mismo criterio que el `_terminar` real de model_catalog.sync_all()
                pass
        return resultado

    monkeypatch.setattr(model_catalog, "sync_all", _sync_all_con_on_terminar_fail_soft)

    resultado = client.portal.call(functools.partial(registro.correr_sync_registrado, origen="manual"))
    limpiar_catalogo_sync_ejecucion.append(resultado["ejecucion_id"])

    assert len(llamadas) == 2, "on_terminar tiene que intentar cerrar, reventar, y la red de seguridad reintentar"
    assert resultado.get("cierre_omitido") is not True

    _origen, estado_final, *_resto = client.portal.call(_fila, resultado["ejecucion_id"])
    assert estado_final == "ok"




# --------------------------------------------------------------------------
# MAJOR-1 (cuarta ronda de la auditoria adversarial, 2026-09-28): la fila se
# cierra ANTES de soltar el candado real -- integracion de punta a punta con
# `model_catalog.sync_all()` REAL (proveedores/enriquecimiento mockeados,
# candado y orquestacion de cierre reales), no el fake de arriba.
# --------------------------------------------------------------------------

def test_ejecutar_reservada_cierra_la_fila_con_el_candado_todavia_sostenido(
        client, monkeypatch, limpiar_catalogo_sync_ejecucion):
    """El defecto real que MAJOR-1 cierra: antes, `sync_all()` soltaba el
    candado en su `finally` y RECIEN DESPUES `ejecutar_reservada()` cerraba
    la fila -- en ese hueco, un `GET /sync/estado` (que corre
    `marcar_huerfanas_interrumpidas()` en cada pedido) podia ver "candado
    libre + fila 'corriendo' + mas vieja que el margen" y marcarla 'error',
    y el cierre normal que llegaba un instante despues la pisaba con 'ok'
    SIN CONDICION, escondiendo la carrera.

    Prueba de punta a punta con el candado REAL: se engancha una sonda
    ENCIMA de `finalizar_ejecucion` (justo donde `on_terminar` cierra la
    fila, dentro de `sync_all()`, con el candado todavia tomado) que, desde
    OTRA conexion, verifica que el candado de trabajo TODAVIA esta
    sostenido (`IS_FREE_LOCK`=0) en ese instante -- y corre
    `marcar_huerfanas_interrumpidas()` ahi mismo, para probar que ni
    siquiera intentandolo justo en ese momento se puede corromper la fila
    (el candado sostenido la protege una vez; estar ya cerrada -- no
    'corriendo' -- la protege una segunda vez). `MARGEN_GRACIA_SEGUNDOS` en
    0 para maximizar la ventana de la carrera que esto cierra."""
    import model_catalog
    monkeypatch.setattr(registro, "MARGEN_GRACIA_SEGUNDOS", 0)

    async def _sync_provider_models_rapido(provider_id):
        return {"provider_id": provider_id, "fetched": 1, "nuevos": []}
    monkeypatch.setattr(model_catalog, "sync_provider_models", _sync_provider_models_rapido)

    async def _enrich_rapido():
        return {"enriched": 0}
    monkeypatch.setattr(model_catalog, "enrich_from_models_dev", _enrich_rapido)

    libres_al_cerrar = []
    finalizar_real = registro.finalizar_ejecucion

    async def _finalizar_con_sonda(ejecucion_id, estado, resultado):
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                candado = await model_catalog.nombre_candado(cur, model_catalog._NOMBRE_CANDADO_SYNC)
                await cur.execute("SELECT IS_FREE_LOCK(%s)", (candado,))
                (libre,) = await cur.fetchone()
                libres_al_cerrar.append(libre)
                # La sonda del defecto: si esto marcara la fila 'error' acá
                # (porque el candado ya estuviera libre), el cierre normal
                # que sigue la pisaría con 'ok' sin condición -- justo la
                # carrera que MAJOR-1 cierra.
                await registro.marcar_huerfanas_interrumpidas(cur)
            await conn.commit()
        return await finalizar_real(ejecucion_id, estado, resultado)

    monkeypatch.setattr(registro, "finalizar_ejecucion", _finalizar_con_sonda)

    resultado = client.portal.call(functools.partial(registro.correr_sync_registrado, origen="manual"))
    limpiar_catalogo_sync_ejecucion.append(resultado["ejecucion_id"])

    assert libres_al_cerrar == [0], (
        "el candado de trabajo debía seguir SOSTENIDO en el momento del cierre -- "
        f"IS_FREE_LOCK devolvió {libres_al_cerrar!r}"
    )
    assert resultado.get("code") != "sync_en_curso"
    assert resultado.get("cierre_omitido") is not True

    # Lo que importa: NUNCA 'error' por una huérfana espuria -- ni "ok" (todo
    # sincronizó bien) ni "con_problemas" (alguna faceta en riesgo real de
    # los datos sembrados de la sesión, ajeno a este test) son el defecto
    # que MAJOR-1 cierra; 'error' por la carrera sí lo sería.
    _origen, estado_final, *_resto = client.portal.call(_fila, resultado["ejecucion_id"])
    assert estado_final in ("ok", "con_problemas")


def test_ejecutar_reservada_cierra_error_con_el_candado_sostenido_si_sync_all_revienta(
        client, monkeypatch, limpiar_catalogo_sync_ejecucion):
    """MINOR-2 (quinta ronda de la auditoría adversarial, 2026-09-28): el
    camino de EXCEPCIÓN de `sync_all()` REAL (no un fake) -- una excepción
    DENTRO del `try`, con el candado tomado -- también tiene que cerrar la
    fila 'error' con el candado TODAVÍA sostenido, mismo criterio que el
    camino feliz de arriba.

    El ejemplo de la auditoría fue "`sync_provider_models` que lanza" --
    pero `sync_all()` envuelve CADA proveedor en su propio try/except
    (fail-soft, ver `model_catalog.py`): una excepción ahí NUNCA se
    propaga, queda en `results` como `{"error": ...}` y el sync sigue,
    `ok=False`. Para ejercitar el camino de EXCEPCIÓN real (el que
    `_terminar(..., es_error=True)` cubre) hace falta algo que `sync_all()`
    NO envuelva -- `_facetas_en_riesgo` es ese punto (ya usado con el mismo
    fin en test_model_catalog_sync_all.py::test_sync_all_libera_el_candado_incluso_si_algo_revienta_dentro_del_try),
    y es lo que se usa acá para que el test pruebe lo que la auditoría pide
    probar, no la forma exacta en que lo describió."""
    import model_catalog
    monkeypatch.setattr(registro, "MARGEN_GRACIA_SEGUNDOS", 0)

    async def _sync_provider_models_rapido(provider_id):
        return {"provider_id": provider_id, "fetched": 1, "nuevos": []}
    monkeypatch.setattr(model_catalog, "sync_provider_models", _sync_provider_models_rapido)

    async def _enrich_rapido():
        return {"enriched": 0}
    monkeypatch.setattr(model_catalog, "enrich_from_models_dev", _enrich_rapido)

    async def _facetas_en_riesgo_revienta(cur):
        raise RuntimeError("boom -- explota antes de terminar, con el candado tomado")
    monkeypatch.setattr(model_catalog, "_facetas_en_riesgo", _facetas_en_riesgo_revienta)

    libres_al_cerrar = []
    finalizar_real = registro.finalizar_ejecucion

    async def _finalizar_con_sonda(ejecucion_id, estado, resultado):
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                candado = await model_catalog.nombre_candado(cur, model_catalog._NOMBRE_CANDADO_SYNC)
                await cur.execute("SELECT IS_FREE_LOCK(%s)", (candado,))
                (libre,) = await cur.fetchone()
                libres_al_cerrar.append(libre)
                await registro.marcar_huerfanas_interrumpidas(cur)
            await conn.commit()
        return await finalizar_real(ejecucion_id, estado, resultado)

    monkeypatch.setattr(registro, "finalizar_ejecucion", _finalizar_con_sonda)

    with pytest.raises(RuntimeError, match="boom"):
        client.portal.call(functools.partial(registro.correr_sync_registrado, origen="manual"))

    async def _ultima_fila_manual():
        pool = await _pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, estado, resultado FROM catalogo_sync_ejecucion "
                    "WHERE origen='manual' ORDER BY id DESC LIMIT 1")
                return await cur.fetchone()

    eid, estado_final, resultado_json = client.portal.call(_ultima_fila_manual)
    limpiar_catalogo_sync_ejecucion.append(eid)

    assert libres_al_cerrar == [0], (
        "el candado de trabajo debía seguir SOSTENIDO en el momento del cierre por excepción -- "
        f"IS_FREE_LOCK devolvió {libres_al_cerrar!r}"
    )
    assert estado_final == "error"
    assert "boom" in resultado_json


def test_cierre_omitido_se_marca_cuando_nada_logra_cerrar_la_fila(
        client, monkeypatch, limpiar_catalogo_sync_ejecucion):
    """MINOR-2 (quinta ronda de la auditoría adversarial, 2026-09-28):
    escenario doblemente degradado -- `on_terminar` NO logra cerrar (ver
    `test_on_terminar_que_revienta_...` de arriba, mismo motivo cualquiera
    que sea) Y la red de seguridad final TAMPOCO -- `finalizar_ejecucion()`
    devuelve `False` las dos veces. `correr_sync_registrado()` tiene que
    devolver `cierre_omitido: True` en vez de mentir que cerró."""
    import model_catalog

    async def _finalizar_nunca_cierra(_ejecucion_id, _estado, _resultado):
        return False  # simula que la fila queda cerrada por otra vía, siempre

    monkeypatch.setattr(registro, "finalizar_ejecucion", _finalizar_nunca_cierra)

    async def _sync_all_fake(marca_nuevos=None, on_progreso=None, on_terminar=None):
        resultado = {
            "ok": True, "providers": [], "enrich": {}, "providers_fallidos": [],
            "providers_saltados": [], "enrich_fallido": False, "nuevos": {}, "facetas_en_riesgo": [],
        }
        if on_terminar is not None:
            await on_terminar(resultado)
        return resultado

    monkeypatch.setattr(model_catalog, "sync_all", _sync_all_fake)

    resultado = client.portal.call(functools.partial(registro.correr_sync_registrado, origen="manual"))
    if resultado.get("ejecucion_id") is not None:
        limpiar_catalogo_sync_ejecucion.append(resultado["ejecucion_id"])

    assert resultado.get("cierre_omitido") is True
