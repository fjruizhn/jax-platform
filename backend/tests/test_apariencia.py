"""GET /api/apariencia (spec 2026-09-14-tema-tokens §5.2).

Público y sin parámetros: devuelve theme_default (validado contra
{'dark','light'}, con respaldo 'dark') y, desde el frente C (2026-09-16),
lang_default y system_name vía ajustes.py (ilegibles -> 503, sin respaldo).
Contra jax_memory_test (conftest.py). Cada test deja la fila theme_default
como estaba.
"""
import pytest

from api.apariencia import CLAVE, CONSULTA
from tests.identidades import cabeceras


async def _sql(q, args=(), fetch=False):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(q, args)
            if fetch:
                return await cur.fetchall(), [d[0] for d in cur.description]
            return None


async def _leer():
    filas, _ = await _sql("SELECT config_value FROM axioma_config WHERE config_key = %s", (CLAVE,), True)
    return filas[0][0] if filas else None


async def _poner(valor):
    await _sql(
        "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE config_value = %s",
        (CLAVE, valor, valor),
    )


async def _borrar():
    await _sql("DELETE FROM axioma_config WHERE config_key = %s", (CLAVE,))


async def _restaurar(valor):
    if valor is None:
        await _borrar()
    else:
        await _poner(valor)


@pytest.fixture(autouse=True)
def fila_intacta(client, ajustes_en_db):
    ajustes_en_db.poner(lang_default="es", system_name="Axioma")
    antes = client.portal.call(_leer)
    yield
    client.portal.call(_restaurar, antes)


def test_devuelve_el_valor_guardado(client):
    client.portal.call(_poner, "light")
    r = client.get("/api/apariencia")
    assert r.status_code == 200
    assert r.json() == {"theme_default": "light", "lang_default": "es", "system_name": "Axioma"}


def test_fila_ausente_da_oscuro(client):
    client.portal.call(_borrar)
    assert client.get("/api/apariencia").json()["theme_default"] == "dark"


def test_valor_fuera_de_lista_da_oscuro(client):
    # El valor viaja a un atributo del DOM: nada fuera de la lista llega al cliente.
    for valor in ("<img src=x onerror=alert(1)>", "LIGHT", "claro", ""):
        client.portal.call(_poner, valor)
        assert client.get("/api/apariencia").json()["theme_default"] == "dark", valor


def test_no_devuelve_otras_claves(client):
    async def poner_smtp():
        await _sql("INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
                   "ON DUPLICATE KEY UPDATE config_value = %s",
                   ("smtp.apariencia_test", "secreto-que-no-sale", "secreto-que-no-sale"))

    async def quitar_smtp():
        await _sql("DELETE FROM axioma_config WHERE config_key = %s", ("smtp.apariencia_test",))

    client.portal.call(poner_smtp)
    try:
        r = client.get("/api/apariencia")
        assert set(r.json()) == {"theme_default", "lang_default", "system_name"}
        assert "secreto-que-no-sale" not in r.text
    finally:
        client.portal.call(quitar_smtp)


def test_responde_sin_token_y_no_valida_uno_basura(client):
    assert client.get("/api/apariencia").status_code == 200
    r = client.get("/api/apariencia", headers={"Authorization": "Bearer basura"})
    assert r.status_code == 200


def test_cache_control_no_cache(client):
    # Un cambio del admin se ve en la carga siguiente, no cuando caduque un caché.
    assert client.get("/api/apariencia").headers["cache-control"] == "no-cache"


def test_explain_usa_la_primary_sin_filesort_ni_temporary(client):
    # LAS CUATRO: EXPLAIN sobre la consulta REAL del endpoint (la misma constante).
    client.portal.call(_poner, "dark")

    async def explain():
        return await _sql("EXPLAIN " + CONSULTA, (CLAVE,), True)

    filas, columnas = client.portal.call(explain)
    plan = dict(zip(columnas, filas[0]))
    assert plan["key"] == "PRIMARY", plan
    extra = plan.get("Extra") or ""
    assert "filesort" not in extra and "temporary" not in extra, plan


def test_devuelve_el_idioma_y_el_nombre_del_sistema(client, ajustes_en_db):
    ajustes_en_db.poner(lang_default="en", system_name="Hal")
    j = client.get("/api/apariencia").json()
    assert (j["lang_default"], j["system_name"]) == ("en", "Hal")


def test_un_nombre_ilegible_responde_503_con_codigo_y_no_un_default(client, ajustes_en_db):
    ajustes_en_db.poner(system_name=" ")
    r = client.get("/api/apariencia")
    assert (r.status_code, r.json()) == (503, {"detail": {"code": "ajuste_ilegible", "clave": "system_name"}})


def test_un_cambio_desde_admin_se_ve_en_la_carga_siguiente(client, ajustes_en_db):
    assert client.get("/api/apariencia").json()["system_name"] == "Axioma"
    r = client.put("/api/admin/config", json=[{"key": "system_name", "value": "Axioma Lab"}],
                   headers=cabeceras(client, "ajustes-apariencia", role="superadmin"))
    assert r.status_code == 200
    assert client.get("/api/apariencia").json()["system_name"] == "Axioma Lab"
