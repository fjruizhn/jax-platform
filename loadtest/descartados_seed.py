"""Siembra para la carga de Task 8 (descartar-pipelines, 2026-09-22,
docs/carga-descartados-pipelines-2026-09-22.md) -- LAS CUATRO #4, decisión
del coordinador: mide GET /api/pipelines y GET /api/pipelines?estado=discarded
contra la base de TEST, con un usuario sembrado A ESCALA (>= 5.000 pipelines,
20 % descartados) MÁS un usuario con la forma extrema (pocos vivos, muchos
descartados) -- las mismas dos formas que ya midió, a nivel SQL puro, el fix
round 4 de Task 1-bis en jax (backend/tests/test_carga_indice_pipelines_del_usuario.py,
"historial largo" / "muchos descartados"). Acá se mide el camino COMPLETO
(HTTP -> auth -> Mesa -> SQL), no sólo el EXPLAIN.

Requiere que `jacobs_pipelines` tenga el esquema de descartar-pipelines
(status_previo/descartado_por/descartado_at/visible + los índices
idx_pipelines_visibles/idx_pipelines_descartados/idx_pipelines_ocultos) --
`_asegurar_esquema()` lo agrega de forma IDEMPOTENTE contra `jax_memory_test`
llamando a `jacobs.store.init_tables()` del repo `jax` apuntado por
JAX_REPO_PATH (necesita jax#259 -- `visible`/`idx_pipelines_visibles` -- y
jax#257 -- las tres columnas de descarte). Mismo mecanismo que
`backend/tests/conftest.py::_esquema_de_jax_en_la_base_de_test`, aplicado acá
contra `jax_memory_test` directo (no a un clon por sesión): los scripts de
loadtest/ ya escriben ahí directo (ver historial_seed.py).

USO:
    JAX_REPO_PATH=/home/fruiz/worktrees/jax-master-para-tests \
    python3 loadtest/descartados_seed.py loadtest/_descartados_seed_result.json

Requiere `sudo -n cat /etc/jax/.env` (mismo mecanismo que historial_seed.py).
NUNCA escribe fuera de `jax_memory_test` -- misma barrera dura que
historial_seed.py: revienta antes de escribir una sola fila si la conexión
no es a esa base.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid

import bcrypt
import pymysql

BASE_DE_PRUEBA = "jax_memory_test"  # la ÚNICA base a la que este script escribe

# --- Forma A: "a escala" (pedido mínimo del coordinador) -------------------
# >= 5.000 pipelines, 20 % descartados -- exactamente 5.000 / 1.000 (20 %).
N_VISIBLES_A = 4000
N_DESCARTADOS_A = 1000

# --- Forma B: "extrema" -- pocos vivos, muchos descartados ------------------
# Mismo espíritu que "muchos descartados" en
# test_carga_indice_pipelines_del_usuario.py (5000 descartadas + 3 vivas): el
# caso que en la ronda 3 (FORCE INDEX idx_jacobs_pipelines_duenio, sin
# `visible`) pagaba un recorrido lineal del histórico completo del dueño.
N_VISIBLES_B = 3
N_DESCARTADOS_B = 5000


def _cargar_env_produccion() -> dict:
    r = subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"], capture_output=True, text=True, check=True)
    env = {}
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            env[k.strip()] = v.strip()
    return env


def _asegurar_esquema(env: dict) -> None:
    """Idempotente -- ver docstring del módulo. JAX_REPO_PATH tiene que
    apuntar a un checkout de jax con jax#257 y jax#259 (verificado: el mismo
    checkout que usa policy.yml para las mediciones de índice,
    /home/fruiz/worktrees/jax-master-para-tests)."""
    jax_repo_path = os.environ.get("JAX_REPO_PATH")
    if not jax_repo_path:
        raise SystemExit(
            "JAX_REPO_PATH no está seteado -- tiene que apuntar a un checkout "
            "de jax con jax#257 (columnas de descarte) y jax#259 (visible/"
            "idx_pipelines_visibles). Ver docs/carga-sql-pipelines-del-usuario-indice-2026-09-22.md.")
    sys.path.insert(0, jax_repo_path)
    # jacobs/policy.py hace `from interruptor import interruptor_activo` --
    # un import PELADO, no `from las_manos.interruptor import ...`: sin
    # las_manos/ agregado APARTE al path (además de la raíz del repo, que
    # da `jacobs`/`jax`), ese import falla con
    # `ModuleNotFoundError: No module named 'interruptor'`. Mismo patrón
    # que backend/tests/conftest.py::_esquema_de_jax_en_la_base_de_test
    # necesita cuando corre `jacobs.store` fuera del árbol de tests de jax
    # (que sí tiene las_manos en su propio PYTHONPATH de CI).
    sys.path.insert(0, os.path.join(jax_repo_path, "las_manos"))

    os.environ.update(env)
    os.environ["JAX_DB_NAME"] = BASE_DE_PRUEBA

    from jacobs import store as jacobs_store

    async def _correr():
        try:
            await jacobs_store.init_tables()
        finally:
            await jacobs_store.cerrar_pool()

    asyncio.run(_correr())


def _filas(user_id, tenant_id, n_visibles, n_descartados, offset_id):
    """Filas para UN usuario: n_visibles NO descartadas (owner_ack_at
    seteado -> `visible=1`) + n_descartados con status='discarded'
    (owner_ack_at también seteado -- SQL_DESCARTADOS_DEL_USUARIO lo exige --
    y descartado_at/status_previo/descartado_por con el valor que Jacobs
    escribiría de verdad, no NULL). created_at decreciente por índice (el
    más nuevo primero), como un historial real."""
    ahora = time.time()
    total = n_visibles + n_descartados
    filas = []
    ids_visibles, ids_descartados = [], []
    for i in range(total):
        pid = str(uuid.uuid4())
        creado = ahora - (offset_id + total - i) * 3
        actualizado = creado + 5.0
        es_descartado = i < n_descartados  # los primeros N -- no importa el orden para la medición
        if es_descartado:
            descartado_at = creado + 10.0
            filas.append((pid, "carga descartar-pipelines", "plataforma", "supervised",
                          "discarded", creado, actualizado, user_id, tenant_id, creado,
                          "aborted", user_id, descartado_at))
            ids_descartados.append(pid)
        else:
            filas.append((pid, "carga descartar-pipelines", "plataforma", "supervised",
                          "completed", creado, actualizado, user_id, tenant_id, creado,
                          None, None, None))
            ids_visibles.append(pid)
    return filas, ids_visibles, ids_descartados


def _insertar(cur, filas):
    cur.executemany(
        "INSERT INTO jacobs_pipelines "
        "(pipeline_id, name, invoked_by, mode, status, created_at, updated_at, "
        " user_id, tenant_id, owner_ack_at, status_previo, descartado_por, descartado_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        filas,
    )


def _crear_usuario(cur, tenant_id, etiqueta) -> int:
    email = f"carga-descartados-{etiqueta}-{uuid.uuid4().hex[:10]}@example.invalid"
    pw_hash = bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode()
    cur.execute(
        "INSERT INTO jax_users (tenant_id, email, password_hash, role, status, token_version) "
        "VALUES (%s, %s, %s, 'operator', 'active', 0)",
        (tenant_id, email, pw_hash),
    )
    return cur.lastrowid


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("uso: descartados_seed.py <ruta-de-salida.json>")

    env = _cargar_env_produccion()

    print("asegurando el esquema de descartar-pipelines en jax_memory_test "
          "(idempotente, jacobs.store.init_tables)...", file=sys.stderr)
    _asegurar_esquema(env)

    conn = pymysql.connect(
        host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]),
        user=env["JAX_DB_USER"], password=env["JAX_DB_PASSWORD"],
        database=BASE_DE_PRUEBA, autocommit=False, charset="utf8mb4",
    )
    # BARRERA DURA, no un assert -- mismo criterio que historial_seed.py:
    # ningún otro script de este directorio confía en que otro ya la hizo.
    with conn.cursor() as cur:
        cur.execute("SELECT DATABASE()")
        (db,) = cur.fetchone()
    if db != BASE_DE_PRUEBA:
        raise RuntimeError(
            f"conectado a {db!r}, no a {BASE_DE_PRUEBA!r} -- ABORTANDO sin escribir nada.")
    print(f"conectado a {db}", file=sys.stderr)

    tenant_id = "1"
    usuarios = {}
    for etiqueta, n_vis, n_desc, offset in (
        ("escala", N_VISIBLES_A, N_DESCARTADOS_A, 0),
        ("extremo", N_VISIBLES_B, N_DESCARTADOS_B, N_VISIBLES_A + N_DESCARTADOS_A),
    ):
        with conn.cursor() as cur:
            user_id = _crear_usuario(cur, tenant_id, etiqueta)
        conn.commit()
        filas, ids_vis, ids_desc = _filas(str(user_id), tenant_id, n_vis, n_desc, offset)
        with conn.cursor() as cur:
            _insertar(cur, filas)
        conn.commit()
        print(f"usuario {etiqueta}: user_id={user_id} visibles={len(ids_vis)} "
              f"descartados={len(ids_desc)} total={len(filas)}", file=sys.stderr)
        usuarios[etiqueta] = {
            "user_id": user_id, "tenant_id": tenant_id,
            "n_visibles": len(ids_vis), "n_descartados": len(ids_desc),
            "todos_los_pipelines": ids_vis + ids_desc,
        }

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM jacobs_pipelines")
        (total_tabla,) = cur.fetchone()
    print(f"jacobs_pipelines total tras la siembra: {total_tabla}", file=sys.stderr)

    resultado = {"tenant_id": tenant_id, "usuarios": usuarios}
    with open(sys.argv[1], "w") as f:
        json.dump(resultado, f)
    conn.close()
    print("SEED OK", file=sys.stderr)


if __name__ == "__main__":
    main()
