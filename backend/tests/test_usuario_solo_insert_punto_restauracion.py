"""C2 (Esquema, diseño 2026-09-22): el usuario que escribe en ejecutor_punto_restauracion es
de SOLO INSERT.

jax_user (con quien corre run_migrations() y toda la suite) NO PUEDE crear ese usuario:
verificado 2026-09-22 contra jax_memory con `SHOW GRANTS FOR CURRENT_USER()` -- sólo tiene
USAGE en *.* y ALL PRIVILEGES acotado a jax_memory/jax_memory_test/jax_memory_test_%, sin
CREATE USER ni GRANT OPTION en ningún alcance. Por eso este test NO corre contra
jax_memory_test (no podría crear el usuario ahí tampoco) ni contra jax_memory: levanta un
MariaDB EFÍMERO y descartable por Docker (misma imagen que producción, 12.3.3), ejecuta ahí
las sentencias EXACTAS que la cabecera de claude-skills/bin/verificar-punto-restauracion.sh
documenta como mecanismo manual, y comprueba -- de verdad, no por lectura de GRANTS -- que
ese usuario puede INSERT y no puede UPDATE ni DELETE (Principio VII: un freno sin prueba no
es freno).

Se salta si no hay `docker` en el PATH o si `sudo -n docker version` no contesta (en
hall9000, fruiz no está en el grupo docker: el acceso es por sudoers NOPASSWD). Cuando corre,
es la única prueba de este repo que toca Docker -- documentado acá, no oculto en un fixture
compartido.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid

import pymysql
import pytest

from db import migrations

IMAGEN = "mariadb:12.3.3"  # misma serie que corre en producción (mariadb-12-3-jax)
ROOT_PASSWORD = "efimero-" + uuid.uuid4().hex
USUARIO = "ejecutor_verificador"
CONTRASENA_USUARIO = "efimero-" + uuid.uuid4().hex

# Las sentencias EXACTAS documentadas en la cabecera de
# claude-skills/bin/verificar-punto-restauracion.sh (fuente canónica del mecanismo). Se
# repiten acá a propósito -- "espejo mínimo, sin paquete compartido" entre los dos repos,
# mismo criterio que base_de_test.py -- para probar que SON ejecutables y que hacen lo que
# dicen, no sólo que están escritas.
SQL_CREAR_USUARIO = (
    f"CREATE USER '{USUARIO}'@'%' IDENTIFIED BY '{CONTRASENA_USUARIO}'"
)
SQL_GRANT = f"GRANT INSERT ON jax_memory.ejecutor_punto_restauracion TO '{USUARIO}'@'%'"


def _docker_disponible() -> bool:
    if shutil.which("docker") is None:
        return False
    r = subprocess.run(["sudo", "-n", "docker", "version"], capture_output=True, timeout=15)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_disponible(),
    reason="sin docker (o sin sudo -n docker) en esta máquina -- ver el docstring del módulo",
)


@pytest.fixture(scope="module")
def mariadb_efimero():
    nombre = f"jp-test-punto-restauracion-{uuid.uuid4().hex[:10]}"
    proc = subprocess.run(
        ["sudo", "-n", "docker", "run", "-d", "--rm", "--name", nombre,
         "-e", f"MARIADB_ROOT_PASSWORD={ROOT_PASSWORD}", "-e", "MARIADB_DATABASE=jax_memory",
         "-p", "127.0.0.1::3306", IMAGEN],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"docker run falló: {proc.stderr}"
    try:
        puerto_raw = subprocess.run(
            ["sudo", "-n", "docker", "port", nombre, "3306/tcp"],
            capture_output=True, text=True, timeout=15,
        )
        assert puerto_raw.returncode == 0, puerto_raw.stderr
        puerto = int(puerto_raw.stdout.strip().rsplit(":", 1)[-1])

        conn = None
        ultimo_error = None
        for _ in range(60):
            try:
                conn = pymysql.connect(host="127.0.0.1", port=puerto, user="root",
                                       password=ROOT_PASSWORD, database="jax_memory",
                                       connect_timeout=2)
                break
            except Exception as exc:  # fail-soft: el server tarda unos segundos en aceptar conexiones, se reintenta
                ultimo_error = exc
                time.sleep(1)
        assert conn is not None, f"el MariaDB efímero no levantó a tiempo: {ultimo_error}"
        try:
            yield conn, puerto
        finally:
            conn.close()
    finally:
        subprocess.run(["sudo", "-n", "docker", "rm", "-f", nombre],
                       capture_output=True, timeout=30)


def _ejecutar(conn, sentencia):
    with conn.cursor() as cur:
        cur.execute(sentencia)
    conn.commit()


def test_usuario_solo_insert_puede_insert_pero_no_update_ni_delete(mariadb_efimero):
    conn, puerto = mariadb_efimero

    # El esquema real: el mismo DDL que corre en producción (db/migrations.py), más las
    # tres correcciones de _asegurar_forma_de_ejecutor_punto_restauracion (esta prueba fija
    # ENUM sin pasar por el idempotente -- lo idempotente ya lo prueba test_ejecutor_tablas.py).
    _ejecutar(conn, migrations.CREATE_EJECUTOR_HOST)
    _ejecutar(conn, migrations.CREATE_EJECUTOR_PUNTO_RESTAURACION)
    enum_sql = ",".join(f"'{m}'" for m in migrations._METODOS_PUNTO_RESTAURACION)
    _ejecutar(conn, f"ALTER TABLE ejecutor_punto_restauracion MODIFY COLUMN metodo ENUM({enum_sql}) NOT NULL")
    _ejecutar(conn, "ALTER TABLE ejecutor_punto_restauracion ADD COLUMN respaldado_at DATETIME NOT NULL AFTER referencia")
    _ejecutar(conn, "INSERT INTO ejecutor_host (nombre, ip, puerto, rol) VALUES ('prod', '172.16.20.10', 58291, 'produccion')")

    # El mecanismo EXACTO documentado -- no una aproximación de la prueba.
    _ejecutar(conn, SQL_CREAR_USUARIO)
    _ejecutar(conn, SQL_GRANT)
    _ejecutar(conn, "FLUSH PRIVILEGES")

    conn_usuario = pymysql.connect(host="127.0.0.1", port=puerto, user=USUARIO,
                                   password=CONTRASENA_USUARIO, database="jax_memory",
                                   connect_timeout=5)
    try:
        # INSERT: tiene que poder.
        with conn_usuario.cursor() as cur:
            cur.execute(
                "INSERT INTO ejecutor_punto_restauracion (host_nombre, referencia, metodo, "
                "respaldado_at, restaurado_y_verificado_at, verificado_por, evidencia) "
                "VALUES ('prod', 'snap-1@repo', 'imagen_vm', UTC_TIMESTAMP(), UTC_TIMESTAMP(), "
                "'test', '{\"bytes\":1}')"
            )
        conn_usuario.commit()

        # UPDATE: NO tiene que poder -- error 1142 (ER_TABLEACCESS_DENIED_ERROR).
        with pytest.raises(pymysql.err.OperationalError) as exc_update:
            with conn_usuario.cursor() as cur:
                cur.execute("UPDATE ejecutor_punto_restauracion SET referencia='otra' WHERE host_nombre='prod'")
        assert exc_update.value.args[0] == 1142, exc_update.value.args

        # DELETE: tampoco.
        with pytest.raises(pymysql.err.OperationalError) as exc_delete:
            with conn_usuario.cursor() as cur:
                cur.execute("DELETE FROM ejecutor_punto_restauracion WHERE host_nombre='prod'")
        assert exc_delete.value.args[0] == 1142, exc_delete.value.args

        # La fila del INSERT sigue ahí, intacta: ni el UPDATE ni el DELETE pasaron.
        with conn.cursor() as cur:
            cur.execute("SELECT referencia FROM ejecutor_punto_restauracion WHERE host_nombre='prod'")
            filas = cur.fetchall()
        assert filas == (("snap-1@repo",),)
    finally:
        conn_usuario.close()


def test_usuario_solo_insert_no_puede_select(mariadb_efimero):
    """No pedido explícitamente por el diseño, pero es la lectura honesta de "SOLO INSERT":
    si además pudiera SELECT, un compromiso de esa credencial permitiría leer qué máquinas
    tienen puntos de restauración recientes -- información de reconocimiento gratis."""
    conn, puerto = mariadb_efimero
    conn_usuario = pymysql.connect(host="127.0.0.1", port=puerto, user=USUARIO,
                                   password=CONTRASENA_USUARIO, database="jax_memory",
                                   connect_timeout=5)
    try:
        with pytest.raises(pymysql.err.OperationalError) as exc_select:
            with conn_usuario.cursor() as cur:
                cur.execute("SELECT * FROM ejecutor_punto_restauracion")
        assert exc_select.value.args[0] == 1142, exc_select.value.args
    finally:
        conn_usuario.close()
