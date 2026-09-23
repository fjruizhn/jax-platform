"""Limpieza de la siembra de `descartados_seed.py` (Task 8, 2026-09-22).

Borra TODO lo que sembró la corrida (pipelines y los dos usuarios) contra
`jax_memory_test` -- JAMÁS `jax_memory`. `descartados_orquestar.py` la llama
sola al terminar (éxito o error); se puede correr a mano si una corrida se
cortó a la mitad:

    python3 loadtest/descartados_limpiar.py loadtest/_descartados_seed_result.json
"""
from __future__ import annotations

import json
import subprocess
import sys

import pymysql

from descartados_seed import BASE_DE_PRUEBA


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
        raise SystemExit("uso: descartados_limpiar.py <ruta-al-json-de-descartados_seed.py>")

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

    total_pipelines = 0
    total_usuarios = 0
    for etiqueta, datos in seed["usuarios"].items():
        pids = datos["todos_los_pipelines"]
        marcador = ", ".join(["%s"] * len(pids))
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM jacobs_pipelines WHERE pipeline_id IN ({marcador})", pids)
            n_pipelines = cur.rowcount
            cur.execute("DELETE FROM jax_users WHERE user_id = %s", (datos["user_id"],))
            n_usuarios = cur.rowcount
        conn.commit()
        print(f"usuario {etiqueta}: pipelines={n_pipelines} usuarios={n_usuarios}")
        total_pipelines += n_pipelines
        total_usuarios += n_usuarios

    # Fix round 1 (BLOCK-2): limpieza de las dos formas nuevas -- superadmin
    # + admin_muchos_usuarios + pipeline_con_muchos_eventos (y sus eventos).
    # `.get(...)` porque un JSON de una corrida VIEJA (de antes de este fix)
    # no tiene estas claves -- este script sigue pudiendo limpiar corridas
    # viejas sin reventar.
    if "superadmin" in seed:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM jax_users WHERE user_id = %s", (seed["superadmin"]["user_id"],))
            n_superadmin = cur.rowcount
        conn.commit()
        print(f"superadmin: usuarios={n_superadmin}")
        total_usuarios += n_superadmin

    if "admin_muchos_usuarios" in seed:
        pids = seed["admin_muchos_usuarios"]["pipeline_ids"]
        marcador = ", ".join(["%s"] * len(pids))
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM jacobs_pipelines WHERE pipeline_id IN ({marcador})", pids)
            n_admin = cur.rowcount
        conn.commit()
        print(f"admin_muchos_usuarios: pipelines={n_admin}")
        total_pipelines += n_admin

    if "pipeline_con_muchos_eventos" in seed:
        pid = seed["pipeline_con_muchos_eventos"]["pipeline_id"]
        with conn.cursor() as cur:
            cur.execute("DELETE FROM jacobs_events WHERE pipeline_id = %s", (pid,))
            n_eventos = cur.rowcount
            cur.execute("DELETE FROM jacobs_pipelines WHERE pipeline_id = %s", (pid,))
            n_pipeline_eventos = cur.rowcount
        conn.commit()
        print(f"pipeline_con_muchos_eventos: pipelines={n_pipeline_eventos} eventos={n_eventos}")
        total_pipelines += n_pipeline_eventos

    with conn.cursor() as cur:
        restantes = 0
        for datos in seed["usuarios"].values():
            cur.execute("SELECT COUNT(*) FROM jacobs_pipelines WHERE user_id=%s", (str(datos["user_id"]),))
            (n,) = cur.fetchone()
            restantes += n
        if "admin_muchos_usuarios" in seed:
            pids = seed["admin_muchos_usuarios"]["pipeline_ids"]
            marcador = ", ".join(["%s"] * len(pids))
            cur.execute(f"SELECT COUNT(*) FROM jacobs_pipelines WHERE pipeline_id IN ({marcador})", pids)
            (n,) = cur.fetchone()
            restantes += n
        if "pipeline_con_muchos_eventos" in seed:
            pid_eventos = seed["pipeline_con_muchos_eventos"]["pipeline_id"]
            cur.execute("SELECT COUNT(*) FROM jacobs_pipelines WHERE pipeline_id=%s", (pid_eventos,))
            (n,) = cur.fetchone()
            restantes += n
            # MINOR-C (fix round 2, revisión adversarial de PR 151): la
            # verificación final SÓLO contaba jacobs_pipelines -- nunca
            # jacobs_events, que es donde viven las N_EVENTOS_AUDITORIA
            # filas que este mismo bloque acaba de borrar más arriba. Sin
            # esto, un DELETE de jacobs_events que fallara (o que borrara
            # de menos) quedaba sin verificación independiente -- el mismo
            # mecanismo que dejó ~940.000 eventos huérfanos en
            # jax_memory_test (otros scripts de carga, documentado en el
            # reporte de la ronda 1).
            cur.execute("SELECT COUNT(*) FROM jacobs_events WHERE pipeline_id=%s", (pid_eventos,))
            (n_eventos_restantes,) = cur.fetchone()
            restantes += n_eventos_restantes
    print(f"verificación final: filas_restantes_de_la_siembra={restantes} "
          f"(borrados: pipelines={total_pipelines} usuarios={total_usuarios})")
    if restantes != 0:
        raise RuntimeError("quedaron restos sin borrar -- revisar a mano contra jax_memory_test")
    conn.close()
    print("LIMPIEZA OK")


if __name__ == "__main__":
    main()
