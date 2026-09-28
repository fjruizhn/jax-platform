"""Configuración del sync programado del catálogo (2026-09-27, pedido de
Fernando: encender/apagar y elegir cada cuánto corre).

Dos capas: la ARITMÉTICA pura (validación de límites, meses de calendario,
próxima corrida, "¿toca correr?") sin ninguna base -- y la lectura/escritura
de la fila única en `catalogo_sync_config`, contra la base real de tests
(fixture `client`, mismo patrón que el resto del catálogo).
"""
import datetime as dt
import json

import pytest

import catalogo_sync_config as csc


# --------------------------------------------------------------------------
# validar_config: límites por unidad
# --------------------------------------------------------------------------

@pytest.mark.parametrize("unidad,minimo,maximo", [
    ("horas", 1, 720),
    ("dias", 1, 90),
    ("semanas", 1, 12),
    ("meses", 1, 12),
])
def test_validar_config_acepta_los_bordes_del_rango(unidad, minimo, maximo):
    csc.validar_config(minimo, unidad)
    csc.validar_config(maximo, unidad)


@pytest.mark.parametrize("unidad,minimo,maximo", [
    ("horas", 1, 720),
    ("dias", 1, 90),
    ("semanas", 1, 12),
    ("meses", 1, 12),
])
def test_validar_config_rechaza_fuera_del_rango(unidad, minimo, maximo):
    with pytest.raises(csc.ConfigInvalidaError):
        csc.validar_config(minimo - 1, unidad)
    with pytest.raises(csc.ConfigInvalidaError):
        csc.validar_config(maximo + 1, unidad)


def test_validar_config_rechaza_unidad_desconocida():
    with pytest.raises(csc.ConfigInvalidaError):
        csc.validar_config(1, "meses_lunares")


def test_validar_config_rechaza_valor_no_entero():
    with pytest.raises(csc.ConfigInvalidaError):
        csc.validar_config(1.5, "horas")
    with pytest.raises(csc.ConfigInvalidaError):
        csc.validar_config("6", "horas")


def test_validar_config_rechaza_booleano_aunque_sea_instancia_de_int():
    # bool es subclase de int en Python -- True/False no son "un entero" acá.
    with pytest.raises(csc.ConfigInvalidaError):
        csc.validar_config(True, "horas")


# --------------------------------------------------------------------------
# sumar_meses / proxima_corrida: meses de CALENDARIO, no 30 días
# --------------------------------------------------------------------------

def test_sumar_meses_31_enero_mas_un_mes_cae_en_28_febrero_no_bisiesto():
    resultado = csc.sumar_meses(dt.datetime(2027, 1, 31, 10, 0, 0), 1)
    assert resultado == dt.datetime(2027, 2, 28, 10, 0, 0)


def test_sumar_meses_31_enero_mas_un_mes_cae_en_29_febrero_bisiesto():
    resultado = csc.sumar_meses(dt.datetime(2028, 1, 31, 10, 0, 0), 1)
    assert resultado == dt.datetime(2028, 2, 29, 10, 0, 0)


def test_sumar_meses_cruza_el_anio():
    resultado = csc.sumar_meses(dt.datetime(2026, 11, 15, 8, 0, 0), 3)
    assert resultado == dt.datetime(2027, 2, 15, 8, 0, 0)


def test_sumar_meses_conserva_hora_minuto_segundo():
    resultado = csc.sumar_meses(dt.datetime(2026, 6, 10, 14, 33, 7), 2)
    assert resultado == dt.datetime(2026, 8, 10, 14, 33, 7)


def test_proxima_corrida_horas():
    desde = dt.datetime(2026, 9, 27, 0, 0, 0)
    assert csc.proxima_corrida(desde, 6, "horas") == dt.datetime(2026, 9, 27, 6, 0, 0)


def test_proxima_corrida_dias():
    desde = dt.datetime(2026, 9, 27, 0, 0, 0)
    assert csc.proxima_corrida(desde, 2, "dias") == dt.datetime(2026, 9, 29, 0, 0, 0)


def test_proxima_corrida_semanas():
    desde = dt.datetime(2026, 9, 27, 0, 0, 0)
    assert csc.proxima_corrida(desde, 1, "semanas") == dt.datetime(2026, 10, 4, 0, 0, 0)


def test_proxima_corrida_meses_usa_sumar_meses():
    desde = dt.datetime(2027, 1, 31, 0, 0, 0)
    assert csc.proxima_corrida(desde, 1, "meses") == dt.datetime(2027, 2, 28, 0, 0, 0)


# --------------------------------------------------------------------------
# toca_correr
# --------------------------------------------------------------------------

def test_toca_correr_sin_corrida_previa_es_siempre_true():
    assert csc.toca_correr(None, dt.datetime(2026, 9, 27, 0, 0, 0), 6, "horas") is True


def test_toca_correr_bien_antes_del_intervalo_es_false():
    # 1h antes del límite -- muy por fuera de TOLERANCIA_SEGUNDOS (10 min),
    # nunca se confunde con la holgura de MAJOR-1.
    ultima = dt.datetime(2026, 9, 27, 0, 0, 0)
    ahora = dt.datetime(2026, 9, 27, 5, 0, 0)
    assert csc.toca_correr(ultima, ahora, 6, "horas") is False


def test_toca_correr_justo_en_el_intervalo_es_true():
    ultima = dt.datetime(2026, 9, 27, 0, 0, 0)
    ahora = dt.datetime(2026, 9, 27, 6, 0, 0)
    assert csc.toca_correr(ultima, ahora, 6, "horas") is True


def test_toca_correr_despues_del_intervalo_es_true():
    ultima = dt.datetime(2026, 9, 27, 0, 0, 0)
    ahora = dt.datetime(2026, 9, 28, 0, 0, 0)
    assert csc.toca_correr(ultima, ahora, 6, "horas") is True


# --------------------------------------------------------------------------
# MAJOR-1 (auditoría adversarial, 2026-09-27): tolerancia contra el jitter
# real del timer (OnCalendar=hourly + RandomizedDelaySec=2min + duración).
# --------------------------------------------------------------------------

def test_toca_correr_dentro_de_la_tolerancia_antes_del_limite_es_true():
    ultima = dt.datetime(2026, 9, 27, 0, 0, 0)
    # 5 min antes del límite exacto de 6h -- dentro de TOLERANCIA_SEGUNDOS (10 min).
    ahora = dt.datetime(2026, 9, 27, 5, 55, 0)
    assert csc.toca_correr(ultima, ahora, 6, "horas") is True


def test_toca_correr_justo_fuera_de_la_tolerancia_es_false():
    ultima = dt.datetime(2026, 9, 27, 0, 0, 0)
    # 11 min antes del límite -- más allá de los 10 min de tolerancia.
    ahora = dt.datetime(2026, 9, 27, 5, 49, 0)
    assert csc.toca_correr(ultima, ahora, 6, "horas") is False


def test_toca_correr_reloj_retrocedido_corre_igual_y_avisa(caplog):
    """MINOR-2: última_exitosa en el futuro (reloj/zona movidos hacia atrás)
    -> corre igual, sin quedar esperando una fecha que tal vez nunca llegue,
    y deja un warning."""
    ultima = dt.datetime(2026, 9, 27, 12, 0, 0)
    ahora = dt.datetime(2026, 9, 27, 0, 0, 0)
    with caplog.at_level("WARNING"):
        assert csc.toca_correr(ultima, ahora, 6, "horas") is True
    assert any("reloj" in r.message.lower() or "zona" in r.message.lower() for r in caplog.records)


def test_toca_correr_serie_de_ticks_horarios_con_jitter_cada_1h_corre_en_cada_tick():
    """MAJOR-1: simula el timer real -- ticks aproximadamente cada hora, con
    el jitter de RandomizedDelaySec (hasta 2 min) más la duración real del
    chequeo (unos segundos) en CADA tick, independiente entre ticks (no
    acumulado -- cada tick de `OnCalendar=hourly` dispara relativo al
    calendario, no al tick anterior). "Cada 1 hora" tiene que correr en
    TODOS los ticks: sin tolerancia, un tick que llega unos segundos ANTES
    del límite exacto (porque el jitter de esta vez fue más chico que el de
    la vez pasada) se saltearía una hora entera."""
    import random
    aleatorio = random.Random(20260927)
    epoca = dt.datetime(2026, 1, 1, 0, 0, 0)
    ultima_exitosa = None
    corridas = 0
    for hora in range(1, 49):
        ahora = epoca + dt.timedelta(hours=hora, seconds=aleatorio.uniform(0, 120) + 4)
        if csc.toca_correr(ultima_exitosa, ahora, 1, "horas"):
            corridas += 1
            ultima_exitosa = ahora
    assert corridas == 48


def test_toca_correr_serie_de_ticks_horarios_con_jitter_cada_6h_corre_cada_6_ticks():
    import random
    aleatorio = random.Random(20260928)
    epoca = dt.datetime(2026, 1, 1, 0, 0, 0)
    # Arranca con una corrida exitosa YA hecha (bootstrap ya resuelto) --
    # sin esto el primer tick corre siempre (sin "última exitosa" con qué
    # comparar), y ese primer intervalo de largo 1 no es parte de lo que
    # este test mide (el régimen estable de "cada 6 ticks").
    ultima_exitosa = epoca
    ticks_desde_la_ultima = 0
    intervalos = []
    for hora in range(1, 49):
        ahora = epoca + dt.timedelta(hours=hora, seconds=aleatorio.uniform(0, 120) + 4)
        ticks_desde_la_ultima += 1
        if csc.toca_correr(ultima_exitosa, ahora, 6, "horas"):
            intervalos.append(ticks_desde_la_ultima)
            ticks_desde_la_ultima = 0
            ultima_exitosa = ahora
    assert intervalos == [6, 6, 6, 6, 6, 6, 6, 6]


# --------------------------------------------------------------------------
# leer_config / actualizar_config: contra la base real (fila única id=1)
# --------------------------------------------------------------------------

async def _restaurar_config(valores):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE catalogo_sync_config SET habilitado=%s, cada_valor=%s, cada_unidad=%s, "
                "actualizado_por=%s, actualizado_en=%s WHERE id=1",
                valores,
            )
        await conn.commit()


@pytest.fixture
def config_original(client):
    """Restaura la fila única al valor sembrado (habilitado, 6 horas, sin
    autor) al terminar -- la fila es compartida por toda la sesión de tests."""
    yield
    client.portal.call(_restaurar_config, (True, 6, "horas", None, None))


async def _leer_config():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            return await csc.leer_config(cur)


def test_leer_config_trae_la_semilla_por_defecto(client, config_original):
    config = client.portal.call(_leer_config)
    assert config["habilitado"] is True
    assert config["cada_valor"] == 6
    assert config["cada_unidad"] == "horas"


def test_actualizar_config_escribe_y_se_puede_releer(client, config_original):
    async def _actualizar_y_releer():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await csc.actualizar_config(cur, habilitado=False, cada_valor=3, cada_unidad="dias", actualizado_por=1)
            await conn.commit()
            async with conn.cursor() as cur:
                return await csc.leer_config(cur)

    config = client.portal.call(_actualizar_y_releer)
    assert config["habilitado"] is False
    assert config["cada_valor"] == 3
    assert config["cada_unidad"] == "dias"
    assert config["actualizado_por"] == 1
    assert config["actualizado_en"] is not None


def test_actualizar_config_valida_antes_de_escribir(client, config_original):
    async def _intentar():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await csc.actualizar_config(cur, habilitado=True, cada_valor=999, cada_unidad="horas", actualizado_por=1)

    with pytest.raises(csc.ConfigInvalidaError):
        client.portal.call(_intentar)

    # No se escribió nada: la fila sigue en el valor sembrado.
    config = client.portal.call(_leer_config)
    assert config["cada_valor"] == 6


# --------------------------------------------------------------------------
# MINOR-3 (auditoría adversarial, 2026-09-27): leer_config valida la fila.
# --------------------------------------------------------------------------

def test_leer_config_con_fila_invalida_levanta_error(client, config_original):
    """Una fila corrupta (escrita a mano, o por un bug futuro que sortee
    actualizar_config) tiene que ser un ERROR ruidoso -- nunca un
    toca_correr() que interprete basura en silencio como "cada hora" o
    "nunca"."""
    async def _corromper_y_leer():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE catalogo_sync_config SET cada_valor=999, cada_unidad='horas' WHERE id=1")
            await conn.commit()
            async with conn.cursor() as cur:
                return await csc.leer_config(cur)

    with pytest.raises(csc.ConfigInvalidaError):
        client.portal.call(_corromper_y_leer)


# --------------------------------------------------------------------------
# MAJOR-2 (auditoría adversarial, 2026-09-27): auditoría en axioma_config_audit.
# --------------------------------------------------------------------------

async def _historial_catalogo_sync(cur):
    await cur.execute(
        "SELECT actor_user_id, valor_anterior, valor_nuevo, origen, ip FROM axioma_config_audit "
        "WHERE config_key=%s ORDER BY id DESC LIMIT 1",
        (csc.CONFIG_KEY_AUDITORIA,),
    )
    return await cur.fetchone()


def test_actualizar_config_escribe_auditoria_con_antes_y_despues(client, config_original):
    async def _actualizar_y_auditar():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await csc.actualizar_config(
                    cur, habilitado=False, cada_valor=2, cada_unidad="dias", actualizado_por=1,
                    ip="203.0.113.7")
            await conn.commit()
            async with conn.cursor() as cur:
                return await _historial_catalogo_sync(cur)

    actor, antes, despues, origen, ip = client.portal.call(_actualizar_y_auditar)
    assert actor == 1
    assert origen == "catalogo_sync"
    assert ip == "203.0.113.7"
    antes_dict = json.loads(antes)
    despues_dict = json.loads(despues)
    assert antes_dict["cada_valor"] == 6  # el valor sembrado, antes de este cambio
    assert despues_dict == {"habilitado": False, "cada_valor": 2, "cada_unidad": "dias"}


def test_actualizar_config_sin_cambios_reales_no_audita(client, config_original):
    """Guardar lo MISMO no es un cambio que auditar -- mismo criterio que
    config_audit.escribir()."""
    async def _releer_id_de_ultima_auditoria():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM axioma_config_audit WHERE config_key=%s",
                    (csc.CONFIG_KEY_AUDITORIA,))
                (antes,) = await cur.fetchone()
                await csc.actualizar_config(
                    cur, habilitado=True, cada_valor=6, cada_unidad="horas", actualizado_por=1)
            await conn.commit()
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM axioma_config_audit WHERE config_key=%s",
                    (csc.CONFIG_KEY_AUDITORIA,))
                (despues,) = await cur.fetchone()
        return antes, despues

    antes, despues = client.portal.call(_releer_id_de_ultima_auditoria)
    assert antes == despues  # ninguna fila nueva


def test_un_fallo_de_auditoria_no_deja_el_cambio_aplicado(client, config_original, monkeypatch):
    """MAJOR-2: si el INSERT de auditoría revienta DENTRO de la transacción
    explícita (db.transaccion.transaccion), el UPDATE se revierte con él --
    el pool es autocommit=True, así que sin BEGIN/COMMIT explícitos esto no
    se podría demostrar (cada sentencia ya habría confirmado sola)."""
    from db.transaccion import transaccion

    class _AuditoriaRota(RuntimeError):
        pass

    original_execute = None

    async def _fallar_en_la_auditoria():
        nonlocal original_execute
        async with transaccion() as cur:
            original_execute = cur.execute

            async def execute_vigilado(sql, *args, **kwargs):
                if "axioma_config_audit" in sql:
                    raise _AuditoriaRota("el INSERT de auditoría revienta a propósito")
                return await original_execute(sql, *args, **kwargs)
            cur.execute = execute_vigilado

            await csc.actualizar_config(
                cur, habilitado=False, cada_valor=4, cada_unidad="semanas", actualizado_por=1)

    with pytest.raises(_AuditoriaRota):
        client.portal.call(_fallar_en_la_auditoria)

    # El UPDATE de catalogo_sync_config se revirtió: sigue en el valor sembrado.
    config = client.portal.call(_leer_config)
    assert config["cada_valor"] == 6
    assert config["cada_unidad"] == "horas"
