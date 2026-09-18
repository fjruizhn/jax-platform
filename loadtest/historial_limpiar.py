"""Limpieza de la siembra de `historial_seed.py` (Task 10, 2026-09-18).

Borra TODO lo que sembró la corrida (pipelines, eventos, uso real y de
relleno, usuario) contra `jax_memory_test` -- JAMÁS `jax_memory` -- y verifica
que no quede resto. `historial_orquestar.py` la llama sola al terminar (éxito
o error); se puede correr a mano si una corrida se cortó a la mitad:

    python3 loadtest/historial_limpiar.py loadtest/_seed_result.json
"""
from __future__ import annotations

import json
import subprocess
import sys

import pymysql

from historial_seed import BASE_DE_PRUEBA


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
        raise SystemExit("uso: historial_limpiar.py <ruta-al-json-de-historial_seed.py>")

    env = _cargar_env_produccion()
    seed = json.loads(open(sys.argv[1]).read())
    conn = pymysql.connect(
        host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]),
        user=env["JAX_DB_USER"], password=env["JAX_DB_PASSWORD"],
        database=BASE_DE_PRUEBA, autocommit=False, charset="utf8mb4",
    )
    with conn.cursor() as cur:
        cur.execute("SELECT DATABASE()")
        (db,) = cur.fetchone()
    if db != BASE_DE_PRUEBA:
        raise RuntimeError(f"conectado a {db!r}, no a {BASE_DE_PRUEBA!r} -- ABORTANDO la limpieza")

    pids = seed["todos_los_pipelines"]
    marcador = ", ".join(["%s"] * len(pids))

    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM jacobs_events WHERE pipeline_id IN ({marcador})", pids)
        n_eventos = cur.rowcount
        cur.execute(f"DELETE FROM axioma_usage WHERE pipeline_id IN ({marcador})", pids)
        n_uso_real = cur.rowcount
        cur.execute("DELETE FROM axioma_usage WHERE tenant_id = %s", (seed["marcador_tenant_filler"],))
        n_uso_filler = cur.rowcount
        cur.execute(f"DELETE FROM jacobs_pipelines WHERE pipeline_id IN ({marcador})", pids)
        n_pipelines = cur.rowcount
        cur.execute("DELETE FROM jax_users WHERE user_id = %s", (seed["user_id"],))
        n_usuarios = cur.rowcount
    conn.commit()
    print(f"borrados: eventos={n_eventos} uso_real={n_uso_real} uso_filler={n_uso_filler} "
          f"pipelines={n_pipelines} usuarios={n_usuarios}")

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM axioma_usage")
        (usage_total,) = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM jacobs_pipelines WHERE user_id=%s", (str(seed["user_id"]),))
        (pl_restantes,) = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM jax_users WHERE user_id=%s", (seed["user_id"],))
        (u_restantes,) = cur.fetchone()
    print(f"verificación final: axioma_usage_total={usage_total} "
          f"pipelines_restantes_del_user={pl_restantes} usuario_restante={u_restantes}")
    if pl_restantes != 0 or u_restantes != 0:
        raise RuntimeError("quedaron restos sin borrar -- revisar a mano contra jax_memory_test")
    conn.close()
    print("LIMPIEZA OK")


if __name__ == "__main__":
    main()
