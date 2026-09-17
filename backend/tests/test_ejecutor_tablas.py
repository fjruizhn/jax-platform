# backend/tests/test_ejecutor_tablas.py
"""Tablas del Ejecutor (SP1, plan 1): prohibiciones, inventario y puntos de
restauración. Contra jax_memory_test. La semilla de reglas corre UNA vez: una
regla que el admin desactiva después no revive en el próximo arranque."""
import json

import pytest

from db.migrations import (
    MIGRACION_EJECUTOR_INVENTARIO_V1, MIGRACION_EJECUTOR_REGLAS_V1, _ejecutor_inventario_v1,
    _ejecutor_reglas_v1, parsear_inventario,
)
from tests.identidades import sql

_SEMILLA_CODIGOS = {
    "canario_c1", "ssh_sin_tt", "apt_full_upgrade_bridge", "migrate_fresh_produccion", "sed_i_env",
    "pure_ftpd_parar_atemai", "respaldos_borrar", "ajustes_claude_code", "borrar_archivos",
    "sql_destructivo", "dns_correo", "parar_servicio", "quitar_paquetes", "disco",
}


async def _correr(funcion):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await funcion(cur)
        await conn.commit()


def _vaciar(client):
    for nombre in (MIGRACION_EJECUTOR_REGLAS_V1, MIGRACION_EJECUTOR_INVENTARIO_V1):
        client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (nombre,))
    client.portal.call(sql, "DELETE FROM ejecutor_punto_restauracion")
    client.portal.call(sql, "DELETE FROM ejecutor_regla")
    client.portal.call(sql, "DELETE FROM ejecutor_host")


@pytest.fixture
def sin_marcas(client):
    """jax_memory_test es compartida entre frentes: se deja como se encontró el
    esquema (vacío de filas del Ejecutor) y la semilla vuelve a correr en el
    próximo arranque de la suite."""
    _vaciar(client)
    yield
    _vaciar(client)


def test_la_semilla_trae_las_catorce_reglas_con_un_solo_canario(client, sin_marcas):
    client.portal.call(_correr, _ejecutor_reglas_v1)
    filas = client.portal.call(sql, "SELECT codigo, es_canario, ejemplos_coincide FROM ejecutor_regla", None, True)
    assert {f[0] for f in filas} == _SEMILLA_CODIGOS
    assert sum(1 for f in filas if f[1]) == 1
    assert all(json.loads(f[2]) for f in filas), "una regla sin ejemplo que coincida no se vio bloquear"


def test_la_semilla_corre_una_sola_vez(client, sin_marcas):
    client.portal.call(_correr, _ejecutor_reglas_v1)
    client.portal.call(sql, "UPDATE ejecutor_regla SET activa = 0 WHERE codigo = 'sed_i_env'")
    client.portal.call(_correr, _ejecutor_reglas_v1)
    assert client.portal.call(sql, "SELECT activa FROM ejecutor_regla WHERE codigo = 'sed_i_env'", None, True) == ((0,),)


def test_la_edad_maxima_de_c2_se_siembra_sin_pisar(client, sin_marcas):
    client.portal.call(sql, "DELETE FROM axioma_config WHERE config_key = 'ejecutor.c2_edad_max_s'")
    client.portal.call(_correr, _ejecutor_reglas_v1)
    assert client.portal.call(sql, "SELECT config_value FROM axioma_config WHERE config_key = 'ejecutor.c2_edad_max_s'",
                              None, True) == (("86400",),)


def test_inventario_desde_el_entorno(client, sin_marcas, monkeypatch):
    monkeypatch.setenv("JAX_EJECUTOR_INVENTARIO",
                       "hall9000:192.0.2.5:58291:hypervisor:local+sin_clientes,bridge:192.0.2.20:58291:clientes")
    client.portal.call(_correr, _ejecutor_inventario_v1)
    filas = client.portal.call(sql, "SELECT nombre, ip, puerto, rol, es_local, con_datos_de_clientes FROM ejecutor_host "
                                    "ORDER BY nombre", None, True)
    assert filas == (("bridge", "192.0.2.20", 58291, "clientes", 0, 1),
                     ("hall9000", "192.0.2.5", 58291, "hypervisor", 1, 0))


def test_inventario_ausente_no_marca_la_migracion(client, sin_marcas, monkeypatch):
    monkeypatch.delenv("JAX_EJECUTOR_INVENTARIO", raising=False)
    client.portal.call(_correr, _ejecutor_inventario_v1)
    assert client.portal.call(sql, "SELECT COUNT(*) FROM axioma_migracion_de_datos WHERE nombre = %s",
                              (MIGRACION_EJECUTOR_INVENTARIO_V1,), True) == ((0,),)


def test_inventario_mal_formado_no_tumba_ni_marca(client, sin_marcas, monkeypatch):
    monkeypatch.setenv("JAX_EJECUTOR_INVENTARIO", "hall9000:192.0.2.5:no-es-puerto:hypervisor")
    client.portal.call(_correr, _ejecutor_inventario_v1)
    assert client.portal.call(sql, "SELECT COUNT(*) FROM ejecutor_host", None, True) == ((0,),)


@pytest.mark.parametrize("texto", [
    "", "a:1.2.3.4:22", "a:1.2.3.4:22:rol_raro", "a:1.2.3.4:0:clientes", "a:1.2.3.4:22:clientes:opcion_rara",
    "a:1.2.3.4:22:clientes,a:1.2.3.5:22:clientes",
])
def test_parsear_inventario_rechaza(texto):
    with pytest.raises(ValueError):
        parsear_inventario(texto)


def test_la_consulta_del_exportador_usa_el_indice(client, sin_marcas, monkeypatch):
    monkeypatch.setenv("JAX_EJECUTOR_INVENTARIO", "a:192.0.2.1:22:clientes,b:192.0.2.2:22:clientes")
    client.portal.call(_correr, _ejecutor_inventario_v1)
    for k in range(200):
        client.portal.call(sql, "INSERT INTO ejecutor_punto_restauracion (host_nombre, referencia, metodo, "
                                "restaurado_y_verificado_at, verificado_por, evidencia) VALUES (%s, %s, 'prueba', "
                                "UTC_TIMESTAMP(), 'test', 'test')", ("ab"[k % 2], f"prueba-{k}"))
    filas = client.portal.call(sql, "EXPLAIN SELECT host_nombre, MAX(restaurado_y_verificado_at) "
                                    "FROM ejecutor_punto_restauracion GROUP BY host_nombre", None, True)
    assert any("idx_ejecutor_punto_host_fecha" in str(f) for f in filas), filas
