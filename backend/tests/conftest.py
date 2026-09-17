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

# Sello de facet_resolver aislado para TODA la sesión (2026-09-12), además del
# aislamiento por función de `_sello_de_facets_aislado` más abajo. El fixture
# `client` es de sesión y arranca la app -- y con ella run_migrations, que
# estampa el sello -- ANTES que cualquier fixture por función: sin esto, correr
# la suite en hall9000 estampó /srv/jax-data/facet-cache-seal (14:37:46), el
# archivo que vigilan Jacobs, el REPL y LAS MANOS. facet_resolver lee la ruta
# al importarse, así que tiene que quedar fijada acá, antes de cualquier import.
import tempfile  # noqa: E402

os.environ["JAX_FACET_SEAL_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="jax-test-sello-"), "facet-cache-seal")

# Respaldo de uso aislado para TODA la sesión (2026-09-15, cola durable,
# Task 2), por la misma razón que el sello de arriba: el default de
# `uso/cola.py` es /srv/jax-data/usage-spool, el directorio REAL del que drena
# la plataforma en producción y donde depositan Jacobs y LAS MANOS. Un test que
# ejercite el `except` de record_usage encolaría ahí una fila de mentira, y el
# reintento se la cobraría a un tenant de verdad. Se fija acá, antes de
# cualquier import: cola.py lee la variable en cada llamada, así que un test
# que quiera su propio directorio igual puede hacer monkeypatch.setenv.
os.environ["JAX_USAGE_SPOOL_DIR"] = tempfile.mkdtemp(prefix="jax-test-respaldo-uso-")

# Rutas de datos aisladas (2026-09-16, frente A, A-55), por la misma razón
# que el sello y el respaldo de uso: api/command.py, api/audit.py y
# api/admin/repository.py las leen AL IMPORTARSE. Antes eran ~/jax/... REALES
# y test_command_path_traversal escribía en ~/jax/missions de producción.
# Forzadas (no setdefault): un /etc/jax/.env con las rutas reales no puede
# ganarles. JAX_REPO_PATH y JAX_CONFIG_PATH NO se fijan acá: apuntan al repo
# `jax` de verdad (vocabulario, config) y las pone el job de CI o quien corre.
_RUTAS_DE_PRUEBA = tempfile.mkdtemp(prefix="jax-test-rutas-")
os.environ["JAX_MISSIONS_DIR"] = os.path.join(_RUTAS_DE_PRUEBA, "missions")
os.environ["JAX_REPO_BASE"] = os.path.join(_RUTAS_DE_PRUEBA, "repo")
os.environ["JAX_AUDIT_LOG_PATH"] = os.path.join(_RUTAS_DE_PRUEBA, "audit.jsonl")
os.environ["JAX_BIN"] = os.path.join(_RUTAS_DE_PRUEBA, "bin", "jax")
# Semilla (A-54): en una base vacía (CI) hay que sembrar user_id=1. Valores de
# prueba salvo que el .env traiga los reales.
os.environ.setdefault("JAX_SEED_SUPERADMIN_EMAIL", "superadmin-semilla@example.invalid")
os.environ.setdefault("JAX_SEED_TENANT_NAME", "Tenant de la semilla de prueba")


def _envolver_portal_call(portal_call):
    """Envuelve `BlockingPortal.call` (tanda A, hallazgo de Tarea 1,
    2026-09-14 -- regla de Fernando: sin hallazgos diferidos, se arregla
    acá). Una excepción de `pytest.outcomes.OutcomeException` (`Failed`,
    `Skipped`, ...) -- o cualquier `BaseException` que no sea
    `KeyboardInterrupt`/`SystemExit`/`GeneratorExit`/la excepción de
    cancelación de anyio -- lanzada DENTRO de la corrutina que corre el
    portal se captura ADENTRO (el `BlockingPortal` termina la tarea sin
    excepción, así que sigue vivo) y se relanza AFUERA, en el hilo
    sincrónico que llamó a `.call()` -- el test falla como corresponde.

    Medido (reporte anterior de Tarea 1): sin este envoltorio, un
    `pytest.raises(...)` que no dispara dentro de una función corrida vía
    `client.portal.call()` deja escapar un `Failed` (`BaseException`) hacia
    el `BlockingPortal`, y como el fixture `client` es `scope="session"`,
    mata el portal para el RESTO de la sesión -- la siguiente llamada de
    CUALQUIER test da `RuntimeError: This portal is not running` (635 →
    352 passed / 184 failed / 108 errors, medido). Por esto la ENMIENDA v3
    de la Tarea 1 prohíbe `pytest.raises(...)` dentro de una corrutina
    corrida por el portal: la corrutina captura y devuelve el error, la
    aserción va afuera -- este envoltorio es la red de seguridad para
    cuando ese patrón no se respeta (aquí o en cualquier test futuro).
    Test: tests/test_arnes_portal.py. Mutación: sacar este envoltorio pone
    ese test en rojo.

    ALCANCE, importante (fix round 1, Minor 7 de la revisión): esto NO
    envuelve solo las llamadas que un test hace explícitamente con
    `client.portal.call(...)`. `starlette.testclient.TestClient` usa el
    MISMO objeto `self.portal` para sus propias llamadas internas --
    `_portal_factory` en `starlette/testclient.py` lo comparte -- así que
    cada request HTTP (`client.get(...)`, `client.post(...)`, ...), el
    arranque/apagado del lifespan de la app, y cada mensaje de un
    `websocket_connect(...)` pasan también por `envuelto` sin que el test
    lo pida. Es intencional y no cambia nada para el camino feliz: con una
    `Exception` normal (lo único que esos caminos internos producen en la
    práctica), `envuelto` la captura y la relanza IDÉNTICA, así que el
    comportamiento es indistinguible de no tener el envoltorio -- la suite
    entera lo confirma (645/0/0 con DB, sin cambios de conducta). Solo
    importa si algún día una `BaseException` (un `pytest.fail`/`skip`, por
    ejemplo) escapara desde DENTRO de uno de esos caminos internos: ahí
    también quedaría protegida, no solo en los tests que llaman al portal
    a mano."""
    import functools
    import inspect

    import anyio

    @functools.wraps(portal_call)
    def envuelto(func, *args):
        capturada: list[BaseException] = []

        async def atrapada():
            # anyio.get_cancelled_exc_class() exige un backend async EN
            # CURSO -- acá, dentro de la corrutina que ya corre en el loop
            # del portal, no al armar el envoltorio (eso rompía el fixture
            # `client` entero: NoEventLoopError, sin loop activo todavía).
            inofensivas = (KeyboardInterrupt, SystemExit, GeneratorExit,
                           anyio.get_cancelled_exc_class())
            try:
                resultado = func(*args)
                if inspect.isawaitable(resultado):
                    resultado = await resultado
                return resultado
            except inofensivas:
                raise
            except BaseException as error:  # fail-soft: capturamos DENTRO del portal para relanzar AFUERA, ver docstring de _envolver_portal_call
                capturada.append(error)
                return None

        resultado = portal_call(atrapada)
        if capturada:
            raise capturada[0]
        return resultado

    return envuelto


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as c:
        c.portal.call = _envolver_portal_call(c.portal.call)
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

# El modo sin-DB simula "configurado, pero la DB no responde" -- NO "sin
# configurar". Desde que get_pool()/_db_conn fallan cerrado ante
# JAX_DB_HOST/JAX_DB_PORT ausentes (2026-09-01), sin esto los tests que tocan
# la DB revientan con RuntimeError en vez de saltarse por la Regla 2, y el modo
# entero deja de funcionar en un runner (donde no hay /etc/jax/.env). El valor
# es un placeholder: nada escucha ahi, y `aiomysql.create_pool` esta parcheado
# para saltar antes de intentarlo. Se usa 3308 y no 3306 a proposito -- 3306 es
# la instancia MUERTA y no debe aparecer como valor en ningun lado.
if _CI_NO_DB:
    os.environ.setdefault("JAX_DB_HOST", "127.0.0.1")
    os.environ.setdefault("JAX_DB_PORT", "3308")

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
def _sello_de_facets_aislado(tmp_path, monkeypatch):
    """Ningun test toca el sello REAL de facet_resolver.

    `invalidate_facet_cache()` escribe FACET_SEAL_PATH (default
    /srv/jax-data/facet-cache-seal, 2026-09-01): es el archivo compartido con
    Jacobs y el REPL en hall9000, asi que sin este aislamiento correr la suite
    en la maquina de desarrollo invalidaria la cache de los TRES procesos de
    produccion -- un efecto de lado fuera del arbol, invisible desde el log de
    pytest.

    Estructural y no una lista, mismo criterio que las dos reglas de
    JAX_CI_NO_DB: un test nuevo que llame al escritor queda aislado solo, sin
    que nadie lo tenga que agregar a ningun lado.
    """
    try:
        import facet_resolver
    except ImportError:
        # Si `facet_resolver` no se puede importar en este entorno, NINGUN test
        # puede llamar al escritor tampoco: no hay sello real que proteger. No
        # es una excepcion a la regla ni un fail-open -- es exactamente la
        # misma condicion. Pasa de verdad en CI: los jobs `no-fail-open-except`
        # e `invoke-facet-envoltorio` instalan solo pytest, sin aiomysql, y con
        # un import duro esta fixture los rompia enteros (verificado en la
        # primera corrida de este PR, 26 ERROR de fixture).
        return

    monkeypatch.setattr(
        facet_resolver, "FACET_SEAL_PATH", str(tmp_path / "facet-cache-seal"))


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
def _facet_canary_no_real_dispatch(monkeypatch):
    """Ronda de correccion 1 de Task 4 (2026-08-27), Hallazgo 5: el guard
    _running_under_pytest() de facet_canary.py solo cubre un punto de
    entrada de tres -- start_facet_canary(). probe_all() y probe_facet()
    son importables directo y hacen llamadas PAGAS sin ningun guard propio.
    El accidente real del 2026-08-24 (11 dispatches reales) no paso por
    NINGUN loop -- fue un script con codigo a nivel de modulo y nombre
    descubierto por pytest -- asi que un guard puesto solo en el loop no
    lo habria frenado.

    Autouse global: parchea facet_canary._invoke_facet para que CUALQUIER
    test que llegue a probe_facet/probe_all sin parchearlo el mismo
    explote con un RuntimeError ruidoso, en vez de completar una llamada
    real. La Task 5 va a escribir tests nuevos alrededor de probe_facet;
    si alguno se olvida de parchear _invoke_facet, esto lo hace fallar en
    vez de gastar plata.

    Los tests de tests/test_facet_canary.py que SI necesitan controlar
    _invoke_facet lo parchean ellos mismos dentro del test cuerpo, vía el
    mismo `monkeypatch` (fixture de function-scope, una sola instancia por
    test) -- ese setattr posterior pisa a este autouse sin conflicto: el
    undo de monkeypatch es una pila, se deshace en orden inverso.

    EL IMPORT VA GUARDADO, y no es fail-open. El job `no-fail-open-except`
    del CI instala SOLO pytest a proposito (es un scanner estatico que no
    debe arrastrar las dependencias de la app), asi que ahi `facet_canary`
    -> `api.chat` -> `fastapi` no es importable. Sin este guard, este
    autouse rompia la COLECCION de ese job: rojo en CI, verde en local,
    porque el venv del proyecto si tiene fastapi.
    No hay nada que proteger en ese entorno: si `facet_canary` no se puede
    importar, NINGUN test puede llamar a probe_facet/probe_all, asi que no
    existe llamada paga que prevenir. La proteccion se salta exactamente
    cuando es imposible que haga falta."""
    try:
        from jax_engine import facet_canary
    except ImportError:
        return

    async def _sin_parchear(*args, **kwargs):
        raise RuntimeError(
            "facet_canary._invoke_facet sin parchear en este test -- "
            "esto dispararia una llamada PAGA a un proveedor real "
            "(ver Hallazgo 5, ronda de correccion 1 de Task 4)")

    monkeypatch.setattr(facet_canary, "_invoke_facet", _sin_parchear)


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


@pytest.fixture(autouse=True)
def _limites_de_login_limpios():
    """Cada test arranca con los limitadores de login vacíos (2026-09-12).

    auth/rate_limit.py guarda sus contadores en memoria del proceso: sin esto,
    los logins de TODA la sesión suman en el mismo balde de la IP "testclient"
    y un test cualquiera recibe 429 según cuántos corrieron antes. Estructural,
    igual que el aislamiento del sello: un test nuevo que haga login queda
    cubierto solo.
    """
    try:
        from auth import rate_limit
    except ImportError:  # fail-soft: jobs de CI que solo instalan pytest (sin fastapi) no pueden importar el login ni llamarlo; no hay contador que limpiar
        yield
        return
    rate_limit.reset_login_limiters()
    yield
    rate_limit.reset_login_limiters()


async def _borrar_filas_de_uso(ids):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            marcas = ", ".join(["%s"] * len(ids))
            await cur.execute(f"DELETE FROM axioma_usage WHERE id IN ({marcas})", tuple(ids))
        await conn.commit()


@pytest.fixture(autouse=True)
def _uso_de_chat_limpio(request, monkeypatch):
    """Fix wave final, item 4 (2026-09-15): cada turno de chat con respuesta
    del LLM inserta una fila real en axioma_usage (jax_memory_test) y nadie
    la borraba -- la tabla crecia con cada corrida de la suite.

    Anota el id EXACTO de cada fila que escribe api.chat.record_usage durante
    el test (record_usage devuelve el id) y borra esos ids al terminar. Ni por
    tenant ni por ventana de tiempo: los tests de chat comparten tenant 1 y
    usuarios con otras sesiones, y la prueba de carga usa tenants >= 900000
    -- solo se borra lo que ESTE test escribio. Autouse y no un fixture a
    pedir: un test de chat nuevo queda cubierto solo (mismo criterio que el
    aislamiento del sello). Test: tests/test_uso_de_chat_limpio.py."""
    try:
        from api import chat as chat_mod
    except ImportError:  # fail-soft: jobs de CI que solo instalan pytest (sin fastapi) no pueden correr un turno de chat; no hay fila que borrar
        yield
        return
    real = chat_mod.record_usage
    escritas: list[int] = []

    async def anotando(*args, **kwargs):
        fila = await real(*args, **kwargs)
        if fila:
            escritas.append(fila)
        return fila

    monkeypatch.setattr(chat_mod, "record_usage", anotando)
    yield
    if escritas:
        # Hubo una fila => hubo DB => el test pidio `client` (el pool vive
        # en el loop de su portal).
        assert "client" in request.fixturenames, (
            f"el test escribio axioma_usage {escritas} sin el fixture client: no se pueden borrar")
        request.getfixturevalue("client").portal.call(_borrar_filas_de_uso, escritas)


@pytest.fixture
def chat_sin_memoria(monkeypatch):
    """Task 7 (2026-09-15): /api/chat rechaza ids no numericos ANTES del LLM
    (api/admin/usage.py::validar_ids_de_uso), asi que los tests de chat ya no
    pueden pasar un tenant NO numerico para esquivar la memoria semantica.
    Este fixture la apaga de forma explicita: con MemoryDB = None,
    _ensure_memory() devuelve False y el turno corre sin conversacion ni
    contexto semantico -- lo mismo que antes lograba el tenant no numerico."""
    from api import chat as chat_mod
    monkeypatch.setattr(chat_mod, "MemoryDB", None)


@pytest.fixture
def usuarios(client):
    """Fábrica de usuarios REALES en jax_users que se borran al terminar el
    test (2026-09-12, admin usuarios etapa 2): crear(**kw) -> (user_id, email),
    con los kwargs de tests/identidades.py::crear_usuario. Pide `client`, así
    que en el job sin DB se salta sola (Regla 1)."""
    from functools import partial

    from tests.identidades import borrar_usuario, crear_usuario

    creados = []

    def crear(**kw):
        user_id, email = client.portal.call(partial(crear_usuario, **kw))
        creados.append(user_id)
        return user_id, email

    yield crear
    for user_id in creados:
        client.portal.call(borrar_usuario, user_id)
