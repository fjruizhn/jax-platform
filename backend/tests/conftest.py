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


@pytest.fixture(autouse=True)
def _stub_facet_health_writer_sin_db(monkeypatch):
    """Hace que `record_facet_health`, visto desde `api.chat`, se comporte
    bajo JAX_CI_NO_DB=1 tal como se comporta en produccion sin DB -- no
    como se comporta hoy en este arnes.

    En PRODUCCION sin DB: aiomysql.create_pool() lanza una excepcion
    normal, que el `except Exception` fail-soft de
    facet_health.record_facet_health atrapa; la funcion devuelve False y
    el turno de chat sigue (ver facet_health.py, Task 2).

    En TESTS sin DB: la Regla 2 de arriba parchea aiomysql.create_pool
    con un `pytest.skip(...)`, que hereda de BaseException (no de
    Exception) -- ESQUIVA ese except fail-soft y mata el test entero en
    vez de dejarlo correr. Sin este stub, cualquier test que invoque
    `_invoke_facet` (o cualquier otro camino que llegue a
    `record_facet_health`) sin mockearlo el mismo test se saltea en vez
    de correr bajo CI-sin-DB, aunque en produccion real ese camino jamas
    fallaria asi -- ver task-3-report.md, hallazgo del Step 7.

    Parcheamos el NAMESPACE de `api.chat` (no el de `facet_health`) a
    proposito: `tests/test_facet_health_writer.py` (Task 2) necesita la
    funcion REAL de `facet_health.record_facet_health` y parchea su
    propio `facet_health.get_pool` -- parchear `facet_health` acá le
    rompería esa cobertura.

    Import diferido de `api.chat` (no a nivel de modulo de este
    conftest): así no se altera el orden de import del arnés para los
    tests que no corren bajo JAX_CI_NO_DB=1.

    Un test que quiera espiar las llamadas reales (como
    tests/test_facet_health_outcomes.py) hace su propio
    `monkeypatch.setattr(chat_mod, "record_facet_health", ...)` dentro del
    test -- eso corre despues de este fixture y lo pisa, así que ese
    espía sigue viendo lo que llama de verdad.
    """
    if not _CI_NO_DB:
        return

    import api.chat as chat_mod

    async def _noop(facet, outcome, source, detail=None):
        return False

    monkeypatch.setattr(chat_mod, "record_facet_health", _noop)
