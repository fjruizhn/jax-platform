"""La base de datos de tests de ESTA sesión.

ESPEJO -- port de `base_de_test.py` de `jax` (raíz del repo), 2026-09-20.
Mismo criterio que `db_connect_config.py`/`facet_resolver.py`/
`credential_resolver.py`: "espejo mínimo, sin paquete compartido" -- cada
repo con su conector local, comparado por `scripts/check_mirror_sync.py`
(familia `base_de_test`, agregada en el mismo commit que este archivo).

**El problema real** (2026-09-17, en `jax`). Tres sesiones de Claude
corriendo la suite a la vez comparten una sola base, `jax_memory_test`, en
la MariaDB de hall9000. Se pisaron de verdad: filas con `mode` en NULL, un
arnés expirando pipelines ajenos, una sesión borrando una fila de uso de
otra, un test dejando todas las filas en NULL. El síntoma son rojos
intermitentes que no son del código -- el peor tipo de rojo, porque enseña
a reintentar hasta que pase.

**`jax-platform` no tenía este mecanismo** (medido 2026-09-20):
`backend/tests/conftest.py:13` fijaba `JAX_DB_NAME = "jax_memory_test"` a
secas, sin aislamiento de ninguna clase -- el mismo choque de `jax`, sin
siquiera el opt-in que `jax` sí tenía. Comparten la MISMA base física
(`jax_memory_test` en la MariaDB de hall9000: sus tablas mezclan catálogo de
este repo -- `facet`, `motor`, `credential`... -- con las de `jax` --
`jacobs_pipelines`, `jacobs_steps`...), así que el nombre y el prefijo de
sesión tienen que ser IDÉNTICOS a los de `jax` para no inventar una segunda
convención sobre la misma base.

**La decisión** (Fernando, 2026-09-17 en `jax`, portada acá 2026-09-20).
Cada sesión usa su propia base: `JAX_TEST_DB_SUFIJO=<sufijo>` y la suite
corre contra `jax_memory_test_<sufijo>`. Sin la variable Y fuera de CI, cada
proceso genera un sufijo propio (`auto<pid><random>`) la primera vez que
resuelve el nombre, lo fija en `os.environ` para que el resto del proceso
-- y los subprocesos que hereden el entorno -- vean el mismo valor, y lo
registra para borrarse solo al salir del proceso (`atexit`): el default
automático multiplica el ritmo al que se acumulan bases huérfanas si nadie
limpia. CI se detecta con la variable estándar `CI` (GitHub Actions, GitLab
CI, CircleCI) y NO cambia: cada job de `.github/workflows/policy.yml` que
toca la base ya corre contra su propio contenedor MariaDB efímero, sin
sesiones concurrentes que se puedan pisar ahí.

**Por qué un error y no un fallback.** Un sufijo inválido (puesto a mano,
con un valor que no matchea `SUFIJO_VALIDO`) es un error explícito al
arrancar, nunca una caída silenciosa a la base compartida: caer a la
compartida en silencio ES el defecto que esto arregla. Misma razón por la
que `_verificar_que_no_es_produccion()` existe aunque el nombre se arme con
un f-string que no puede dar `jax_memory`: el control no está para el
camino que hoy se ve imposible, está para el refactor que mañana lo hace
posible (Principio IX -- el contrato va antes que la capacidad).

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

#: La base compartida de siempre. Sin sufijo, la suite sigue corriendo acá.
BASE_COMPARTIDA = "jax_memory_test"

#: PRODUCCIÓN. Ninguna resolución de este módulo puede dar este nombre.
BASE_DE_PRODUCCION = "jax_memory"

#: La variable de entorno que elige la base de la sesión.
VARIABLE_DEL_SUFIJO = "JAX_TEST_DB_SUFIJO"

#: La variable que el código lee para saber contra qué base conectarse.
VARIABLE_DE_LA_BASE = "JAX_DB_NAME"

#: Minúsculas, números y guion bajo. Nada más: el nombre va a un `CREATE
#: DATABASE` y a un `DROP DATABASE`, y no se escapa un identificador con
#: parámetros. Lo que no matchea esto no llega a la SQL.
SUFIJO_VALIDO = re.compile(r"\A[a-z0-9_]+\Z")

#: El identificador de MariaDB tiene un tope de 64 caracteres. Se valida el
#: NOMBRE COMPLETO contra ese tope, no el sufijo contra un número inventado:
#: si mañana `BASE_COMPARTIDA` cambia de largo, el control sigue siendo cierto.
LARGO_MAXIMO_DEL_IDENTIFICADOR = 64


class BaseDeTestInvalida(RuntimeError):
    """El sufijo (o el nombre resuelto) no sirve. Se levanta al arrancar."""


def _verificar_que_no_es_produccion(nombre: str) -> str:
    """El último control antes de devolver un nombre: nunca `jax_memory`, y
    siempre derivado de `BASE_COMPARTIDA`.

    Es deliberadamente redundante con la construcción del nombre. Un control
    que sólo cubre lo que hoy puede pasar no protege del cambio de mañana, y
    lo que está en juego es la base de producción.
    """
    if nombre == BASE_DE_PRODUCCION:
        raise BaseDeTestInvalida(
            f"la base de tests resolvió a {BASE_DE_PRODUCCION!r}, que es PRODUCCIÓN. "
            f"La suite BORRA y ACTUALIZA filas: no corre contra esa base."
        )
    if nombre != BASE_COMPARTIDA and not nombre.startswith(f"{BASE_COMPARTIDA}_"):
        raise BaseDeTestInvalida(
            f"la base de tests resolvió a {nombre!r}, que no es {BASE_COMPARTIDA!r} "
            f"ni una base con el prefijo {BASE_COMPARTIDA + '_'!r}."
        )
    return nombre


def es_base_de_test(nombre: str | None) -> bool:
    """¿`nombre` es una base de tests legítima? La compartida o una con sufijo
    válido. `jax_memory` (y cualquier otra cosa) da False."""
    if not nombre:
        return False
    if nombre == BASE_COMPARTIDA:
        return True
    prefijo = f"{BASE_COMPARTIDA}_"
    if not nombre.startswith(prefijo):
        return False
    if len(nombre) > LARGO_MAXIMO_DEL_IDENTIFICADOR:
        return False
    return bool(SUFIJO_VALIDO.match(nombre[len(prefijo):]))


def _en_ci() -> bool:
    """¿Esta corrida es un job de CI? `CI` es la variable que exportan
    GitHub Actions, GitLab CI y CircleCI por convención propia -- se lee
    tal cual, no se inventa. Cada job de `.github/workflows/policy.yml` que
    toca la base corre contra su propio contenedor MariaDB efímero (ver
    `services: mariadb:` de cada job): no hay sesiones concurrentes que se
    puedan pisar ahí, así que el default automático de acá abajo no hace
    falta y sólo sumaría un clonado de esquema de más en cada corrida."""
    return os.environ.get("CI", "").strip().lower() in ("1", "true", "yes")


def _sufijo_automatico_de_sesion() -> str:
    """Un sufijo propio de ESTE proceso, para la sesión que no exportó
    `JAX_TEST_DB_SUFIJO` a mano. `os.getpid()` más unos bytes al azar: el PID
    solo no alcanza (se reutiliza entre procesos que ya terminaron), y el
    azar solo pierde la pista de qué proceso la creó al mirar el nombre en
    `SHOW DATABASES`.

    Antes de este mecanismo, crear una base con sufijo era un paso que
    alguien pedía a propósito (`export JAX_TEST_DB_SUFIJO=...`); ahora pasa
    en CADA corrida local que no lo pida. Sin limpiar, eso multiplica el
    ritmo al que se acumulan bases huérfanas. Por eso acá mismo se registra
    el borrado al salir del proceso -- ver `_borrar_al_salir()`. Una base
    con sufijo EXPLÍCITO (pasado a mano) NO se registra: alguien pudo poner
    ese sufijo a propósito para reusarla entre corridas, y borrarla forzaría
    un clonado de esquema de más en cada pytest suelto."""
    sufijo = f"auto{os.getpid()}{secrets.token_hex(4)}"
    atexit.register(_borrar_al_salir, f"{BASE_COMPARTIDA}_{sufijo}")
    return sufijo


def _en_ci_sin_db() -> bool:
    """¿Este proceso corre en modo "CI sin base de datos"
    (`JAX_CI_NO_DB=1`, ver `tests/conftest.py`)? Bajo ese modo no hay
    MariaDB real a mano, punto -- sin importar qué diga `JAX_DB_HOST`.

    Hace falta esta pregunta APARTE de `not os.environ.get("JAX_DB_HOST")`:
    en cualquier máquina con `/etc/jax/.env` (hall9000 entre ellas),
    `tests/conftest.py` carga ese archivo con `setdefault` ANTES de que
    `JAX_CI_NO_DB` se evalúe ahí (conftest.py:393, después de la línea 30
    que llama a `fijar_base_de_test()`/`asegurar_base_de_test()`), así que
    `JAX_DB_HOST` queda puesto igual. La causa raíz medida el 2026-09-21:
    `asegurar_base_de_test()` sólo miraba `JAX_DB_HOST` y terminaba
    intentando clonar el esquema de verdad bajo `JAX_CI_NO_DB=1`, explotando
    contra el puerto que el modo "sin DB" usa para simular una MariaDB
    configurada pero caída. Mismo criterio que
    `tests/test_el_juez_facet.py::_SIN_MARIADB` en la rama
    `fix/semilla-el-juez`: la pregunta es "¿hay MariaDB reconocida como
    ausente por este runner?", no "¿hay una variable puesta?"."""
    return os.environ.get("JAX_CI_NO_DB") == "1"


def _borrar_al_salir(nombre: str) -> None:
    """Registrado en `atexit` SOLO para una base auto-generada (nunca para
    una pasada por `JAX_TEST_DB_SUFIJO` a mano). Sin `JAX_DB_HOST`, o bajo
    `JAX_CI_NO_DB=1` (ver `_en_ci_sin_db()`), no hay MariaDB a mano y no hay
    nada que borrar -- mismo criterio que `asegurar_base_de_test()`.
    Cualquier error (red caída, timeout) queda silenciado a propósito: es un
    best-effort de limpieza al cerrar, no una condición de salida del
    proceso; lo que esto no llegue a borrar lo barre después
    `scripts/limpiar_bases_de_test.py`.

    DIVERGENCIA DELIBERADA con jax: el guard de `_en_ci_sin_db()` es propio de
    este repo. jax no tiene modo "CI sin base" -- cada job suyo que toca la
    base levanta su propio contenedor MariaDB efímero (`services: mariadb:` en
    su policy.yml), así que allá `JAX_DB_HOST` ausente ya distingue bien los
    dos casos. Acá no alcanzaba: `tests/conftest.py` recarga `/etc/jax/.env` en
    cada import y REPONE `JAX_DB_HOST`, así que el modo sin base quedaba
    "configurado y caído" y esta función intentaba conectar de verdad --
    con el puerto real habría creado un clon en la MariaDB compartida, que es
    justo lo que ese modo promete que no pasa (jax-platform#142, 2026-09-21)."""
    if _en_ci_sin_db() or not os.environ.get("JAX_DB_HOST"):
        return
    import asyncio
    try:
        asyncio.run(_dropear_base_de_sesion(nombre))
    except Exception:  # fail-soft: best-effort al salir del proceso, no una condición de salida; scripts/limpiar_bases_de_test.py barre lo que quede
        pass


async def _dropear_base_de_sesion(nombre: str) -> None:
    """El DROP de verdad. El candado es el mismo `es_base_de_test()` que usa
    el resto del módulo, MÁS la exclusión explícita de `BASE_COMPARTIDA`:
    esta función nunca borra la base pelada ni nada que no lleve el prefijo
    de test. `tests/test_base_por_sesion.py` lo ejercita intentando borrar
    `jax_memory` y `jax_memory_test` de verdad."""
    if nombre == BASE_COMPARTIDA or not es_base_de_test(nombre):
        return
    exigir_conexion_permitida(nombre)
    import aiomysql

    # DIVERGENCIA DELIBERADA con jax: allá este import es
    # `from jax.core.db_connect_config import db_connect_timeout_seconds`
    # (paquete `jax.core`); acá `pytest.ini` pone la raíz de `backend/` en
    # `sys.path` (`pythonpath = .`) y el conector local vive suelto,
    # `db_connect_config.py`, igual que main.py y el resto de este repo lo
    # importan.
    from db_connect_config import db_connect_timeout_seconds

    conn = await aiomysql.connect(
        db=BASE_PLANTILLA, autocommit=True,
        connect_timeout=db_connect_timeout_seconds(),
        **_parametros_de_conexion())
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{nombre}`")
    finally:
        conn.close()


def nombre_base_de_test(sufijo: str | None = None) -> str:
    """El nombre de la base de tests de esta sesión.

    Con `JAX_TEST_DB_SUFIJO` puesto (a mano, o ya fijado por una llamada
    anterior de este mismo proceso): `jax_memory_test_<sufijo>`.

    Sin la variable: en CI, `jax_memory_test` pelada, como siempre (cada job
    ya está aislado en su propio contenedor). Fuera de CI -- el caso de
    cualquier sesión o worktree local -- se genera un sufijo propio del
    proceso y se fija en `JAX_TEST_DB_SUFIJO` para que el resto de esta
    sesión (y los subprocesos que hereden el entorno) resuelvan la MISMA
    base. Decisión de Fernando, 2026-09-20: el aislamiento es el default,
    nadie tiene que acordarse de exportar nada.

    Un sufijo presente pero inválido (vacío, con mayúsculas, con guiones,
    con punto y coma, demasiado largo) es `BaseDeTestInvalida`. Un sufijo
    vacío TAMBIÉN es un error y no un "como si no estuviera": quien exportó
    la variable quiso una base propia, y darle la compartida en silencio es
    justo el defecto que este módulo arregla.
    """
    if sufijo is None:
        sufijo = os.environ.get(VARIABLE_DEL_SUFIJO)
    if sufijo is None:
        if _en_ci():
            return _verificar_que_no_es_produccion(BASE_COMPARTIDA)
        sufijo = _sufijo_automatico_de_sesion()
        os.environ[VARIABLE_DEL_SUFIJO] = sufijo
    if not SUFIJO_VALIDO.match(sufijo):
        raise BaseDeTestInvalida(
            f"{VARIABLE_DEL_SUFIJO}={sufijo!r} no sirve como sufijo de base: "
            f"sólo minúsculas, números y guion bajo (`[a-z0-9_]+`), sin vacío. "
            f"NO se cae a {BASE_COMPARTIDA!r}: la base compartida es lo que "
            f"este mecanismo evita."
        )
    nombre = f"{BASE_COMPARTIDA}_{sufijo}"
    if len(nombre) > LARGO_MAXIMO_DEL_IDENTIFICADOR:
        raise BaseDeTestInvalida(
            f"{VARIABLE_DEL_SUFIJO}={sufijo!r} da {nombre!r}, de {len(nombre)} "
            f"caracteres: el identificador de MariaDB tope en "
            f"{LARGO_MAXIMO_DEL_IDENTIFICADOR}."
        )
    return _verificar_que_no_es_produccion(nombre)


def fijar_base_de_test() -> str:
    """Pone `JAX_DB_NAME` en la base de esta sesión, pise lo que pise.

    Es el reemplazo exacto de `os.environ["JAX_DB_NAME"] = "jax_memory_test"`
    que `tests/conftest.py` fijaba a mano: mismo comportamiento (override
    incondicional), pero respetando el sufijo de la sesión.
    DIVERGENCIA DELIBERADA con jax: ver la nota en el canónico -- allá el
    reemplazo es de ~20 archivos; acá, de un solo punto.
    """
    nombre = nombre_base_de_test()
    os.environ[VARIABLE_DE_LA_BASE] = nombre
    return nombre


def exigir_base_de_test() -> str:
    """Como `fijar_base_de_test()`, pero respeta una `JAX_DB_NAME` que ya
    venga puesta -- y explota si esa que viene NO es una base de tests.

    Protege del `set -a; . <(sudo -n cat /etc/jax/.env)` (ahí
    `JAX_DB_NAME=jax_memory`). DIVERGENCIA DELIBERADA con jax: allá esta
    función reemplaza un guard viejo que existió en ~20 archivos; acá la
    protección es nueva.
    """
    actual = os.environ.get(VARIABLE_DE_LA_BASE)
    if actual:
        if not es_base_de_test(actual):
            raise BaseDeTestInvalida(
                f"{VARIABLE_DE_LA_BASE}={actual!r} ya está seteado (¿sourceaste "
                f"/etc/jax/.env?). Este test ESCRIBE en la base: sólo corre "
                f"contra {BASE_COMPARTIDA!r} o una base "
                f"{BASE_COMPARTIDA + '_<sufijo>'!r}."
            )
        return actual
    return fijar_base_de_test()


# --------------------------------------------------------------------------
# Creación de la base de la sesión
# --------------------------------------------------------------------------
#
# La base con sufijo no existe hasta que alguien la crea. Se crea CLONANDO el
# esquema de `jax_memory_test` -- la base que la suite usa hoy -- y corriendo
# después `run_migrations()`, que es el camino que este repo ya tiene para su
# propio esquema. No hay un DDL paralelo acá a propósito: una segunda fuente
# de verdad del esquema se desincroniza sola (Regla Absoluta).
#
# El esquema de `jax_memory_test` incluye tablas que NO son de este repo
# (`jacobs_pipelines`, `jacobs_steps`, ... las crea `jax`). Por eso la
# plantilla es la base compartida y no `run_migrations()` a secas: una base
# vacía más `run_migrations()` no reproduce lo que la suite necesita --
# mismo motivo, mismo esquema físico, que en `jax`.

#: De dónde se copia el esquema de una base nueva.
BASE_PLANTILLA = BASE_COMPARTIDA

#: Además del esquema se copian los DATOS de las tablas chicas. No es un
#: capricho de tamaño: las tablas de catálogo y gobernanza (`facet`,
#: `capability`, `motor`, `provider`, `model`, `facet_binding`, `credential`,
#: `jax_tenants`...) son las que el código RESUELVE contra la base, y una base
#: recién clonada sin ellas hace fallar tests que hoy pasan (mismo hallazgo
#: que en `jax`, 2026-09-17: `tests/test_facetas_de_gobernanza_db.py` en rojo
#: contra una clonación sólo de esquema). Las tablas grandes son historia
#: transaccional (`jacobs_events`, `jacobs_steps`, `shadow_messages`...) que
#: la suite se escribe sola y que nadie afirma nada sobre ella: copiarlas
#: costaría minutos por sesión y cientos de MB.
#:
#: El corte se declara acá y se puede mover con `JAX_TEST_DB_FILAS_MAXIMAS`.
#: Es un umbral, no una lista de tablas: una lista se desactualiza en cuanto
#: alguien agrega una tabla de catálogo y nadie se entera hasta el rojo.
FILAS_MAXIMAS_A_COPIAR = int(os.environ.get("JAX_TEST_DB_FILAS_MAXIMAS", "2000"))


#: Puertos donde vive la MariaDB de PRODUCCION (3308 en hall9000; 3306 es el
#: default de `_parametros_de_conexion()` y el puerto que usa CI en su propio
#: contenedor). Fuera de CI, conectarse a uno de ellos es conectarse al servidor
#: que tambien tiene `jax_memory`: la suite CREA y BORRA bases ahi, y la semilla de
#: gobernanza lee produccion. Un contenedor desechable usa otro puerto.
PUERTOS_DE_PRODUCCION = frozenset({3306, 3308})

#: El permiso explicito, para quien de verdad quiere correr la suite contra la
#: instancia de produccion (hall9000: la base de test vive en la misma MariaDB).
#: Habilita dos cosas, y solo dos: usar un puerto de `PUERTOS_DE_PRODUCCION` fuera
#: de CI, y la conexion de SOLO LECTURA a `jax_memory` de la semilla de gobernanza.
#: Nunca habilita escribir en una base sin sufijo de test.
VARIABLE_PERMISO_INSTANCIA_DE_PRODUCCION = "JAX_TEST_DB_PERMITIR_INSTANCIA_DE_PRODUCCION"


def _permiso_instancia_de_produccion() -> bool:
    return os.environ.get(VARIABLE_PERMISO_INSTANCIA_DE_PRODUCCION, "").strip().lower() in ("1", "true", "yes")


def exigir_conexion_permitida(base: str, *, lectura_de_produccion: bool = False) -> None:
    """El control previo a CADA conexion de este modulo. Falla CERRADO, con el
    motivo y la variable que lo levanta, antes de abrir nada.

    1. La base: toda conexion de este modulo va a una base de tests
       (`es_base_de_test`). La unica excepcion es `lectura_de_produccion=True`,
       la lectura de `jax_memory` de la semilla de gobernanza, y solo con
       `VARIABLE_PERMISO_INSTANCIA_DE_PRODUCCION`: sin ella, ni leer.
    2. El puerto (el de `_parametros_de_conexion()`, con su mismo default 3306):
       ilegible o fuera de 1..65535 es un error; uno de `PUERTOS_DE_PRODUCCION`
       fuera de CI exige la misma variable. En CI cada job trae su propio
       contenedor (ver `_en_ci`), asi que ahi no aplica.
    """
    permiso = _permiso_instancia_de_produccion()
    if lectura_de_produccion:
        if base != BASE_DE_PRODUCCION:
            raise BaseDeTestInvalida(f"la lectura de produccion es solo de {BASE_DE_PRODUCCION!r}, no de {base!r}")
        if not permiso:
            raise BaseDeTestInvalida(
                f"la suite iba a abrir una conexion a {BASE_DE_PRODUCCION!r}, la base de PRODUCCION. "
                f"No se hace sin permiso explicito: exporta {VARIABLE_PERMISO_INSTANCIA_DE_PRODUCCION}=1 "
                f"si de verdad quieres que la semilla de gobernanza lea produccion (solo lectura).")
    elif not es_base_de_test(base):
        raise BaseDeTestInvalida(
            f"{base!r} no es una base de tests ({BASE_COMPARTIDA!r} o {BASE_COMPARTIDA + '_<sufijo>'!r}): "
            f"esta suite no abre conexiones a otra base, y esto no lo levanta ninguna variable.")
    crudo = os.environ.get("JAX_DB_PORT", "3306")
    try:
        puerto = int(crudo)
    except ValueError:
        puerto = 0
    if not 1 <= puerto <= 65535:
        raise BaseDeTestInvalida(f"JAX_DB_PORT={crudo!r} no es un puerto valido (1..65535): no se conecta a ciegas.")
    if puerto in PUERTOS_DE_PRODUCCION and not _en_ci() and not permiso:
        raise BaseDeTestInvalida(
            f"JAX_DB_PORT={puerto} es la MariaDB de PRODUCCION (3306 y 3308) y esto no es CI: la suite "
            f"crea y borra bases ahi. Usa un contenedor desechable en otro puerto, o exporta "
            f"{VARIABLE_PERMISO_INSTANCIA_DE_PRODUCCION}=1 si de verdad quieres correr contra esa instancia.")


def _parametros_de_conexion() -> dict:
    """Host, puerto y credenciales de la MariaDB, del entorno (/etc/jax/.env).
    El NOMBRE de la base no sale de acá: lo elige quien llama.

    `connect_timeout` NO sale de acá aunque sea un parámetro de conexión:
    mismo criterio que `jax` -- el kwarg va ESCRITO en la llamada, no
    escondido en un `**dict`, para que no se pierda en una indirección.
    DIVERGENCIA DELIBERADA con jax: el tripwire puntual que lo exige ahí
    (`tests/test_aiomysql_connect_timeout_tripwire.py`) sólo existe en ese
    repo; el criterio es el mismo en los dos."""
    return {
        "host": os.environ.get("JAX_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("JAX_DB_PORT", "3306")),
        "user": os.environ.get("JAX_DB_USER", ""),
        "password": os.environ.get("JAX_DB_PASSWORD", ""),
    }


async def _columnas_copiables(cur, esquema: str, tabla: str) -> list[str]:
    """Columnas de `esquema.tabla` que se pueden nombrar en un INSERT --
    todas MENOS las GENERATED (`EXTRA` trae 'STORED GENERATED' o 'VIRTUAL
    GENERATED'). MariaDB rechaza un valor explícito para una columna
    generada con el error 1906 (`The value specified for generated column
    'col' in table 't' has been ignored` -- texto EXACTO de MariaDB,
    verificado en vivo, no el de MySQL, que es otro), y
    `SELECT *`/`INSERT INTO t SELECT * FROM t2` la
    incluye igual que cualquier otra -- fix round 4 de Task 4
    (descartar-pipelines, 2026-09-22, Ruling 18/19): `jacobs_pipelines.visible`
    (GENERATED VIRTUAL, columna que agrega `jax`) es la primera columna
    generada que pasa por acá; sin este filtro, clonar una base de sesión
    desde una plantilla que tuviera aunque sea UNA fila en `jacobs_pipelines`
    rompía `asegurar_base_de_test()` con un 1906 -- no es hipotético, es la
    MISMA plantilla compartida que usan todas las sesiones. Mismo arreglo
    que jax aplicó a su propio `base_de_test.py` el mismo día (espejo
    mínimo, ver el docstring del módulo: cada repo con su conector local).
    Orden por ORDINAL_POSITION: no importa para la corrección (los nombres
    van explícitos en las dos listas del INSERT), pero mantiene el SQL
    generado legible si algo lo imprime en un log."""
    await cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND EXTRA NOT LIKE '%%GENERATED%%' "
        "ORDER BY ORDINAL_POSITION",
        (esquema, tabla),
    )
    return [fila[0] for fila in await cur.fetchall()]


async def _copiar_filas(cur, esquema_origen: str, esquema_destino: str, tabla: str) -> None:
    """`INSERT INTO destino.tabla (cols) SELECT cols FROM origen.tabla` con
    una lista EXPLÍCITA de columnas no generadas (`_columnas_copiables`) --
    nunca `SELECT *`. Función propia y testeable aparte de `_clonar_esquema`
    (que decide CUÁNDO copiar, por el corte de `FILAS_MAXIMAS_A_COPIAR`;
    esta función sólo sabe copiar)."""
    columnas = await _columnas_copiables(cur, esquema_origen, tabla)
    lista = ", ".join(f"`{c}`" for c in columnas)
    await cur.execute(
        f"INSERT INTO `{esquema_destino}`.`{tabla}` ({lista}) "
        f"SELECT {lista} FROM `{esquema_origen}`.`{tabla}`"
    )


async def _copiar_triggers(cur, esquema_origen: str, esquema_destino: str) -> int:
    """Clone trigger behavior onto the destination's copied tables.

    The trigger body and per-trigger SQL mode come from information_schema.
    The source DEFINER is intentionally not copied: isolated test databases
    must execute as the configured test principal, not depend on a production
    account existing in the test server.
    """
    await cur.execute(
        "SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE, ACTION_TIMING, "
        "EVENT_MANIPULATION, ACTION_STATEMENT, SQL_MODE, "
        "CHARACTER_SET_CLIENT, COLLATION_CONNECTION "
        "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s "
        "ORDER BY EVENT_OBJECT_TABLE, EVENT_MANIPULATION, ACTION_TIMING, ACTION_ORDER",
        (esquema_origen,),
    )
    triggers = await cur.fetchall()
    if not triggers:
        return 0

    await cur.execute(
        "SELECT @@SESSION.sql_mode, @@SESSION.character_set_client, "
        "@@SESSION.collation_connection")
    original_sql_mode, original_charset, original_collation = await cur.fetchone()
    copied = 0
    try:
        for (trigger_name, table_name, timing, event, statement, sql_mode,
             character_set, collation) in triggers:
            identifiers = (trigger_name, table_name)
            if any(not isinstance(value, str) or not value or len(value) > 64
                   or "`" in value for value in identifiers):
                raise BaseDeTestInvalida("la plantilla tiene identificadores de trigger inválidos")
            if timing not in {"BEFORE", "AFTER"} or event not in {"INSERT", "UPDATE", "DELETE"}:
                raise BaseDeTestInvalida("la plantilla tiene un tipo de trigger no soportado")
            if not isinstance(statement, str) or not statement.strip():
                raise BaseDeTestInvalida("la plantilla tiene un trigger sin cuerpo")
            if not isinstance(character_set, str) or not re.fullmatch(r"[A-Za-z0-9_]+", character_set):
                raise BaseDeTestInvalida("la plantilla tiene un charset de trigger inválido")
            if not isinstance(collation, str) or not re.fullmatch(r"[A-Za-z0-9_]+", collation):
                raise BaseDeTestInvalida("la plantilla tiene una collation de trigger inválida")
            if not isinstance(sql_mode, str):
                raise BaseDeTestInvalida("la plantilla tiene un SQL mode de trigger inválido")
            await cur.execute("SET SESSION sql_mode=%s", (sql_mode,))
            await cur.execute(f"SET NAMES {character_set} COLLATE {collation}")
            # Trigger names and table names are metadata identifiers validated
            # above; the destination is validated by es_base_de_test().
            ddl = (
                f"CREATE TRIGGER `{esquema_destino}`.`{trigger_name}` {timing} {event} "
                f"ON `{esquema_destino}`.`{table_name}` FOR EACH ROW {statement}"
            )
            await cur.execute(ddl)
            copied += 1
    finally:
        await cur.execute("SET SESSION sql_mode=%s", (original_sql_mode,))
        if (isinstance(original_charset, str) and re.fullmatch(r"[A-Za-z0-9_]+", original_charset)
                and isinstance(original_collation, str)
                and re.fullmatch(r"[A-Za-z0-9_]+", original_collation)):
            await cur.execute(f"SET NAMES {original_charset} COLLATE {original_collation}")
    return copied


async def _clonar_esquema(nombre: str) -> int:
    """Crea `nombre` y le copia el ESQUEMA (no los datos) de la plantilla.
    Devuelve cuántas tablas copió. Idempotente: si la base ya existe, no
    toca nada y devuelve -1."""
    import aiomysql  # import perezoso: la mayoría de los tests no crea bases

    _verificar_que_no_es_produccion(nombre)
    if nombre == BASE_COMPARTIDA:
        return -1
    exigir_conexion_permitida(nombre)

    # DIVERGENCIA DELIBERADA con jax: ver la nota en _dropear_base_de_sesion.
    from db_connect_config import db_connect_timeout_seconds

    conn = await aiomysql.connect(
        db=BASE_PLANTILLA, autocommit=True,
        connect_timeout=db_connect_timeout_seconds(),
        **_parametros_de_conexion())
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME=%s", (nombre,))
            if (await cur.fetchone())[0]:
                return -1
            # El nombre está validado por `nombre_base_de_test()`/`es_base_de_test()`
            # contra `[a-z0-9_]`: un identificador no se pasa como parámetro.
            if not es_base_de_test(nombre):
                raise BaseDeTestInvalida(f"{nombre!r} no es una base de tests")
            await cur.execute(f"CREATE DATABASE `{nombre}`")
            await cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME",
                (BASE_PLANTILLA,))
            tablas = [fila[0] for fila in await cur.fetchall()]
            copiadas = 0
            # Sin chequeo de FKs mientras se copia: el orden alfabético no
            # respeta las dependencias y no hay datos que validar.
            await cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for tabla in tablas:
                await cur.execute(f"SHOW CREATE TABLE `{BASE_PLANTILLA}`.`{tabla}`")
                ddl = (await cur.fetchone())[1]
                ddl = ddl.replace(
                    f"CREATE TABLE `{tabla}`", f"CREATE TABLE `{nombre}`.`{tabla}`", 1)
                await cur.execute(ddl)
                copiadas += 1
                # Los datos de las tablas chicas (catálogo y gobernanza). El
                # COUNT es exacto a propósito: `information_schema.TABLE_ROWS`
                # es una ESTIMACIÓN de InnoDB, y decidir con una estimación es
                # suponer. Se cuenta una vez, al crear la base.
                await cur.execute(f"SELECT COUNT(*) FROM `{BASE_PLANTILLA}`.`{tabla}`")
                filas = (await cur.fetchone())[0]
                if filas and filas <= FILAS_MAXIMAS_A_COPIAR:
                    await _copiar_filas(cur, BASE_PLANTILLA, nombre, tabla)
            # Las VISTAS, después de las tablas que miran. `motor_resolved` es
            # una: sin ella el catálogo de motores queda sin tests (mismo
            # hallazgo que en jax, 2026-09-17). Su definición viene calificada
            # con el esquema de la plantilla y hay que reapuntarla a la base
            # nueva.
            await cur.execute(
                "SELECT TABLE_NAME, VIEW_DEFINITION FROM information_schema.VIEWS "
                "WHERE TABLE_SCHEMA=%s", (BASE_PLANTILLA,))
            for vista, definicion in await cur.fetchall():
                definicion = definicion.replace(
                    f"`{BASE_PLANTILLA}`.", f"`{nombre}`.")
                await cur.execute(f"CREATE VIEW `{nombre}`.`{vista}` AS {definicion}")
                copiadas += 1
            await cur.execute("SET FOREIGN_KEY_CHECKS=1")

            # Los triggers forman parte del esquema ejecutable: copiar sus
            # cuerpos y SQL_MODE preserva las mismas restricciones en cada
            # base de sesión. Rutinas y eventos todavía no tienen una ruta de
            # clonación segura y, por eso, siguen fallando en voz alta.
            await _copiar_triggers(cur, BASE_PLANTILLA, nombre)
            for tipo, tabla_is, columna in (
                ("rutinas", "ROUTINES", "ROUTINE_SCHEMA"),
                ("eventos", "EVENTS", "EVENT_SCHEMA"),
            ):
                await cur.execute(
                    f"SELECT COUNT(*) FROM information_schema.{tabla_is} "
                    f"WHERE {columna}=%s", (BASE_PLANTILLA,))
                cuantos = (await cur.fetchone())[0]
                if cuantos:
                    raise BaseDeTestInvalida(
                        f"{BASE_PLANTILLA} tiene {cuantos} {tipo} y este clonador "
                        f"no los copia: la base de sesión saldría distinta de la "
                        f"plantilla. Agregalos a `_clonar_esquema()` antes de seguir."
                    )
            return copiadas
    finally:
        conn.close()


def _bootstrap_jax_schema_para_base_de_test(nombre: str) -> None:
    """Load the existing JAX schema source into an isolated test database.

    ``projects`` is JAX-owned and is the FK parent for migration 003.  The
    platform migration runner deliberately does not create or parse that
    schema in production.  CI already loads this exact source before the
    platform suite; isolated local databases need the same prerequisite after
    cloning a legacy template that predates ``projects``.
    """
    _verificar_que_no_es_produccion(nombre)
    exigir_conexion_permitida(nombre)
    configured_root = os.environ.get("JAX_REPO_PATH", "").strip()
    root = Path(configured_root)
    if not configured_root or not root.is_absolute():
        raise BaseDeTestInvalida(
            "JAX_REPO_PATH absoluto es requerido para bootstrap del esquema JAX en tests"
        )
    schema = (root.resolve() / "jax_memory_schema.sql").resolve()
    try:
        schema.relative_to(root.resolve())
    except ValueError as exc:
        raise BaseDeTestInvalida("el esquema JAX escapó JAX_REPO_PATH") from exc
    if not schema.is_file():
        raise BaseDeTestInvalida(f"falta el esquema JAX requerido: {schema}")

    sql = schema.read_text(encoding="utf-8")
    # Igual que el job DB de CI: no se parte SQL en Python; MariaDB interpreta
    # el archivo entero. Solo se elimina el encabezado que seleccionaría la
    # base de producción, y se conserva cada sentencia de esquema de JAX.
    sql, replacements = re.subn(
        r"(?ms)^CREATE DATABASE IF NOT EXISTS jax_memory\s+CHARACTER SET.*?;\s*^USE jax_memory;\s*",
        "",
        sql,
        count=1,
    )
    if replacements != 1:
        raise BaseDeTestInvalida(
            "el encabezado de jax_memory_schema.sql cambió; actualizar el bootstrap de tests"
        )
    # Migration 003 has a parent in both repositories: projects from JAX and
    # jax_tenants from the platform. It belongs in the platform migration
    # chain after jax_tenants exists, never in this standalone JAX bootstrap.
    if "CREATE TABLE `jax_project_scope`" in sql:
        raise BaseDeTestInvalida(
            "jax_memory_schema.sql no debe incluir migration 003; "
            "la corre el runner de plataforma después de jax_tenants"
        )
    # The template can already contain some JAX tables while still lacking
    # `projects` (the historical state this test bootstrap repairs). Keep the
    # JAX source authoritative and make only its CREATEs idempotent; this is
    # not a drift repairer and does not alter existing definitions.
    sql = re.sub(r"(?m)^CREATE TABLE `", "CREATE TABLE IF NOT EXISTS `", sql)

    env = os.environ.copy()
    env["MYSQL_PWD"] = os.environ.get("JAX_DB_PASSWORD", "")
    result = subprocess.run(
        [
            "mysql",
            "--host", os.environ["JAX_DB_HOST"],
            "--port", os.environ["JAX_DB_PORT"],
            "--user", os.environ.get("JAX_DB_USER", ""),
            "--database", nombre,
        ],
        input=sql,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode:
        raise BaseDeTestInvalida(
            "el bootstrap del esquema JAX falló para la base aislada: "
            f"{result.stderr.strip()[:500]}"
        )


#: Archivos de `jax/memory/b9_migrations/` que YA aplica `db.migrations.run_migrations()`:
#: 001/002 por `_apply_jax_b9_core_migrations` (lee estos DOS archivos tal cual, ver
#: `_jax_b9_core_migration_statements()`) y 003 por `_apply_jax_project_authority_migration`,
#: que ejecuta el hook Python `project_authority_migrations.py` -- una copia deliberada,
#: mantenida por JAX, de `003_project_scope_authority.sql` (ver el docstring de ese hook).
#: Volver a aplicarlas acá no rompería nada (las tres son puro `IF NOT EXISTS`), pero sería
#: trabajo de más en cada sesión y una tercera fuente de la misma lista. Todo lo que la
#: carpeta tenga FUERA de este conjunto es DDL que el propio README de esa carpeta declara
#: que NO se aplica sola -- justo lo que este bootstrap de test tiene que ponerse al día,
#: descubriéndolo por GLOB y no por una lista fija (ver `aplicar_migraciones_b9_restantes`).
_MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS = frozenset({
    "001_b9_shared_memory.sql",
    "002_b9_hardening.sql",
    "003_project_scope_authority.sql",
})

#: Registro de qué migración B9 (de las que quedan fuera del conjunto de arriba) ya se
#: aplicó a ESTA base física. Vive SOLO en la base de tests -- producción no corre este
#: módulo -- y es lo que permite reusar una base entre corridas sin reventar un DDL no
#: idempotente. NI 004 NI 006 son idempotentes de punta a punta (verificado contra jax
#: master 2026-09-27, leyendo cada sentencia, no de memoria):
#:   - 004 (`004_tenant_legacy_binding.sql`): `ALTER TABLE memory_legacy_bindings
#:     ADD COLUMN tenant_id ...` SIN `IF NOT EXISTS` ("Duplicate column name" la segunda
#:     vez); el `DROP PRIMARY KEY` de esa misma tabla asume que la PK vieja sigue ahí
#:     (la segunda vez ya no está: "Can't DROP PRIMARY KEY; check that it exists"); y
#:     `ALTER TABLE memory_objects DROP INDEX uq_memory_legacy_binding` (la segunda vez
#:     ese índice ya no existe -- lo reemplazó `uq_memory_legacy_binding_tenant` en la
#:     misma sentencia, MINOR-D de la auditoría del PR #164, ronda 2: esta viñeta lo
#:     tenía puesto, por error, bajo 006).
#:   - 006 (`006_memory_jobs.sql`): `CREATE TRIGGER memory_revision_tenant_compat` y
#:     `ADD CONSTRAINT fk_memory_revision_tenant` tampoco traen `IF NOT EXISTS` ("trigger
#:     ya existe" / "constraint duplicada" la segunda vez).
#: Con el registro, cada archivo corre UNA sola vez por base física, para siempre --
#: mismo criterio de fondo que `axioma_migracion_de_datos` en `db/migrations.py`, pero
#: acotado al arnés de tests: una migración de JAX no es una migración de datos de este
#: repo, y no comparte esa tabla.
CREATE_TABLA_MIGRACIONES_B9_DE_TEST = """
CREATE TABLE IF NOT EXISTS _test_b9_migraciones_aplicadas (
  archivo VARCHAR(191) NOT NULL PRIMARY KEY,
  sha256 CHAR(64) NULL,
  aplicada_at DATETIME DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

#: Una base creada por una versión anterior de este módulo (antes de MAJOR-A de la
#: auditoría del PR #164, ronda 2) tiene la tabla de arriba SIN esta columna --
#: `ADD COLUMN IF NOT EXISTS` la repone, idempotente, sin tocar las filas que ya tenía
#: (quedan con `sha256 IS NULL`, que `aplicar_migraciones_b9_restantes` trata como "no
#: coincide" -- ver más abajo: no hay forma de confiar en una fila sin hash).
ALTER_TABLA_MIGRACIONES_B9_DE_TEST_AGREGA_SHA256 = """
ALTER TABLE _test_b9_migraciones_aplicadas ADD COLUMN IF NOT EXISTS sha256 CHAR(64) NULL;
"""

#: El manifiesto (ver `_manifiesto_b9_de_produccion()`) vive en este archivo del propio
#: repo -- versionado, no generado -- para que declarar una migración quede en el mismo
#: PR que la usa.
RUTA_MANIFIESTO_B9_DE_PRODUCCION = Path(__file__).resolve().parent / "b9_migraciones_en_produccion.json"


def _sha256_de_archivo(ruta: Path) -> str:
    """El hash del CONTENIDO exacto del archivo, en hex minúscula -- mismo formato que
    `sha256sum` en la terminal, para que declarar una entrada del manifiesto sea copiar
    y pegar la salida de ese comando, sin transformación."""
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _manifiesto_b9_de_produccion() -> dict:
    """Qué migraciones de `jax/memory/b9_migrations/` -- de las que `run_migrations()` NO
    cubre -- ya están aplicadas A MANO en `jax_memory` de PRODUCCIÓN, con fecha, quién,
    CÓMO se verificó y el `sha256` exacto del `.sql` que se aplicó
    (`backend/b9_migraciones_en_produccion.json`, versionado en el repo).

    **Por qué un manifiesto y no "aplicá lo que el glob encuentre".** La primera versión de
    este bootstrap confiaba en que cualquier `.sql` de esa carpeta, más allá de 001-003, era
    seguro de aplicar en la base de tests porque "ya estaba en jax master". Eso da vuelta la
    carga de la prueba: el README de esa carpeta es explícito en que la cadena NO se aplica
    sola en ningún lado, ni siquiera en JAX -- que un archivo esté en el repo no dice que
    ya pasó por el flujo revisado de producción (Ruling de la auditoría adversarial del
    PR #164, MAJOR-2, 2026-09-27). Con el manifiesto, la pregunta que decide no es "¿existe
    el archivo?" sino "¿alguien ya lo aplicó en producción y lo declaró acá?" -- y si la
    respuesta es no, la suite lo dice en rojo (`aplicar_migraciones_b9_restantes`) en vez de
    aplicarlo a ciegas contra una base de test.

    **Por qué también el hash, y no sólo el nombre (MAJOR-A, misma auditoría, ronda 2).**
    Declarar "004_tenant_legacy_binding.sql: aplicada" no dice NADA sobre qué contenido
    tenía ese archivo cuando alguien lo aplicó y lo verificó -- si `jax` edita el archivo
    después (mismo nombre, otro DDL) y nadie vuelve a mirar el manifiesto, la suite
    aplicaría en la base de tests un contenido que ningún humano revisó contra producción.
    El nombre declara "yo sé de este archivo"; el hash declara "y es ESTE, byte a byte".
    """
    if not RUTA_MANIFIESTO_B9_DE_PRODUCCION.is_file():
        raise BaseDeTestInvalida(
            f"falta el manifiesto de migraciones B9 de producción: "
            f"{RUTA_MANIFIESTO_B9_DE_PRODUCCION}"
        )
    datos = json.loads(RUTA_MANIFIESTO_B9_DE_PRODUCCION.read_text(encoding="utf-8"))
    return {clave: valor for clave, valor in datos.items() if not clave.startswith("_")}


async def aplicar_migraciones_b9_restantes() -> None:
    """Deja en la base de ESTA sesión toda la cadena B9 de JAX, más allá de 001-003 (lo
    que `run_migrations()` ya cubre).

    **Por qué existe.** El README de `jax/memory/b9_migrations/` es explícito: esa cadena
    "is not executed by application import or worker startup. Apply it only through the
    repository's reviewed database migration workflow" -- ni siquiera 001-003 se aplican
    solos en JAX; en `jax-platform` sí se aplican, pero a través de `run_migrations()`, que
    deliberadamente sólo trae 001-003 (ver el comentario de esa función). En PRODUCCIÓN
    (`jax_memory`) la cadena completa, 004 y 006 incluidos, ya está aplicada por fuera de
    ese flujo (verificado 2026-09-27, ver `backend/b9_migraciones_en_produccion.json`).
    Una base de TEST que sólo corre `run_migrations()` se queda atrás de esa realidad, y
    cualquier código que dependa de lo que 004/006 agregan
    (`jax.memory.b9_mariadb._retrieve_scoped`, tras jax#279, lee `r.tenant_id` de
    `memory_revisions`) revienta en la suite con "Unknown column 'r.tenant_id'" -- medido
    en esta rama antes de este cambio: 34 tests de chat/adjuntos/facetas/shadow en rojo,
    todos con `MEMORY_UNAVAILABLE` (503) porque `api/chat.py` envuelve esa falla de B9.

    **Por qué NO va en `run_migrations()`.** Esa función es el arranque de PRODUCCIÓN
    (la corre el lifespan de `main.py` contra `jax_memory`), y el README de arriba es una
    decisión de diseño de JAX sobre qué se aplica solo y qué no -- diferirla es de ese
    repo, no de este bootstrap de test. Esta función es EXCLUSIVA del arnés de tests: sólo
    hace que la base de prueba deje de mentir sobre lo que producción ya tiene.

    **Descubrimiento por GLOB, contra un MANIFIESTO de NOMBRE + CONTENIDO -- no una
    lista fija ni confianza ciega.** Un archivo nuevo (007, ...) que JAX agregue mañana
    se recoge solo, en orden alfabético, sin que nadie tenga que tocar este módulo para
    que la suite se entere de que existe -- pero no se aplica solo: si no está declarado
    en `backend/b9_migraciones_en_produccion.json`, o si está declarado pero su `sha256`
    ya no coincide (alguien lo editó después de que un humano lo verificó), esta función
    revienta con un mensaje que dice exactamente qué falta y qué hacer (ver
    `_manifiesto_b9_de_produccion`). Así, un archivo nuevo o cambiado nunca se aplica a
    ciegas Y nunca se pierde en silencio (lo que ya pasó una vez: 004 y 006 llevaban
    semanas en `jax` master sin que nada de acá los aplicara).

    **Cuándo llamarla.** DESPUÉS de que `run_migrations()` ya corrió: `memory_objects`,
    `memory_revisions` y `memory_legacy_bindings` (que 004/006 alteran) son de 001/002.
    `tests/conftest.py` la llama desde el fixture `client`, en el mismo punto donde ya se
    asegura el esquema de Jacobs -- ahí el lifespan de la app (que corre `run_migrations()`)
    ya terminó de arrancar.
    """
    from db.connection import get_pool
    from db.migrations import _jax_b9_migration_root, _split_jax_b9_sql

    nombre = os.environ.get(VARIABLE_DE_LA_BASE)
    if not es_base_de_test(nombre):
        raise BaseDeTestInvalida(
            f"{VARIABLE_DE_LA_BASE}={nombre!r} no es una base de tests: no se le aplican "
            "acá las migraciones B9 de JAX que producción reserva para su propio flujo "
            "revisado."
        )
    # Antes de leer nada y de pedir el pool: estas migraciones ESCRIBEN por la
    # conexion de `db.connection`, que usa el mismo `JAX_DB_PORT`.
    exigir_conexion_permitida(nombre)

    directorio = _jax_b9_migration_root()
    pendientes = sorted(
        p.name for p in directorio.glob("*.sql")
        if p.name not in _MIGRACIONES_B9_YA_CUBIERTAS_POR_RUN_MIGRATIONS
    )
    if not pendientes:
        return

    # Contra el manifiesto ANTES de conectar a nada: un archivo no declarado es un error
    # de configuración/proceso, no algo que se resuelve abriendo una conexión.
    manifiesto = _manifiesto_b9_de_produccion()
    no_declaradas = [archivo for archivo in pendientes if archivo not in manifiesto]
    if no_declaradas:
        raise BaseDeTestInvalida(
            "migración B9 "
            + ", ".join(no_declaradas)
            + " no declarada como aplicada en producción: aplícala en producción "
            "siguiendo docs/runbooks/despliegue.md (sección 'Migraciones B9 adicionales') "
            f"y agregala a {RUTA_MANIFIESTO_B9_DE_PRODUCCION.name} antes de que la suite "
            "la use."
        )

    # MAJOR-A de la auditoría del PR #164 (ronda 2): el manifiesto declara CONTENIDO, no
    # sólo nombre. Se calcula ANTES de `get_pool()`, junto con la validación de arriba --
    # un contenido que cambió es el mismo tipo de error de proceso que un archivo no
    # declarado, y se detecta sin abrir ninguna conexión.
    hashes_verificados: dict[str, str] = {}
    for archivo in pendientes:
        esperado = manifiesto[archivo].get("sha256")
        real = _sha256_de_archivo(directorio / archivo)
        if real != esperado:
            raise BaseDeTestInvalida(
                f"el contenido de {archivo} cambió respecto de lo aplicado en producción "
                f"(sha256 real {real!r}, el manifiesto declara {esperado!r}): "
                "re-verificalo contra jax_memory siguiendo "
                "docs/runbooks/despliegue.md (sección 'Migraciones B9 adicionales') y "
                f"actualizá el hash en {RUTA_MANIFIESTO_B9_DE_PRODUCCION.name} antes de "
                "que la suite lo use."
            )
        hashes_verificados[archivo] = real

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # MINOR-4 de la auditoría del PR #164: `get_pool()` cachea un pool por loop de
            # asyncio (ver `db/connection.py`) -- si algún día un test reusara un loop con
            # un pool ya abierto contra OTRA base, `os.environ[VARIABLE_DE_LA_BASE]` mentiría
            # sobre la conexión real. Se verifica la base de ESTA conexión, no la variable.
            await cur.execute("SELECT DATABASE()")
            (base_real,) = await cur.fetchone()
            if not es_base_de_test(base_real):
                raise BaseDeTestInvalida(
                    f"la conexión de este pool apunta a {base_real!r}, que no es una base "
                    f"de tests (JAX_DB_NAME decía {nombre!r}): no se aplican acá las "
                    "migraciones B9 de JAX."
                )

            await cur.execute(CREATE_TABLA_MIGRACIONES_B9_DE_TEST)
            await cur.execute(ALTER_TABLA_MIGRACIONES_B9_DE_TEST_AGREGA_SHA256)
            for archivo in pendientes:
                esperado = hashes_verificados[archivo]
                await cur.execute(
                    "SELECT sha256 FROM _test_b9_migraciones_aplicadas WHERE archivo=%s",
                    (archivo,),
                )
                fila = await cur.fetchone()
                if fila is not None:
                    (guardado,) = fila
                    if guardado != esperado:
                        # MAJOR-A de la auditoría del PR #164 (ronda 2): esta base ya
                        # aplicó `archivo` con un contenido DISTINTO del que el
                        # manifiesto declara hoy (o con una fila vieja, de antes de que
                        # esta columna existiera, que quedó en NULL -- tampoco es de
                        # fiar). No hay forma segura de "actualizar" un DDL ya corrido
                        # sin saber qué cambió: la base queda inválida para ese archivo.
                        raise BaseDeTestInvalida(
                            f"la base de esta sesión ya tiene {archivo} aplicado con "
                            f"otro contenido (sha256 guardado {guardado!r}, el "
                            f"manifiesto de hoy declara {esperado!r}): recreala "
                            f"(borrala con DROP DATABASE `{base_real}` y corré la "
                            "suite de nuevo con el mismo u otro JAX_TEST_DB_SUFIJO)."
                        )
                    continue
                script = (directorio / archivo).read_text(encoding="utf-8")
                for numero, statement in enumerate(_split_jax_b9_sql(script), start=1):
                    try:
                        await cur.execute(statement)
                    except Exception as exc:
                        # MINOR-3 de la auditoría del PR #164: ni 004 ni 006 son
                        # idempotentes de punta a punta (ver el docstring de
                        # `CREATE_TABLA_MIGRACIONES_B9_DE_TEST`) y esta función no
                        # registra la migración como aplicada hasta que TERMINÓ -- una
                        # falla a mitad dejaría la base con un DDL parcial que un
                        # reintento no puede completar (la próxima corrida repetiría
                        # desde la sentencia 1 y chocaría con lo que sí llegó a
                        # aplicarse). No hay rollback posible: MariaDB hace commit
                        # implícito por sentencia DDL. La única salida segura es
                        # recrear la base de la sesión.
                        raise BaseDeTestInvalida(
                            f"la migración B9 {archivo} falló en su sentencia "
                            f"#{numero} ({statement[:200]!r}): {exc}. La base de esta "
                            "sesión quedó con un DDL parcial de esa migración y NO es "
                            "segura de reintentar tal cual -- recreala (borrala con "
                            f"DROP DATABASE `{base_real}` y corré la suite de "
                            "nuevo con el mismo u otro JAX_TEST_DB_SUFIJO)."
                        ) from exc
                await cur.execute(
                    "INSERT INTO _test_b9_migraciones_aplicadas (archivo, sha256) "
                    "VALUES (%s, %s)",
                    (archivo, esperado),
                )
        await conn.commit()


async def _tabla_existe_en_base(nombre: str, tabla: str) -> bool:
    exigir_conexion_permitida(nombre)
    import aiomysql
    from db_connect_config import db_connect_timeout_seconds

    conn = await aiomysql.connect(
        db=nombre, autocommit=True,
        connect_timeout=db_connect_timeout_seconds(),
        **_parametros_de_conexion())
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (tabla,))
            return await cur.fetchone() is not None
    finally:
        conn.close()


def asegurar_base_de_test(nombre: str | None = None) -> str:
    """Deja lista la base de esta sesión: la crea con el esquema de la
    plantilla si no existía, y le corre `run_migrations()` del repo.

    Sin `JAX_DB_HOST`, o bajo `JAX_CI_NO_DB=1` (ver `_en_ci_sin_db()`), no
    hay MariaDB a mano (los jobs de tests puros del CI, y cualquier corrida
    local que simule ese modo): no se crea nada y no es un error.
    """
    import asyncio

    nombre = nombre or nombre_base_de_test()
    _verificar_que_no_es_produccion(nombre)
    if nombre == BASE_COMPARTIDA or _en_ci_sin_db() or not os.environ.get("JAX_DB_HOST"):
        return nombre
    exigir_conexion_permitida(nombre)  # antes de tocar el entorno o abrir nada

    anterior = os.environ.get(VARIABLE_DE_LA_BASE)
    os.environ[VARIABLE_DE_LA_BASE] = nombre
    try:
        asyncio.run(_clonar_esquema(nombre))
        # En CI el workflow carga este mismo esquema antes de pytest.  La
        # plantilla local puede ser anterior a `projects`, así que se repone
        # únicamente en bases aisladas y sólo si el padre de FK no está.
        if nombre != BASE_COMPARTIDA and not asyncio.run(_tabla_existe_en_base(nombre, "projects")):
            _bootstrap_jax_schema_para_base_de_test(nombre)
        # El esquema propio del repo, por SU camino. Corre siempre (no sólo al
        # crear): la plantilla puede estar atrasada respecto de esta rama.
        # DIVERGENCIA DELIBERADA con jax: allá es
        # `from jacobs import store; await store.init_tables()`. Acá el repo
        # no tiene `jacobs`: su propio camino de esquema es
        # `db.migrations.run_migrations()`, el mismo que corre el lifespan de
        # `main.py` (línea 158) contra la base real.
        from db.migrations import run_migrations  # import perezoso: arrastra el pool
        asyncio.run(run_migrations())
    except Exception:
        if anterior is None:
            os.environ.pop(VARIABLE_DE_LA_BASE, None)
        else:
            os.environ[VARIABLE_DE_LA_BASE] = anterior
        raise
    return nombre
