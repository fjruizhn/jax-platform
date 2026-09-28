"""Configuración del sync programado del catálogo (2026-09-27, pedido de
Fernando: encender/apagar y elegir cada cuánto corre).

Dos capas: la ARITMÉTICA pura (validación de límites, meses de calendario,
próxima corrida, "¿toca correr?") sin ninguna base -- y la lectura/escritura
de la fila única en `catalogo_sync_config`, contra la base real de tests
(fixture `client`, mismo patrón que el resto del catálogo).
"""
import datetime as dt

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


def test_toca_correr_antes_del_intervalo_es_false():
    ultima = dt.datetime(2026, 9, 27, 0, 0, 0)
    ahora = dt.datetime(2026, 9, 27, 5, 59, 59)
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
