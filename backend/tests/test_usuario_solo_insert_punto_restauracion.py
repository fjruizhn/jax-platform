"""C2 (Esquema, diseño 2026-09-22): el usuario que escribe en
ejecutor_punto_restauracion es de SOLO INSERT.

**Reescrito 2026-09-22 (PR #149, corrección sobre CI).** La versión anterior
dependía de DOS cosas que el job `backend-tests-con-db` de CI no tiene: el
worktree local `~/worktrees/cs-verificador` (de donde extraía el SQL de la
cabecera de `claude-skills/bin/verificar-punto-restauracion.sh`) y
`sudo -n docker` más la red Docker local de `mariadb-12-3-jax` para levantar
un MariaDB efímero. En CI ninguna de las dos existe: los 2 tests de este
archivo se saltaban, por encima del piso de `MAX_SKIPS = 1` de
`.github/workflows/policy.yml` (medido: `colectados=2588 passed=2586
skipped=2`). Reproducido también fuera de CI, el mismo día: con Docker
presente en hall9000 pero con `~/worktrees/cs-verificador` en medio de una
edición de otra sesión (sus marcadores `SQL_CREAR_USUARIO_INICIO/FIN`
temporalmente ausentes del árbol de trabajo), `_extraer_sentencias_sql()`
también devolvía `[]` y los 2 tests se saltaban igual -- la dependencia era
frágil en las dos direcciones, no solo en CI.

**La corrección.** Las sentencias EXACTAS ya no se extraen de otro repo:
viven en `backend/db/usuario_punto_restauracion.sql`, en ESTE repo, junto al
esquema que describen (ver ese archivo para la explicación completa de sus
marcadores `{USUARIO}`/`{BASE}`/`{HOST}`/`__CONTRASENA__`). Este test:

1. Lee esa plantilla.
2. Calcula `{HOST}` en vivo contra SU PROPIA conexión (ver
   `_host_de_esta_conexion()` más abajo para la elección entre `USER()` y
   `CURRENT_USER()`).
3. Sustituye `{BASE}` por la base de ESTA sesión de test (`JAX_DB_NAME` --
   nunca `jax_memory`, que sería producción en el mismo servidor físico que
   usan los tests locales).
4. Sustituye `{USUARIO}` por un nombre ÚNICO por corrida (el usuario es
   GLOBAL al servidor, `mysql.user`, no por base de datos).
5. Aplica la migración REAL de este repo
   (`_asegurar_forma_de_ejecutor_punto_restauracion`, no un ALTER copiado a
   mano) para dejar la tabla en su forma final.
6. Ejecuta las sentencias ya sustituidas y comprueba -- de verdad, no
   leyendo `SHOW GRANTS` -- que ESE usuario puede INSERT y no puede UPDATE,
   DELETE ni SELECT (Principio VII: un freno sin prueba no es freno).
7. Borra el usuario (y la fila de `ejecutor_host` que sembró) en un
   `finally`, corra o no el resto del test.

**Por qué esto SÍ corre en CI y el anterior no.** El job `backend-tests-con-
db` (`.github/workflows/policy.yml`) declara `JAX_DB_USER: root` contra su
propio contenedor MariaDB efímero -- root tiene `CREATE USER`/`GRANT OPTION`
de fábrica en la imagen oficial. Localmente, contra la MariaDB compartida de
hall9000, `JAX_DB_USER=jax_user` NO tiene `CREATE USER` en ningún alcance
(verificado 2026-09-22 con `SHOW GRANTS FOR CURRENT_USER()`: sólo `USAGE` en
`*.*` y `ALL PRIVILEGES` acotado a `jax_memory`/`jax_memory_test`/
`jax_memory_test_%`) -- por diseño: la cuenta de la app nunca tuvo por qué
poder crear usuarios. Por eso este test distingue el error 1227 de MariaDB
("Access denied ... CREATE USER privilege(s)") de cualquier otro: si aparece
y la corrida NO es CI, se salta con motivo visible (gap de entorno legítimo,
documentado, y CI no lo hereda porque ahí el usuario SÍ tiene el privilegio);
si aparece y la corrida SÍ es CI, revienta -- eso significaría que la premisa
de este archivo (root en CI) dejó de ser cierta, y saltarse en silencio
escondería el problema en vez de mostrarlo.
"""
from __future__ import annotations

import os
import re
import secrets
import uuid
from pathlib import Path

import aiomysql
import pymysql
import pytest

import conftest as _conftest
from base_de_test import _en_ci, _parametros_de_conexion
from db import migrations
from db_connect_config import db_connect_timeout_seconds

RUTA_SQL = Path(__file__).resolve().parent.parent / "db" / "usuario_punto_restauracion.sql"

# MariaDB: "Access denied; you need (at least one of) the CREATE USER
# privilege(s) for this operation" -- medido 2026-09-22 contra hall9000 con
# jax_user (ver docstring del módulo).
_ERRNO_SIN_CREATE_USER = 1227

# Mismo criterio que tests/test_el_juez_facet.py: la Regla 2 de conftest.py
# (JAX_CI_NO_DB=1) simula "configurado pero caído" -- pone JAX_DB_HOST a un
# valor con pinta de válido y confía en que cada test llegue a
# aiomysql.create_pool() para que _skip_on_db_access lo intercepte. Este
# archivo abre conexiones DIRECTO con aiomysql.connect()/pymysql.connect(),
# así que ninguna de las dos redes de conftest.py lo alcanza -- el guard
# pregunta lo mismo que conftest._CI_NO_DB pregunta (MariaDB reconocida como
# ausente por este runner), no "¿hay una variable puesta?".
_SIN_MARIADB = _conftest._CI_NO_DB or not os.environ.get("JAX_DB_HOST")
_RAZON_SIN_MARIADB = (
    _conftest._NO_DB_REASON if _conftest._CI_NO_DB
    else "sin MariaDB a mano no hay base contra la que aplicar el usuario"
)


def _leer_plantilla() -> str:
    return RUTA_SQL.read_text(encoding="utf-8")


def _sentencias(texto_sql: str) -> list[str]:
    """Las sentencias de la plantilla, ya sustituida, sin los comentarios
    `--` de la cabecera. Cada sentencia termina en ';' en el archivo."""
    sin_comentarios = "\n".join(
        linea for linea in texto_sql.splitlines() if not linea.strip().startswith("--")
    )
    return [s.strip() for s in sin_comentarios.split(";") if s.strip()]


def _sustituir(plantilla: str, *, usuario: str, base: str, host: str, contrasena: str) -> str:
    return (
        plantilla
        .replace("{USUARIO}", usuario)
        .replace("{BASE}", base)
        .replace("{HOST}", host)
        .replace("__CONTRASENA__", contrasena)
    )


async def _host_de_esta_conexion(cur) -> str:
    """El host LITERAL por el que MariaDB ve llegar a ESTA conexión -- no el
    patrón ya emparejado contra el que se le otorgó privilegio.

    `USER()` devuelve lo que el cliente mandó en el handshake tal como el
    servidor lo resolvió (medido 2026-09-22 contra hall9000: `jax_user`
    conectando por 127.0.0.1:3308 da `USER()='jax_user@172.30.5.1'`, la IP
    real del gateway Docker). `CURRENT_USER()` da la cuenta que MariaDB hizo
    calzar (`'jax_user@172.30.5.%'`, el patrón con el que se otorgó el
    privilegio) -- si esa cuenta estuviera dada de alta con host `'%'`,
    `CURRENT_USER()` devolvería `'%'` y el usuario nuevo nacería sin
    restricción real de host, sin que el test lo notara.

    El usuario que este test crea se va a conectar por la MISMA ruta de red
    que ESTA conexión (mismo proceso, mismo JAX_DB_HOST/JAX_DB_PORT), así
    que el host LITERAL de ahora es exactamente lo que hace falta -- y es lo
    que test_usuario_solo_insert_no_conecta_fuera_de_su_subred comprueba que
    quedó grabado, no un comodín."""
    await cur.execute("SELECT SUBSTRING_INDEX(USER(), '@', -1)")
    (host,) = await cur.fetchone()
    assert host, "USER() no devolvió un host: no se puede acotar el usuario nuevo"
    return host


class _NoSePuedeCrearUsuarios(RuntimeError):
    """El usuario configurado no tiene CREATE USER. En CI esto NO debe
    pasar nunca (ver el docstring del módulo) -- por eso quien atrapa esta
    excepción decide saltar o reventar según `_conftest._en_ci` de este
    módulo, no aquí."""


async def _crear_usuario_solo_insert(cur, *, base: str) -> tuple[str, str, str]:
    """Aplica la migración real, crea el usuario de SOLO INSERT con las
    sentencias EXACTAS de `usuario_punto_restauracion.sql`, y devuelve
    (usuario, contraseña, host) para que el llamador se conecte con él.

    Levanta `_NoSePuedeCrearUsuarios` si el usuario configurado no tiene
    privilegio -- error 1227 de MariaDB."""
    await cur.execute(migrations.CREATE_EJECUTOR_HOST)
    await cur.execute(migrations.CREATE_EJECUTOR_PUNTO_RESTAURACION)
    await migrations._asegurar_forma_de_ejecutor_punto_restauracion(cur)

    host = await _host_de_esta_conexion(cur)
    sufijo = uuid.uuid4().hex[:12]
    usuario = f"c2prueba_{sufijo}"
    contrasena = "efimero-" + secrets.token_hex(16)

    plantilla = _leer_plantilla()
    sustituida = _sustituir(plantilla, usuario=usuario, base=base, host=host, contrasena=contrasena)
    try:
        for sentencia in _sentencias(sustituida):
            await cur.execute(sentencia)
    except pymysql.err.OperationalError as exc:
        if exc.args[0] == _ERRNO_SIN_CREATE_USER:
            raise _NoSePuedeCrearUsuarios(str(exc)) from exc
        raise
    return usuario, contrasena, host


async def _borrar_usuario(cur, usuario: str, host: str) -> None:
    # DROP USER IF EXISTS: si CREATE falló a mitad (poco probable, pero el
    # borrado no debe reventar el `finally` de quien llama por eso).
    await cur.execute(f"DROP USER IF EXISTS '{usuario}'@'{host}'")


@pytest.mark.skipif(_SIN_MARIADB, reason=_RAZON_SIN_MARIADB)
async def test_usuario_solo_insert_puede_insert_pero_no_update_ni_delete_ni_select():
    base = os.environ["JAX_DB_NAME"]
    # Nunca un parámetro (MariaDB no acepta el nombre de la base como
    # parámetro en DDL/GRANT): validado antes de ir a un f-string.
    assert re.fullmatch(r"[A-Za-z0-9_]+", base), base
    assert base != "jax_memory", "la base de test resolvió a PRODUCCIÓN -- no se toca"

    sufijo_host = uuid.uuid4().hex[:10]
    nombre_host = f"c2prueba-{sufijo_host}"
    ip_ejecutor = f"203.0.113.{secrets.randbelow(254) + 1}"  # TEST-NET-3 (RFC 5737), nunca real
    puerto_ejecutor = 40000 + secrets.randbelow(20000)

    conn = await aiomysql.connect(
        db=base, connect_timeout=db_connect_timeout_seconds(), **_parametros_de_conexion())
    usuario = host = None
    conn_usuario = None
    try:
        async with conn.cursor() as cur:
            try:
                usuario, contrasena, host = await _crear_usuario_solo_insert(cur, base=base)
            except _NoSePuedeCrearUsuarios as exc:
                if _en_ci():
                    raise RuntimeError(
                        "CI corrió sin CREATE USER -- la premisa de este archivo (JAX_DB_USER=root "
                        "en el job backend-tests-con-db) dejó de ser cierta. No se salta: repórtalo."
                    ) from exc
                pytest.skip(
                    f"el usuario configurado ({os.environ.get('JAX_DB_USER')!r}) no tiene CREATE "
                    f"USER en esta MariaDB -- esperado fuera de CI (root la tiene en CI): {exc}"
                )
            await cur.execute(
                "INSERT INTO ejecutor_host (nombre, ip, puerto, rol) VALUES (%s, %s, %s, 'produccion')",
                (nombre_host, ip_ejecutor, puerto_ejecutor))
        await conn.commit()

        conn_usuario = pymysql.connect(
            host=os.environ.get("JAX_DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("JAX_DB_PORT", "3306")),
            user=usuario, password=contrasena, database=base, connect_timeout=5)
        with conn_usuario.cursor() as cur_usuario:
            cur_usuario.execute(
                "INSERT INTO ejecutor_punto_restauracion (host_nombre, referencia, metodo, "
                "respaldado_at, restaurado_y_verificado_at, verificado_por, evidencia) "
                "VALUES (%s, 'snap-1@repo', 'imagen_vm', UTC_TIMESTAMP(), UTC_TIMESTAMP(), "
                "'test', '{\"bytes\":1}')",
                (nombre_host,))
        conn_usuario.commit()

        for etiqueta, sentencia in (
            ("UPDATE", "UPDATE ejecutor_punto_restauracion SET referencia='otra' WHERE host_nombre=%s"),
            ("DELETE", "DELETE FROM ejecutor_punto_restauracion WHERE host_nombre=%s"),
            ("SELECT", "SELECT * FROM ejecutor_punto_restauracion WHERE host_nombre=%s"),
        ):
            with pytest.raises(pymysql.err.OperationalError) as exc:
                with conn_usuario.cursor() as cur_usuario:
                    cur_usuario.execute(sentencia, (nombre_host,))
            assert exc.value.args[0] == 1142, (etiqueta, exc.value.args)  # "command denied"

        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT referencia FROM ejecutor_punto_restauracion WHERE host_nombre=%s", (nombre_host,))
            filas = await cur.fetchall()
        assert filas == (("snap-1@repo",),)
    finally:
        if conn_usuario is not None:
            conn_usuario.close()
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM ejecutor_punto_restauracion WHERE host_nombre=%s", (nombre_host,))
            await cur.execute("DELETE FROM ejecutor_host WHERE nombre=%s", (nombre_host,))
            if usuario is not None and host is not None:
                await _borrar_usuario(cur, usuario, host)
        await conn.commit()
        conn.close()


@pytest.mark.skipif(_SIN_MARIADB, reason=_RAZON_SIN_MARIADB)
async def test_usuario_solo_insert_no_conecta_fuera_de_su_subred():
    """El host resuelto en vivo no es cosmético: MariaDB tiene que conocer al
    usuario ÚNICAMENTE con ese host, leído de mysql.user -- no con un
    comodín más amplio que el que devolvió `_host_de_esta_conexion`."""
    base = os.environ["JAX_DB_NAME"]
    assert re.fullmatch(r"[A-Za-z0-9_]+", base), base
    assert base != "jax_memory", "la base de test resolvió a PRODUCCIÓN -- no se toca"

    conn = await aiomysql.connect(
        db=base, connect_timeout=db_connect_timeout_seconds(), **_parametros_de_conexion())
    usuario = host = None
    try:
        async with conn.cursor() as cur:
            try:
                usuario, _contrasena, host = await _crear_usuario_solo_insert(cur, base=base)
            except _NoSePuedeCrearUsuarios as exc:
                if _en_ci():
                    raise RuntimeError(
                        "CI corrió sin CREATE USER -- ver test hermano en este archivo."
                    ) from exc
                pytest.skip(
                    f"el usuario configurado ({os.environ.get('JAX_DB_USER')!r}) no tiene CREATE "
                    f"USER en esta MariaDB -- esperado fuera de CI: {exc}"
                )
        await conn.commit()

        async with conn.cursor() as cur:
            await cur.execute("SELECT Host FROM mysql.user WHERE User = %s", (usuario,))
            filas = await cur.fetchall()
        assert filas == ((host,),), filas
    finally:
        if usuario is not None and host is not None:
            async with conn.cursor() as cur:
                await _borrar_usuario(cur, usuario, host)
            await conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# El GRANT de producción, sin tocar ninguna base: sólo texto. Corre siempre,
# en cualquier runner -- no depende de MariaDB.
# ---------------------------------------------------------------------------

def test_sql_de_produccion_otorga_solo_insert_y_nada_mas():
    """La plantilla, con `{HOST}` sustituido por el host REAL de producción
    (`'172.30.5.%'`, el mismo alcance que jax_user) y `{BASE}`/`{USUARIO}`
    por sus valores reales, tiene que dar EXACTAMENTE
    `GRANT INSERT ON jax_memory.ejecutor_punto_restauracion` -- sin SELECT,
    sin ALL, sin `*.*`. Si algún día alguien agrega un privilegio de más a
    la plantilla (a mano, "por si acaso"), este test lo ve ANTES de que
    llegue a producción."""
    sustituida = _sustituir(
        _leer_plantilla(),
        usuario="ejecutor_verificador", base="jax_memory", host="172.30.5.%",
        contrasena="no-se-usa-en-este-test")

    grants = re.findall(
        r"GRANT\s+(?P<privilegios>.+?)\s+ON\s+(?P<objetivo>\S+)\s+TO\s+'(?P<usuario>[^']+)'@'(?P<host>[^']+)';",
        sustituida)
    assert len(grants) == 1, f"se esperaba exactamente 1 GRANT, hay {len(grants)}: {grants}"
    privilegios, objetivo, usuario, host = grants[0]

    assert privilegios == "INSERT", privilegios
    assert objetivo == "jax_memory.ejecutor_punto_restauracion", objetivo
    assert usuario == "ejecutor_verificador"
    assert host == "172.30.5.%"

    # Cinturón y tirantes sobre la misma afirmación: nada de esto puede
    # aparecer en la sentencia GRANT sustituida.
    linea_grant = next(l for l in sustituida.splitlines() if l.strip().startswith("GRANT"))
    for prohibido in ("SELECT", "UPDATE", "DELETE", "ALL", "*.*"):
        assert prohibido not in linea_grant, (prohibido, linea_grant)


def test_sql_de_produccion_crea_el_usuario_con_el_host_real():
    """`CREATE USER` también queda con el host de producción, no un
    comodín -- mismo criterio que el GRANT."""
    sustituida = _sustituir(
        _leer_plantilla(),
        usuario="ejecutor_verificador", base="jax_memory", host="172.30.5.%",
        contrasena="efimero-de-prueba")
    creaciones = re.findall(r"CREATE USER '([^']+)'@'([^']+)'", sustituida)
    assert creaciones == [("ejecutor_verificador", "172.30.5.%")], creaciones
