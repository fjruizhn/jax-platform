"""C2 (Esquema, diseño 2026-09-22): el usuario que escribe en ejecutor_punto_restauracion es
de SOLO INSERT.

jax_user (con quien corre run_migrations() y toda la suite) NO PUEDE crear ese usuario:
verificado 2026-09-22 contra jax_memory con `SHOW GRANTS FOR CURRENT_USER()` -- sólo tiene
USAGE en *.* y ALL PRIVILEGES acotado a jax_memory/jax_memory_test/jax_memory_test_%, sin
CREATE USER ni GRANT OPTION en ningún alcance. Por eso este test NO corre contra
jax_memory_test (no podría crear el usuario ahí tampoco) ni contra jax_memory: levanta un
MariaDB EFÍMERO y descartable por Docker (misma imagen que producción, 12.3.3), le aplica el
esquema con la FUNCIÓN REAL de este repo (`_asegurar_forma_de_ejecutor_punto_restauracion`,
no un ALTER copiado a mano -- hallazgo de la auditoría adversarial, 2026-09-22), ejecuta ahí
las sentencias EXACTAS extraídas (no retipeadas) de la cabecera de
claude-skills/bin/verificar-punto-restauracion.sh, y comprueba -- de verdad, no por lectura
de GRANTS -- que ese usuario puede INSERT y no puede UPDATE, DELETE ni SELECT (Principio VII:
un freno sin prueba no es freno).

**El host del usuario es '172.30.5.%'** (mismo alcance que jax_user, verificado con SHOW
GRANTS): por eso el contenedor de prueba se conecta a la MISMA red Docker que ya usa
`mariadb-12-3-jax` (`mariadb-123_jax-db-net`, subred 172.30.5.0/24 -- medido 2026-09-22 con
`docker network inspect`; NO se crea una red nueva con esa subred porque ya está asignada, y
Docker la rechazaría). Este proceso se conecta por la IP del contenedor DE PRUEBA en esa red
(no el mapeo a 127.0.0.1), así que MariaDB lo ve llegar por la misma puerta (172.30.5.1) por
la que ve llegar a jax_user -- mismo mecanismo, sin tocar el contenedor real ni sus datos.

Se salta si no hay `docker` en el PATH o si `sudo -n docker version` no contesta (en
hall9000, fruiz no está en el grupo docker: el acceso es por sudoers NOPASSWD). Cuando corre,
es la única prueba de este repo que toca Docker -- documentado acá, no oculto en un fixture
compartido.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import aiomysql
import pymysql
import pytest

from db import migrations

IMAGEN = "mariadb:12.3.3"  # misma serie que corre en producción (mariadb-12-3-jax)
ROOT_PASSWORD = "efimero-" + uuid.uuid4().hex
CONTRASENA_USUARIO = "efimero-" + uuid.uuid4().hex
# La red que YA usa mariadb-12-3-jax (medido 2026-09-22): 172.30.5.0/24, puerta 172.30.5.1.
# Reusada, no recreada -- Docker rechaza una subred duplicada, y es justo la subred por la
# que hall9000 le habla a jax_user.
RED_EXISTENTE = "mariadb-123_jax-db-net"
RED_GATEWAY = "172.30.5.1"

# ---------------------------------------------------------------------------
# Las sentencias EXACTAS: se EXTRAEN de la cabecera de
# claude-skills/bin/verificar-punto-restauracion.sh (entre los marcadores
# SQL_CREAR_USUARIO_INICIO/FIN), no se retipean -- "espejo mínimo, sin paquete compartido"
# entre los dos repos (mismo criterio que base_de_test.py), pero DERIVADO, para que un
# cambio en el guion real rompa este test si diverge, en vez de que las dos copias se separen
# en silencio.
# ---------------------------------------------------------------------------
_MARCADOR_INICIO = "# ---8<--- SQL_CREAR_USUARIO_INICIO"
_MARCADOR_FIN = "# ---8<--- SQL_CREAR_USUARIO_FIN"


def _ruta_guion_verificador() -> Path | None:
    """El worktree de esta ronda primero (tiene el fix del host '172.30.5.%'); si no está
    (otra máquina, CI sin ese worktree), el checkout principal de claude-skills."""
    candidatos = [
        Path.home() / "worktrees" / "cs-verificador" / "bin" / "verificar-punto-restauracion.sh",
        Path.home() / "claude-skills" / "bin" / "verificar-punto-restauracion.sh",
    ]
    return next((p for p in candidatos if p.is_file()), None)


def _extraer_sentencias_sql() -> list[str]:
    ruta = _ruta_guion_verificador()
    if ruta is None:
        return []
    texto = ruta.read_text(encoding="utf-8")
    if _MARCADOR_INICIO not in texto or _MARCADOR_FIN not in texto:
        return []
    bloque = texto.split(_MARCADOR_INICIO, 1)[1].split(_MARCADOR_FIN, 1)[0]
    lineas = []
    for linea in bloque.splitlines():
        linea = linea.strip()
        if not linea.startswith("#"):
            continue
        contenido = linea.lstrip("#").strip()
        if contenido:
            lineas.append(contenido)
    # Cada sentencia termina en ';' en el guion; se separan y se limpia el punto y coma.
    texto_sql = " ".join(lineas)
    return [s.strip() for s in texto_sql.split(";") if s.strip()]


def _docker_disponible() -> bool:
    if shutil.which("docker") is None:
        return False
    r = subprocess.run(["sudo", "-n", "docker", "version"], capture_output=True, timeout=15)
    return r.returncode == 0


_SENTENCIAS = _extraer_sentencias_sql()

pytestmark = pytest.mark.skipif(
    not _docker_disponible(),
    reason="sin docker (o sin sudo -n docker) en esta máquina -- ver el docstring del módulo",
)
pytestmark_sentencias = pytest.mark.skipif(
    not _SENTENCIAS,
    reason="no se encontró bin/verificar-punto-restauracion.sh (ni en ~/worktrees/cs-verificador "
           "ni en ~/claude-skills) para extraer las sentencias EXACTAS -- ver el docstring",
)


def _red_existe(nombre: str) -> bool:
    r = subprocess.run(["sudo", "-n", "docker", "network", "inspect", nombre],
                       capture_output=True, timeout=15)
    return r.returncode == 0


@pytest.fixture(scope="module")
def mariadb_efimero():
    if not _red_existe(RED_EXISTENTE):
        pytest.skip(f"la red {RED_EXISTENTE!r} no existe en esta máquina -- "
                    "sin mariadb-12-3-jax corriendo no hay subred 172.30.5.0/24 que reusar")
    nombre = f"jp-test-punto-restauracion-{uuid.uuid4().hex[:10]}"
    proc = subprocess.run(
        ["sudo", "-n", "docker", "run", "-d", "--rm", "--name", nombre,
         "--network", RED_EXISTENTE,
         "-e", f"MARIADB_ROOT_PASSWORD={ROOT_PASSWORD}", "-e", "MARIADB_DATABASE=jax_memory",
         IMAGEN],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"docker run falló: {proc.stderr}"
    try:
        inspeccion = subprocess.run(
            ["sudo", "-n", "docker", "inspect", "-f",
             f"{{{{(index .NetworkSettings.Networks \"{RED_EXISTENTE}\").IPAddress}}}}", nombre],
            capture_output=True, text=True, timeout=15,
        )
        assert inspeccion.returncode == 0, inspeccion.stderr
        ip_contenedor = inspeccion.stdout.strip()
        assert ip_contenedor, "el contenedor no tiene IP en la red de prueba"

        conn = None
        ultimo_error = None
        for _ in range(60):
            try:
                conn = pymysql.connect(host=ip_contenedor, port=3306, user="root",
                                       password=ROOT_PASSWORD, database="jax_memory",
                                       connect_timeout=2)
                break
            except Exception as exc:  # fail-soft: el server tarda unos segundos en aceptar conexiones, se reintenta
                ultimo_error = exc
                time.sleep(1)
        assert conn is not None, f"el MariaDB efímero no levantó a tiempo: {ultimo_error}"
        try:
            yield conn, ip_contenedor
        finally:
            conn.close()
    finally:
        subprocess.run(["sudo", "-n", "docker", "rm", "-f", nombre], capture_output=True, timeout=30)


def _ejecutar(conn, sentencia):
    with conn.cursor() as cur:
        cur.execute(sentencia)
    conn.commit()


async def _aplicar_esquema_real(host, root_password):
    """Crea las tablas con el DDL real de este repo y les aplica la función REAL de la
    migración (no un ALTER copiado): si `_asegurar_forma_de_ejecutor_punto_restauracion`
    cambia de forma en el futuro, este test lo sigue sin que nadie tenga que acordarse de
    actualizar una copia."""
    conn = await aiomysql.connect(host=host, port=3306, user="root", password=root_password,
                                  db="jax_memory", connect_timeout=10)
    try:
        async with conn.cursor() as cur:
            await cur.execute(migrations.CREATE_EJECUTOR_HOST)
            await cur.execute(migrations.CREATE_EJECUTOR_PUNTO_RESTAURACION)
            await migrations._asegurar_forma_de_ejecutor_punto_restauracion(cur)
            await cur.execute(
                "INSERT INTO ejecutor_host (nombre, ip, puerto, rol) "
                "VALUES ('prod', '172.16.20.10', 58291, 'produccion')"
            )
        await conn.commit()
    finally:
        conn.close()


@pytestmark_sentencias
def test_usuario_solo_insert_puede_insert_pero_no_update_ni_delete_ni_select(mariadb_efimero):
    conn, ip_contenedor = mariadb_efimero
    import asyncio
    asyncio.run(_aplicar_esquema_real(ip_contenedor, ROOT_PASSWORD))

    # Las sentencias EXACTAS extraídas del guion, con el marcador de contraseña sustituido
    # por una generada para esta corrida (nunca la que queda en el repo).
    for sentencia in _SENTENCIAS:
        _ejecutar(conn, sentencia.replace("__CONTRASENA__", CONTRASENA_USUARIO))

    usuario_match = re.search(r"CREATE USER '([^']+)'@'([^']+)'", _SENTENCIAS[0])
    assert usuario_match, _SENTENCIAS[0]
    usuario, host_usuario = usuario_match.groups()
    assert host_usuario == "172.30.5.%", (
        f"el guion documenta el host '{host_usuario}', se esperaba '172.30.5.%' "
        "(el mismo por el que entra jax_user -- ver SHOW GRANTS del 2026-09-22)"
    )

    # Esta conexión llega desde RED_GATEWAY (172.30.5.1): coincide con '172.30.5.%'.
    conn_usuario = pymysql.connect(host=ip_contenedor, port=3306, user=usuario,
                                   password=CONTRASENA_USUARIO, database="jax_memory",
                                   connect_timeout=5)
    try:
        with conn_usuario.cursor() as cur:
            cur.execute(
                "INSERT INTO ejecutor_punto_restauracion (host_nombre, referencia, metodo, "
                "respaldado_at, restaurado_y_verificado_at, verificado_por, evidencia) "
                "VALUES ('prod', 'snap-1@repo', 'imagen_vm', UTC_TIMESTAMP(), UTC_TIMESTAMP(), "
                "'test', '{\"bytes\":1}')"
            )
        conn_usuario.commit()

        for etiqueta, sentencia in (
            ("UPDATE", "UPDATE ejecutor_punto_restauracion SET referencia='otra' WHERE host_nombre='prod'"),
            ("DELETE", "DELETE FROM ejecutor_punto_restauracion WHERE host_nombre='prod'"),
            ("SELECT", "SELECT * FROM ejecutor_punto_restauracion"),
        ):
            with pytest.raises(pymysql.err.OperationalError) as exc:
                with conn_usuario.cursor() as cur:
                    cur.execute(sentencia)
            assert exc.value.args[0] == 1142, (etiqueta, exc.value.args)

        with conn.cursor() as cur:
            cur.execute("SELECT referencia FROM ejecutor_punto_restauracion WHERE host_nombre='prod'")
            filas = cur.fetchall()
        assert filas == (("snap-1@repo",),)
    finally:
        conn_usuario.close()


@pytestmark_sentencias
def test_usuario_solo_insert_no_conecta_fuera_de_su_subred(mariadb_efimero):
    """El host '172.30.5.%' no es cosmético: un intento de conexión desde una IP FUERA de esa
    subred no debería ni autenticar. No hay forma directa de originar la conexión desde otra
    subred en este test (el proceso corre en el host, siempre entra por RED_GATEWAY) -- lo que
    SÍ se comprueba es que MariaDB conoce a ese usuario únicamente con ese host restringido,
    leyendo mysql.user."""
    conn, _ = mariadb_efimero
    with conn.cursor() as cur:
        cur.execute("SELECT Host FROM mysql.user WHERE User = %s", (re.search(
            r"CREATE USER '([^']+)'", _SENTENCIAS[0]).group(1),))
        filas = cur.fetchall()
    assert filas == (("172.30.5.%",),), filas
