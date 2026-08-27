import os

import pytest

ENV_PATH = "/etc/jax/.env"


def _load_env() -> dict:
    env = {}
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
    except FileNotFoundError:  # fail-soft: carga de .env para tests; FileNotFoundError acotado, un .env ausente hace fallar los tests ruidosamente mas adelante, no en silencio
        pass
    return env


for _k, _v in _load_env().items():
    os.environ.setdefault(_k, _v)

os.environ["JAX_DB_NAME"] = "jax_memory_test"


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Modo "CI sin base de datos" (JAX_CI_NO_DB=1)
# ---------------------------------------------------------------------------
# El runner de GitHub Actions no tiene MariaDB. Medido el 2026-08-27 contra
# este arbol: correr la suite tal cual sin DB da 3 failed / 61 passed / 138
# errors -- 138 de esos 138 errores salen de UN solo punto (el fixture
# `client`, que levanta el lifespan de la app y ahi hace get_pool()).
#
# Este modo NO es una lista de tests deseleccionados a mano. Son DOS reglas
# estructurales, y las dos dicen lo mismo: "si el test necesita la DB y no
# hay DB, salta con motivo visible; si no la necesita, corre y se exige".
#
#   Regla 1 (tiempo de coleccion): el test pide el fixture `client`.
#   Regla 2 (tiempo de ejecucion): el test llega a aiomysql.create_pool() por
#           el camino que sea -- unico cuello de botella real de conexion,
#           asi que no depende de COMO cada modulo importe get_pool.
#
# Consecuencia deseada: un test nuevo que NO toca la DB queda cubierto por
# CI automaticamente, sin que nadie lo agregue a ninguna lista. Un test nuevo
# que SI la toca se salta solo, y aparece contado como skip en el log.
_CI_NO_DB = os.getenv("JAX_CI_NO_DB") == "1"

_NO_DB_REASON = "requiere MariaDB; este runner no tiene DB (JAX_CI_NO_DB=1)"


def pytest_collection_modifyitems(config, items):
    """Regla 1: saltar todo lo que pida el fixture `client`."""
    if not _CI_NO_DB:
        return
    marker = pytest.mark.skip(reason=_NO_DB_REASON + " [fixture client]")
    for item in items:
        if "client" in item.fixturenames:
            item.add_marker(marker)


@pytest.fixture(autouse=True)
def _skip_on_db_access(monkeypatch):
    """Regla 2: cualquier intento real de abrir el pool -> skip, no error."""
    if not _CI_NO_DB:
        return
    import aiomysql

    async def _skip(*args, **kwargs):
        pytest.skip(_NO_DB_REASON + " [aiomysql.create_pool]")

    monkeypatch.setattr(aiomysql, "create_pool", _skip)
