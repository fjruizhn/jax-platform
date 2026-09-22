"""Siembra para la carga de Task 8 (descartar-pipelines, 2026-09-22,
docs/carga-descartados-pipelines-2026-09-22.md) -- LAS CUATRO #4, decisión
del coordinador: mide GET /api/pipelines y GET /api/pipelines?estado=discarded
contra la base de TEST, con un usuario sembrado A ESCALA (>= 5.000 pipelines,
20 % descartados) MÁS un usuario con la forma extrema (pocos vivos, muchos
descartados) -- las mismas dos formas que ya midió, a nivel SQL puro, el fix
round 4 de Task 1-bis en jax (backend/tests/test_carga_indice_pipelines_del_usuario.py,
"historial largo" / "muchos descartados"). Acá se mide el camino COMPLETO
(HTTP -> auth -> Mesa -> SQL), no sólo el EXPLAIN.

Fix round 1 (2026-09-22, BLOCK-2, revisión adversarial de PR#151): agrega
DOS formas más, para los dos endpoints nuevos del cierre de huecos (que no
tenían NINGUNA carga -- `loadtest/` y `docs/` quedaron sin tocar en la
primera vuelta):

- **"admin_muchos_usuarios"**: N_ADMIN_DESCARTADOS filas `discarded`
  repartidas entre N_ADMIN_USUARIOS user_id/tenant_id DISTINTOS (sin crear
  cuentas reales en `jax_users` -- GET /admin/pipelines/descartados no hace
  JOIN con esa tabla, lee `jacobs_pipelines` directo, mismo criterio que
  `test_explain_descartados_admin_usa_idx_pipelines_ocultos_sin_filesort`
  del backend). Mide `GET /api/admin/pipelines/descartados`, que necesita
  UN superadmin real (sí se crea en `jax_users`, con rol `superadmin`).
- **"pipeline_con_muchos_eventos"**: UN pipeline del usuario "escala" con
  N_EVENTOS_RUIDO eventos `STEP_FAILED` (mismo número que midió el Ruling
  R20 para `sql_eventos_de_causa`, api/pipelines.py) más los 4 tipos de
  auditoría -- el PEOR caso de `GET /pipelines/{id}/auditoria-descarte`:
  "fires on EVERY pipeline-detail open" (coordinador), así que el pipeline
  medido tiene que ser el que más eventos acumula, no uno vacío.

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

# --- Forma C (fix round 1, BLOCK-2): "admin_muchos_usuarios" ---------------
# GET /admin/pipelines/descartados es GLOBAL (sin filtro de usuario) --
# forma de producción real es MUCHOS usuarios/tenants distintos, no un solo
# dueño con un historial largo (esa es la forma A/B, que miden la vista DEL
# DUEÑO). 5.000 filas entre 500 pares user_id/tenant_id -- mismo orden de
# magnitud que la forma B, para poder comparar el perfil de degradación.
N_ADMIN_DESCARTADOS = 5000
N_ADMIN_USUARIOS = 500

# --- Forma D (fix round 1, BLOCK-2): "pipeline_con_muchos_eventos" --------
# GET /pipelines/{id}/auditoria-descarte se pide en CADA apertura del
# detalle de un pipeline (coordinador) -- el peor caso es el pipeline con
# más eventos acumulados, no uno recién creado. 221 = el mismo número que
# midió el Ruling R20 (comentario de sql_eventos_de_causa, api/pipelines.py).
N_EVENTOS_RUIDO = 221


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


def _crear_usuario(cur, tenant_id, etiqueta, role="operator") -> int:
    email = f"carga-descartados-{etiqueta}-{uuid.uuid4().hex[:10]}@example.invalid"
    pw_hash = bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode()
    cur.execute(
        "INSERT INTO jax_users (tenant_id, email, password_hash, role, status, token_version) "
        "VALUES (%s, %s, %s, %s, 'active', 0)",
        (tenant_id, email, pw_hash, role),
    )
    return cur.lastrowid


def _filas_admin_muchos_usuarios(n_filas, n_usuarios, offset_id):
    """Forma C (fix round 1, BLOCK-2): N filas 'discarded' repartidas entre
    n_usuarios pares user_id/tenant_id DISTINTOS -- sin cuentas reales
    (GET /admin/pipelines/descartados lee jacobs_pipelines directo, sin
    JOIN a jax_users). Los user_id/tenant_id son strings reconocibles
    ('carga-admin-user-N'/'carga-admin-tenant-N') para que la limpieza los
    pueda encontrar con un LIKE, igual que
    test_explain_descartados_admin_usa_idx_pipelines_ocultos_sin_filesort."""
    ahora = time.time()
    filas = []
    ids = []
    for i in range(n_filas):
        pid = str(uuid.uuid4())
        creado = ahora - (offset_id + n_filas - i) * 2
        user_id = f"carga-admin-user-{i % n_usuarios}"
        tenant_id = f"carga-admin-tenant-{i % n_usuarios}"
        filas.append((pid, "carga descartar-pipelines (admin)", "plataforma", "supervised",
                      "discarded", creado, creado + 1, user_id, tenant_id, creado,
                      "aborted", user_id, creado + 1))
        ids.append(pid)
    return filas, ids


def _eventos_pipeline_con_muchos_eventos(pipeline_id):
    """Forma D (fix round 1, BLOCK-2): N_EVENTOS_RUIDO eventos STEP_FAILED
    (ruido, mismo tipo/número que midió el Ruling R20) + los 4 tipos de
    auditoría -- el pipeline con más eventos es el peor caso real de
    GET /pipelines/{id}/auditoria-descarte."""
    ahora = time.time()
    filas = []
    for i in range(N_EVENTOS_RUIDO):
        filas.append((pipeline_id, None, "STEP_FAILED", json.dumps({"step_index": i}), ahora + i))
    for i, tipo in enumerate(("PIPELINE_DISCARDED", "PIPELINE_RECOVERED", "PIPELINE_HIDDEN", "PIPELINE_RESTORED")):
        filas.append((pipeline_id, None, tipo,
                      json.dumps({"user_id": "carga", "desde": "a", "a": "b"}), ahora + N_EVENTOS_RUIDO + i))
    return filas


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

    # Forma C (BLOCK-2): superadmin real + N_ADMIN_DESCARTADOS filas
    # 'discarded' de N_ADMIN_USUARIOS pares distintos, para
    # GET /admin/pipelines/descartados.
    with conn.cursor() as cur:
        superadmin_id = _crear_usuario(cur, tenant_id, "superadmin", role="superadmin")
    conn.commit()
    filas_admin, ids_admin = _filas_admin_muchos_usuarios(
        N_ADMIN_DESCARTADOS, N_ADMIN_USUARIOS, N_VISIBLES_A + N_DESCARTADOS_A + N_VISIBLES_B + N_DESCARTADOS_B)
    with conn.cursor() as cur:
        _insertar(cur, filas_admin)
    conn.commit()
    print(f"admin_muchos_usuarios: superadmin_id={superadmin_id} "
          f"filas={len(ids_admin)} usuarios_distintos={N_ADMIN_USUARIOS}", file=sys.stderr)

    # Forma D (BLOCK-2): un pipeline del usuario "escala" con
    # N_EVENTOS_RUIDO + 4 eventos, para GET /pipelines/{id}/auditoria-descarte.
    # `owner_ack_at` seteado -- lo exige _require_pipeline_owner para no
    # devolver 404 de "pipeline no reconocido por su dueño".
    pipeline_eventos_id = str(uuid.uuid4())
    ahora = time.time()
    escala = usuarios["escala"]
    with conn.cursor() as cur:
        _insertar(cur, [(pipeline_eventos_id, "carga descartar-pipelines (eventos)", "plataforma", "supervised",
                         "completed", ahora, ahora, str(escala["user_id"]), escala["tenant_id"], ahora,
                         None, None, None)])
        cur.executemany(
            "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
            "VALUES (%s, %s, %s, %s, %s)",
            _eventos_pipeline_con_muchos_eventos(pipeline_eventos_id),
        )
    conn.commit()
    print(f"pipeline_con_muchos_eventos: pipeline_id={pipeline_eventos_id} "
          f"dueño=escala({escala['user_id']}) eventos={N_EVENTOS_RUIDO + 4}", file=sys.stderr)

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM jacobs_pipelines")
        (total_tabla,) = cur.fetchone()
    print(f"jacobs_pipelines total tras la siembra: {total_tabla}", file=sys.stderr)

    resultado = {
        "tenant_id": tenant_id, "usuarios": usuarios,
        "superadmin": {"user_id": superadmin_id, "tenant_id": tenant_id},
        "admin_muchos_usuarios": {"pipeline_ids": ids_admin},
        "pipeline_con_muchos_eventos": {
            "pipeline_id": pipeline_eventos_id,
            "owner_user_id": escala["user_id"], "owner_tenant_id": escala["tenant_id"],
        },
    }
    with open(sys.argv[1], "w") as f:
        json.dump(resultado, f)
    conn.close()
    print("SEED OK", file=sys.stderr)


if __name__ == "__main__":
    main()
