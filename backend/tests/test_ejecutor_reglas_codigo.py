# backend/tests/test_ejecutor_reglas_codigo.py
"""C1 dentro de la jaula para misiones de código (Task 7, plan "El Ejecutor
programa"): tres reglas prohibidas -- git push, --no-verify/core.hooksPath, y
tocar .github/workflows/ -- sembradas UNA vez en ejecutor_regla, ninguna
canario. Mismo patrón que test_ejecutor_tablas.py: jax_memory_test es
compartida entre frentes, así que se deja como se encontró."""
import pytest

from db.migrations import MIGRACION_EJECUTOR_REGLAS_CODIGO_V1, _ejecutor_reglas_codigo_v1
from tests.identidades import sql

_CODIGOS = ["codigo_git_push", "codigo_no_verify", "codigo_workflows"]


async def _correr(funcion):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await funcion(cur)
        await conn.commit()


def _vaciar(client):
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s",
                       (MIGRACION_EJECUTOR_REGLAS_CODIGO_V1,))
    client.portal.call(sql, "DELETE FROM ejecutor_regla WHERE codigo LIKE 'codigo\\_%'", None)


@pytest.fixture
def sin_marcas_codigo(client):
    _vaciar(client)
    yield
    _vaciar(client)


def test_reglas_de_codigo_sembradas(client, sin_marcas_codigo):
    client.portal.call(_correr, _ejecutor_reglas_codigo_v1)
    filas = client.portal.call(
        sql, "SELECT codigo, tipo, es_canario FROM ejecutor_regla WHERE codigo LIKE 'codigo\\_%' ORDER BY codigo",
        None, True)
    assert [f[0] for f in filas] == _CODIGOS
    assert all(f[1] == "prohibido" and not f[2] for f in filas)
