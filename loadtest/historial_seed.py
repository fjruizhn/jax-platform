"""Siembra del PEOR CASO para la carga del historial de pipelines (Task 10,
2026-09-18, docs/carga-historial-2026-09-18.md).

Crea, en `jax_memory_test` (JAMÁS `jax_memory`): un usuario descartable, N_PIPELINES
pipelines (los N_ABORTED más recientes quedan en la PRIMERA página del listado y
disparan la consulta de causa + costo a la vez), sus eventos de causa, filas reales
de `axioma_usage` con `pipeline_id` (para que `costo_usd` resuelva de verdad) y
N_USAGE_FILLER filas de relleno (set-based, motor SEQUENCE de MariaDB) para llevar
la tabla al mismo orden de magnitud que la ronda de carga anterior (2026-09-17).

USO:
    python3 loadtest/historial_seed.py loadtest/_seed_result.json

Requiere `sudo -n cat /etc/jax/.env` (mismo mecanismo que
backend/tests/entorno_de_produccion.py) para las credenciales de conexión.
El resultado (ids sembrados) se escribe en el JSON que se le pasa por
argumento -- lo consumen historial_orquestar.py y historial_limpiar.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid

import bcrypt
import pymysql

# ---------------------------------------------------------------------------
# CONSTANTES DEL PEOR CASO -- visibles acá, no enterradas en el cuerpo.
# ---------------------------------------------------------------------------
BASE_DE_PRUEBA = "jax_memory_test"          # la ÚNICA base a la que este script escribe
N_PIPELINES = 600                           # "usuario con MUCHOS pipelines", no tres
N_ABORTED = 50                              # los más recientes -> 1a página, peor caso de causa_de
EVENTOS_POR_ABORTADO_FILLER = 200           # eventos que NO son de causa (ruido real del pipeline)
EVENTOS_POR_ABORTADO_FALLO = 20             # STEP_FAILED reales antes del PIPELINE_ABORTED
N_USAGE_FILLER = 2_860_000                  # misma escala que la ronda de carga anterior (~2,86M filas)
MARCADOR_TENANT_FILLER = 999999             # tenant_id del relleno -> se borra por completo con este filtro
TEXTO_ERROR_CHARS = 300                     # tamaño del motivo de error, igual que la ronda anterior


def _cargar_env_produccion() -> dict:
    r = subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"], capture_output=True, text=True, check=True)
    env = {}
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            env[k.strip()] = v.strip()
    return env


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("uso: historial_seed.py <ruta-de-salida.json>")

    env = _cargar_env_produccion()
    conn = pymysql.connect(
        host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]),
        user=env["JAX_DB_USER"], password=env["JAX_DB_PASSWORD"],
        database=BASE_DE_PRUEBA, autocommit=False, charset="utf8mb4",
    )
    # BARRERA DURA, no un assert (que -O puede pelar): sin esto no se escribe
    # una sola fila. Ver historial_orquestar.py y historial_limpiar.py, que
    # repiten la MISMA verificación por su cuenta -- ninguno de los tres
    # confía en que otro ya la hizo.
    with conn.cursor() as cur:
        cur.execute("SELECT DATABASE()")
        (db,) = cur.fetchone()
    if db != BASE_DE_PRUEBA:
        raise RuntimeError(
            f"conectado a {db!r}, no a {BASE_DE_PRUEBA!r} -- ABORTANDO sin escribir nada. "
            f"Este script SOLO puede sembrar contra la base de pruebas.")
    print(f"conectado a {db}", file=sys.stderr)

    with conn.cursor() as cur:
        email = f"carga-historial-{uuid.uuid4().hex[:10]}@example.invalid"
        pw_hash = bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode()
        cur.execute(
            "INSERT INTO jax_users (tenant_id, email, password_hash, role, status, token_version) "
            "VALUES (1, %s, %s, 'operator', 'active', 0)",
            (email, pw_hash),
        )
        user_id = cur.lastrowid
    conn.commit()
    print(f"usuario de carga user_id={user_id} email={email}", file=sys.stderr)

    ahora = time.time()
    pipelines = []
    for i in range(N_PIPELINES):
        pid = str(uuid.uuid4())
        # Los últimos N_ABORTED (created_at MÁS ALTO) son 'aborted': quedan en
        # la PRIMERA página (ORDER BY created_at DESC LIMIT 50) -- GET
        # /api/pipelines sin offset ya golpea el peor caso solo.
        es_aborted = i >= (N_PIPELINES - N_ABORTED)
        creado = ahora - (N_PIPELINES - i) * 5
        actualizado = creado + 42.0
        pipelines.append((pid, es_aborted, creado, actualizado))

    big_pipeline_id = pipelines[0][0]  # uno 'completed' -- el detalle grande se pide por id directo

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO jacobs_pipelines "
            "(pipeline_id, name, invoked_by, mode, status, created_at, updated_at, "
            " user_id, tenant_id, owner_ack_at, run_epoch, depth) "
            "VALUES (%s, %s, 'plataforma', 'supervised', %s, %s, %s, %s, 1, %s, 0, 0)",
            [
                (pid, f"pipeline de carga {i}", "aborted" if es_ab else "completed",
                 creado, actualizado, str(user_id), creado)
                for i, (pid, es_ab, creado, actualizado) in enumerate(pipelines)
            ],
        )
    conn.commit()
    print(f"{N_PIPELINES} pipelines insertados ({N_ABORTED} aborted en la primera página)", file=sys.stderr)

    abortados = [pid for pid, es_ab, _, _ in pipelines if es_ab]
    filas_eventos = []
    texto_error = "x" * TEXTO_ERROR_CHARS
    for pid in abortados:
        for _ in range(EVENTOS_POR_ABORTADO_FILLER):
            filas_eventos.append((pid, "STEP_STARTED", None, ahora))
        for _ in range(EVENTOS_POR_ABORTADO_FALLO):
            payload = json.dumps({"step_index": 3, "error": texto_error})
            filas_eventos.append((pid, "STEP_FAILED", payload, ahora))
        payload_abort = json.dumps({"failed_steps": [3], "errores": {"3": texto_error}})
        filas_eventos.append((pid, "PIPELINE_ABORTED", payload_abort, ahora))

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
            "VALUES (%s, NULL, %s, %s, %s)",
            filas_eventos,
        )
    conn.commit()
    print(f"{len(filas_eventos)} eventos insertados para {len(abortados)} pipelines abortados", file=sys.stderr)

    # axioma_usage real, con pipeline_id, para que costo_usd resuelva en los 600.
    filas_uso = []
    for pid, _es_ab, creado, _ in pipelines:
        for _ in range(2):
            filas_uso.append((1, user_id, "jekyll", "modelo-de-carga", 500, 1500, 0.045678,
                              "pipeline", creado, pid))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO axioma_usage "
            "(tenant_id, user_id, facet, model, tokens_in, tokens_out, cost_usd, request_type, created_at, pipeline_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FROM_UNIXTIME(%s), %s)",
            filas_uso,
        )
    conn.commit()
    print(f"{len(filas_uso)} filas de uso reales insertadas (costo_usd real por pipeline)", file=sys.stderr)

    # Volumen: filler de axioma_usage SIN pipeline_id, marcado con
    # MARCADOR_TENANT_FILLER para poder borrarlo por completo después.
    # Set-based (motor SEQUENCE de MariaDB), no un loop en Python.
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO axioma_usage "
            "(tenant_id, user_id, facet, model, tokens_in, tokens_out, cost_usd, request_type, created_at) "
            "SELECT %s, 1, 'jekyll', 'modelo-de-relleno', 100, 200, 0.001234, 'chat', "
            "       DATE_SUB(NOW(), INTERVAL (seq %% 90) DAY) "
            "FROM seq_1_to_%s" % (MARCADOR_TENANT_FILLER, N_USAGE_FILLER)
        )
    conn.commit()
    print(f"{N_USAGE_FILLER} filas de relleno insertadas en axioma_usage en {time.time()-t0:.1f}s", file=sys.stderr)

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM axioma_usage")
        (total,) = cur.fetchone()
    print(f"axioma_usage total ahora: {total}", file=sys.stderr)

    resultado = {
        "user_id": user_id,
        "tenant_id": "1",
        "email": email,
        "big_pipeline_id": big_pipeline_id,
        "abortados": abortados,
        "todos_los_pipelines": [p[0] for p in pipelines],
        "marcador_tenant_filler": MARCADOR_TENANT_FILLER,
        "n_usage_filler": N_USAGE_FILLER,
        "axioma_usage_total": total,
    }
    with open(sys.argv[1], "w") as f:
        json.dump(resultado, f)
    conn.close()
    print("SEED OK", file=sys.stderr)


if __name__ == "__main__":
    main()
