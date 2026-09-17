"""D1 de Fernando (spec 2026-09-17 §1, §7 A): kimi y ada con motor.max_tokens=0
(0 = sin tope propio: manda model.max_output_tokens). El pipeline ef9b2d6e se
cortó en 8000 con 7997 tokens de razonamiento. Y un max_tokens negativo llega
al proveedor como tope inválido: el endpoint lo rechaza."""
import pytest

from db import migrations
from db.migrations import MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1, _motor_max_tokens_al_catalogo_v1
from tests.identidades import cabeceras, sql

ADMIN = "motor-tope-catalogo"


def test_la_semilla_de_kimi_y_ada_nace_sin_tope_propio():
    topes = {fila[0]: fila[4] for fila in migrations._MOTOR_SEED}
    assert topes == {"kimi": 0, "ada": 0}


@pytest.fixture
def motores_restaurados(client):
    antes = client.portal.call(sql, "SELECT `key`, max_tokens FROM motor WHERE `key` IN ('kimi', 'ada')", (), True)
    yield
    for clave, valor in antes:
        client.portal.call(sql, "UPDATE motor SET max_tokens = %s WHERE `key` = %s", (valor, clave))


@pytest.fixture
def sin_marca_motor(client, motores_restaurados):
    marca = client.portal.call(sql, "SELECT nombre FROM axioma_migracion_de_datos WHERE nombre = %s",
                               (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,), True)
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,))
    yield
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,))
    if marca:
        client.portal.call(sql, "INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)",
                           (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,))


async def _migrar():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _motor_max_tokens_al_catalogo_v1(cur)
        await conn.commit()


def _topes(client):
    return dict(client.portal.call(sql, "SELECT `key`, max_tokens FROM motor WHERE `key` IN ('kimi', 'ada')", (), True))


def test_primera_corrida_pone_kimi_y_ada_en_cero(client, sin_marca_motor):
    client.portal.call(sql, "UPDATE motor SET max_tokens = 8000 WHERE `key` IN ('kimi', 'ada')", ())
    client.portal.call(_migrar)
    assert _topes(client) == {"kimi": 0, "ada": 0}


def test_segunda_corrida_no_pisa_un_ajuste_posterior(client, sin_marca_motor):
    client.portal.call(_migrar)
    client.portal.call(sql, "UPDATE motor SET max_tokens = 4096 WHERE `key` = 'kimi'", ())
    client.portal.call(_migrar)
    assert _topes(client)["kimi"] == 4096


def test_patch_rechaza_max_tokens_negativo_sin_tocar_la_fila(client, motores_restaurados):
    antes = _topes(client)["kimi"]
    r = client.patch("/api/admin/motors/kimi", json={"max_tokens": -1},
                     headers=cabeceras(client, ADMIN, role="superadmin"))
    assert r.status_code == 422, r.text
    assert _topes(client)["kimi"] == antes


def test_post_rechaza_max_tokens_negativo(client):
    try:
        r = client.post("/api/admin/motors", json={
            "key": "test-tope-negativo", "provider_id": "deepseek", "model_id": "deepseek-v4-flash",
            "transport": "http_openai_compat", "max_tokens": -1,
        }, headers=cabeceras(client, ADMIN, role="superadmin"))
        assert r.status_code == 422, r.text
    finally:
        client.portal.call(sql, "DELETE FROM capability_motor WHERE motor_key = 'test-tope-negativo'", ())
        client.portal.call(sql, "DELETE FROM motor WHERE `key` = 'test-tope-negativo'", ())


# 2026-09-17 (merge de master): en jax_memory_test las filas de `motor` se
# habían vuelto a crear con 8000 (los tests de migraciones las borran y
# resiembran) y el marcador impedía corregirlas. El arreglo corre en cada
# arranque con guard sobre el valor viejo.
def test_el_tope_viejo_se_corrige_aunque_la_migracion_ya_este_marcada(client):
    async def correr():
        await sql("UPDATE motor SET max_tokens = 8000 WHERE `key` IN ('kimi', 'ada')")
        await migrations.run_migrations()
        return await sql("SELECT `key`, max_tokens FROM motor WHERE `key` IN ('kimi','ada') ORDER BY `key`",
                         fetch=True)

    assert client.portal.call(correr) == (("ada", 0), ("kimi", 0))


def test_un_tope_puesto_a_mano_no_se_pisa(client):
    async def correr():
        await sql("UPDATE motor SET max_tokens = 4321 WHERE `key` = 'kimi'")
        await migrations.run_migrations()
        fila = await sql("SELECT max_tokens FROM motor WHERE `key` = 'kimi'", fetch=True)
        await sql("UPDATE motor SET max_tokens = 0 WHERE `key` = 'kimi'")
        return fila

    assert client.portal.call(correr) == ((4321,),)
