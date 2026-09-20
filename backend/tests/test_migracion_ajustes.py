"""Migración de los ajustes que mandan (spec 2026-09-16 §C): fija lo que el
código hacía cumplir en 26c9cd5 (no lo que decía DEFAULT_CONFIG), conserva el
system_name guardado, y corre UNA sola vez: un cambio posterior del admin no
se pisa en el próximo arranque. Contra jax_memory_test."""
import pytest

import ajustes
from db.migrations import MIGRACION_AJUSTES_V1, _ajustes_que_mandan_v1
from tests.identidades import sql


async def _migrar():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _ajustes_que_mandan_v1(cur)
        await conn.commit()


@pytest.fixture
def sin_marca(client, ajustes_en_db):
    marca = client.portal.call(sql, "SELECT nombre FROM axioma_migracion_de_datos WHERE nombre = %s",
                               (MIGRACION_AJUSTES_V1,), True)
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_AJUSTES_V1,))
    yield ajustes_en_db
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_AJUSTES_V1,))
    if marca:
        client.portal.call(sql, "INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)", (MIGRACION_AJUSTES_V1,))


def test_primera_corrida_fija_lo_que_el_codigo_hacia_cumplir(client, sin_marca):
    sin_marca.poner(session_timeout_min="60", max_pipelines="1", web_task_retention_days="7",
                    lang_default="en", system_name="Mi Sistema", pipeline_confirmar_usd="0.50")
    client.portal.call(_migrar)
    assert sin_marca.filas() == {
        "session_timeout_min": "10080", "max_pipelines": "3", "web_task_retention_days": "30",
        "lang_default": "es", "system_name": "Mi Sistema",
        # El tope de devoluciones lo pone OTRA migración
        # (_jacobs_tope_devoluciones_v1, 2026-09-20) y ésta tampoco lo toca --
        # mismo caso que el umbral de abajo.
        "jacobs.tope_devoluciones": "2",
        # La fila del umbral la pone OTRA migración (_ajuste_confirmar_costo_v1)
        # y ésta no la toca: sigue con el valor que tenía.
        "pipeline_confirmar_usd": "0.50",
    }
    assert client.portal.call(sql, "SELECT COUNT(*) FROM axioma_migracion_de_datos WHERE nombre = %s",
                              (MIGRACION_AJUSTES_V1,), True) == ((1,),)


def test_sin_fila_de_nombre_la_crea_con_el_que_se_mostraba(client, sin_marca):
    sin_marca.poner(session_timeout_min="60")
    sin_marca.quitar("system_name")
    client.portal.call(_migrar)
    assert sin_marca.filas()["system_name"] == "Axioma"


def test_segunda_corrida_no_pisa_lo_que_el_admin_cambio(client, sin_marca):
    client.portal.call(_migrar)
    sin_marca.poner(max_pipelines="1", session_timeout_min="120")
    client.portal.call(_migrar)
    filas = sin_marca.filas()
    assert (filas["max_pipelines"], filas["session_timeout_min"]) == ("1", "120")


def test_default_config_ya_no_recrea_los_cinco_ajustes():
    from api.admin.config_admin import DEFAULT_CONFIG
    assert not set(DEFAULT_CONFIG) & set(ajustes.CLAVES)


def test_default_config_no_tiene_ws_notifications():
    # R3 (Ruling del controlador, A-17): ws_notifications se retira de
    # DEFAULT_CONFIG -- si quedara, _ensure_defaults recrearía la fila en
    # cada GET y la migración la borraría en vano.
    from api.admin.config_admin import DEFAULT_CONFIG
    assert "ws_notifications" not in DEFAULT_CONFIG


@pytest.fixture
def sin_ws_notifications(client):
    """Restaura la fila ws_notifications tal cual estaba (A-17: la migración
    la borra una sola vez; el test no puede dejar la DB distinta de como la
    encontró)."""
    antes = client.portal.call(
        sql, "SELECT config_value FROM axioma_config WHERE config_key = %s", ("ws_notifications",), True)
    yield
    client.portal.call(sql, "DELETE FROM axioma_config WHERE config_key = %s", ("ws_notifications",))
    if antes:
        client.portal.call(
            sql, "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s)",
            ("ws_notifications", antes[0][0]))


def test_primera_corrida_borra_ws_notifications(client, sin_marca, sin_ws_notifications):
    client.portal.call(
        sql, "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)",
        ("ws_notifications", "true"))
    client.portal.call(_migrar)
    quedan = client.portal.call(
        sql, "SELECT COUNT(*) FROM axioma_config WHERE config_key = %s", ("ws_notifications",), True)
    assert quedan == ((0,),)
