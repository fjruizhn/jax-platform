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


def _apply_facet_health_writer_stub(monkeypatch, ci_no_db: bool) -> bool:
    """Logica de `_stub_facet_health_writer_sin_db`, extraida a funcion
    plana para que un test pueda ejercitar la condicion `ci_no_db` sin
    depender de la variable de entorno real del proceso (ver
    test_stub_sin_db_solo_aplica_bajo_JAX_CI_NO_DB en
    tests/test_facet_health_outcomes.py, Ronda 2 de correccion, Pedido 2).

    Devuelve True si aplico el parche, False si no hizo nada -- para que
    ese test pueda afirmar sobre el resultado en vez de inspeccionar
    `facet_health.get_pool` por identidad en cada lado.

    Bajo `ci_no_db=True`, parchea **`facet_health.get_pool`** (no
    `api.chat.record_facet_health`, que fue el mecanismo de la Ronda 1 de
    correccion y resulto ser el mas debil de los que servian) para que
    lance una `Exception` NORMAL -- ni `pytest.skip` ni ningun otro
    `BaseException`. Por que este mecanismo y no el anterior:

    1. Cubre TODOS los namespaces por construccion: cualquier modulo que
       importe `record_facet_health` (la sonda de canario de las Tasks 4
       y 5 incluida) pasa por el MISMO `facet_health.get_pool` interno.
       Parchear por namespace de llamador (lo que hacia la Ronda 1) tenia
       fecha de vencimiento: un modulo nuevo que importe
       `record_facet_health` directo se reencuentra intacto el problema
       original (pytest.skip esquivando el except fail-soft).
    2. Conserva la validacion de outcome/source: `record_facet_health`
       corre sus guardas (`if outcome not in OUTCOMES: raise ValueError`)
       ANTES de tocar el pool -- con el stub de la Ronda 1 esas guardas
       nunca se ejecutaban bajo CI-sin-DB porque la funcion entera estaba
       reemplazada por un no-op. Como el CI corre SIEMPRE con
       JAX_CI_NO_DB=1, eso significaba que ningun test del repo podia
       volver a detectar un `outcome` fuera del conjunto valido. Es caro
       en particular porque `record_facet_health` se llama FUERA del
       `try` del envoltorio en `api/chat.py` (`_invoke_facet`): un
       `ValueError` ahi sube al endpoint como HTTP 502 con la respuesta
       del LLM ya generada y pagada, y sin fila en `axioma_usage`.
    3. Ejercita el fail-soft REAL de `facet_health.py` en vez de
       simularlo -- incluido el incremento de `_write_failures` -- porque
       la funcion real corre completa; solo falla adentro, en el mismo
       punto (`await get_pool()`) donde fallaria en produccion sin DB.
    4. Es un cambio de una linea en el cuerpo del fixture (parchear un
       nombre distinto), no un rediseño del mecanismo.

    GARANTIA Y DE QUE DEPENDE: que esto sea seguro para
    `tests/test_facet_health_writer.py` (Task 2) depende explicitamente
    de ese archivo y de que se mantenga como esta hoy: 5 de sus 6 tests
    parchean `facet_health.get_pool` ELLOS MISMOS dentro del test (lo
    cual corre despues de este autouse en el mismo test y lo pisa), y el
    sexto (`test_health_endpoint_expone_facet_health_writer_sin_db`) no
    toca el pool en absoluto. Si ese archivo se borra, o deja de
    parchear `get_pool` el mismo, o empieza a asumir un pool que
    responde, esta garantia se evapora sin aviso -- quien la use debe
    releer ese archivo antes de tocar este fixture."""
    if not ci_no_db:
        return False

    import facet_health

    async def _sin_pool_real(*args, **kwargs):
        raise RuntimeError(
            "JAX_CI_NO_DB=1: este runner no tiene MariaDB, "
            "facet_health.get_pool no esta disponible"
        )

    monkeypatch.setattr(facet_health, "get_pool", _sin_pool_real)
    return True


@pytest.fixture(autouse=True)
def _stub_facet_health_writer_sin_db(monkeypatch):
    """Autouse: bajo JAX_CI_NO_DB=1, hace que `facet_health.get_pool` se
    comporte como se comporta en PRODUCCION sin DB (una excepcion normal
    al abrir el pool) en vez de como se comporta hoy en este arnes bajo
    la Regla 2 de arriba (`pytest.skip`, que hereda de BaseException y
    esquiva el `except Exception` fail-soft de
    `facet_health.record_facet_health`, matando el test entero en vez de
    dejarlo correr). Ver el docstring de `_apply_facet_health_writer_stub`
    para el detalle completo, incluida la garantia de la que depende."""
    _apply_facet_health_writer_stub(monkeypatch, _CI_NO_DB)
