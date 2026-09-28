"""Carga de OFFSET profundo del listado de descartados (2026-09-23) -- ORQUESTADOR.

Pendiente declarado SIN MEDIR en docs/carga-descartados-pipelines-2026-09-22.md
("No se midió `OFFSET` profundo"): la interfaz recorre el historial pidiendo
`offset` creciente, y `LIMIT n OFFSET m` obliga al motor a leer y descartar
las `m` filas anteriores. Este script mide cuánto cuesta eso, en los dos
listados de descartados que aceptan `offset`:

- `GET /api/pipelines?estado=discarded&offset=X` (vista del dueño,
  SQL_DESCARTADOS_DEL_USUARIO) -- usuarios "escala" y "extremo".
- `GET /api/admin/pipelines/descartados?offset=X` (superadmin,
  SQL_DESCARTADOS_ADMIN) -- todas las filas `discarded` de la base.

Por cada offset mide (a) HTTP de punta a punta a c=1 y c=25 y (b) el SQL
REAL, sin HTTP: `EXPLAIN`, `ANALYZE` (plan ejecutado, r_rows) y la
diferencia de `Handler_read_*` de la sesión, más el tiempo de la consulta
sola (mediana de 30 ejecuciones). Además, SÓLO como evidencia para la
propuesta (no es código de la aplicación), la misma página pedida por
cursor/keyset sobre el mismo índice.

REUSA el arnés de descartados_orquestar.py (mismas barreras: `jax_memory_test`
fijo, puertos 18081/17778, JWT propio comparado contra el de producción
releído de /proc/<pid>/environ) y la misma siembra/limpieza
(descartados_seed.py / descartados_limpiar.py). La limpieza corre SIEMPRE.

USO (desde la raíz del repo):
    JAX_REPO_PATH=/ruta/a/checkout/de/jax \
    python3 loadtest/descartados_offset_medir.py [etiqueta-de-corrida]
    CARGA_N_DESCARTADOS_EXTREMO=50000 ...   # histórico más largo (pendiente)

Resultados en loadtest/_descartados_resultados_offset_<etiqueta>.json
(ignorado por git, patrón `_descartados_resultados*.json`).
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pymysql

sys.path.insert(0, str(Path(__file__).parent))
import descartados_orquestar as base  # noqa: E402  (mismo arnés, mismas barreras)

BASE_DE_PRUEBA = base.BASE_DE_PRUEBA
LIMITE = 50  # LISTA_PIPELINES_MAX / LIMITE_MAX por defecto: el que pide la interfaz
CONCURRENCIAS = {1: 200, 25: 500}  # c -> n
REPETICIONES_SQL = 30

SQL_USUARIO = ("SELECT pipeline_id, name, status, created_at, updated_at, descartado_at "
               "FROM jacobs_pipelines "
               "WHERE user_id=%s AND tenant_id=%s AND owner_ack_at IS NOT NULL AND status='discarded' "
               "ORDER BY descartado_at DESC LIMIT %s OFFSET %s")
SQL_ADMIN = ("SELECT pipeline_id, name, user_id, tenant_id, descartado_por, descartado_at, created_at "
             "FROM jacobs_pipelines WHERE status='discarded' "
             "ORDER BY descartado_at DESC LIMIT %s OFFSET %s")
# Sólo para la propuesta (NO está en la aplicación): la misma página por
# cursor (descartado_at, pipeline_id) del último elemento de la anterior.
SQL_USUARIO_KEYSET = ("SELECT pipeline_id, name, status, created_at, updated_at, descartado_at "
                      "FROM jacobs_pipelines "
                      "WHERE user_id=%s AND tenant_id=%s AND owner_ack_at IS NOT NULL AND status='discarded' "
                      "AND (descartado_at < %s OR (descartado_at = %s AND pipeline_id < %s)) "
                      "ORDER BY descartado_at DESC, pipeline_id DESC LIMIT %s")
SQL_ADMIN_KEYSET = ("SELECT pipeline_id, name, user_id, tenant_id, descartado_por, descartado_at, created_at "
                    "FROM jacobs_pipelines WHERE status='discarded' "
                    "AND (descartado_at < %s OR (descartado_at = %s AND pipeline_id < %s)) "
                    "ORDER BY descartado_at DESC, pipeline_id DESC LIMIT %s")


def _offsets(total: int) -> list[int]:
    """0, 100, 1000, 2500, 5000, ... hasta la ÚLTIMA página (total-LIMITE)."""
    candidatos = [0, 100, 1000, 2500, 5000, 10000, 25000]
    ultimo = max(0, total - LIMITE)
    return sorted({o for o in candidatos if o < ultimo} | {ultimo})


def _conectar(env: dict):
    conn = pymysql.connect(host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]),
                           user=env["JAX_DB_USER"], password=env["JAX_DB_PASSWORD"],
                           database=BASE_DE_PRUEBA, autocommit=True, charset="utf8mb4")
    with conn.cursor() as cur:
        cur.execute("SELECT DATABASE()")
        (db,) = cur.fetchone()
    if db != BASE_DE_PRUEBA:
        raise RuntimeError(f"conectado a {db!r}, no a {BASE_DE_PRUEBA!r} -- ABORTANDO")
    return conn


def _handler_read(cur) -> dict:
    cur.execute("SHOW SESSION STATUS LIKE 'Handler_read%'")
    return {k: int(v) for k, v in cur.fetchall()}


def _sql_medir(conn, sql: str, params: tuple) -> dict:
    """EXPLAIN + ANALYZE + Handler_read + tiempo de la consulta sola.
    Todo es lectura (SELECT/EXPLAIN/ANALYZE SELECT/SHOW STATUS)."""
    with conn.cursor() as cur:
        cur.execute("EXPLAIN " + sql, params)
        cols = [d[0] for d in cur.description]
        explain = [dict(zip(cols, f)) for f in cur.fetchall()]
        cur.execute("ANALYZE " + sql, params)
        cols = [d[0] for d in cur.description]
        analyze = [dict(zip(cols, f)) for f in cur.fetchall()]
        cur.execute("ANALYZE FORMAT=JSON " + sql, params)
        analyze_json = json.loads(cur.fetchone()[0])
        antes = _handler_read(cur)
        cur.execute(sql, params)
        filas = cur.fetchall()
        despues = _handler_read(cur)
        # SHOW STATUS mismo suma 1 Handler_read_rnd_next por fila de su
        # resultado; se descuenta midiendo dos SHOW STATUS seguidos.
        base0 = _handler_read(cur)
        base1 = _handler_read(cur)
        delta = {k: despues[k] - antes[k] - (base1[k] - base0[k]) for k in despues}
        tiempos = []
        for _ in range(REPETICIONES_SQL):
            t0 = time.perf_counter()
            cur.execute(sql, params)
            cur.fetchall()
            tiempos.append((time.perf_counter() - t0) * 1000)
    tiempos.sort()
    q = analyze_json.get("query_block", {})
    return {
        "explain": [{k: e.get(k) for k in ("type", "key", "rows", "Extra")} for e in explain],
        "analyze": [{k: a.get(k) for k in ("type", "key", "rows", "r_rows", "filtered", "r_filtered", "Extra")}
                    for a in analyze],
        "r_total_time_ms": q.get("r_total_time_ms"),
        "handler_read": {k: v for k, v in delta.items() if v},
        "handler_read_total": sum(delta.values()),
        "filas_devueltas": len(filas),
        "sql_ms_p50": round(statistics.median(tiempos), 3),
        "sql_ms_p95": round(tiempos[int(0.95 * len(tiempos)) - 1], 3),
        "ultima_fila": filas[-1] if filas else None,
    }


async def _http(url: str, headers: dict) -> dict:
    salida = {}
    for c, n in CONCURRENCIAS.items():
        salida[str(c)] = await base.correr_tanda(url, headers, c, n)
    return salida


async def main_async() -> None:
    etiqueta_corrida = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%H%M%S")
    tmp = Path(tempfile.mkdtemp(prefix="carga-offset-descartados-"))
    seed_json = tmp / "seed_result.json"

    print(f"[offset] sembrando (descartados_seed.py) -> {seed_json}")
    subprocess.run([sys.executable, str(base.LOADTEST_DIR / "descartados_seed.py"), str(seed_json)], check=True)
    seed = json.loads(seed_json.read_text())

    env = base.construir_env(tmp)
    base._verificar_no_apunta_a_produccion(env)

    log_jacobs = open(tmp / "fake_jacobs.log", "w")
    log_backend = open(tmp / "backend.log", "w")
    proc_jacobs = proc_backend = None
    try:
        proc_jacobs = subprocess.Popen(
            [sys.executable, str(base.LOADTEST_DIR / "historial_fake_jacobs.py")],
            env=env, stdout=log_jacobs, stderr=subprocess.STDOUT, start_new_session=True)
        base.esperar_puerto("127.0.0.1", base.FAKE_JACOBS_PORT)
        proc_backend = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
             "--port", str(base.BACKEND_PORT), "--log-level", "warning"],
            cwd=str(base.BACKEND_DIR), env=env, stdout=log_backend, stderr=subprocess.STDOUT,
            start_new_session=True)
        base.esperar_puerto("127.0.0.1", base.BACKEND_PORT, timeout=60.0)
        base.esperar_http_ok(f"{base.BACKEND_URL}/api/health", timeout=60.0)

        environ = Path(f"/proc/{proc_backend.pid}/environ").read_bytes()
        pares = dict(p.split(b"=", 1) for p in environ.split(b"\x00") if b"=" in p)
        db_name = pares.get(b"JAX_DB_NAME", b"").decode()
        if db_name != BASE_DE_PRUEBA:
            raise RuntimeError(f"proceso real con JAX_DB_NAME={db_name!r} -- ABORTANDO")
        print(f"[offset] VERIFICADO /proc/{proc_backend.pid}/environ: JAX_DB_NAME={db_name}")
        jwt_carga = pares.get(b"JAX_JWT_SECRET", b"").decode()
        base._abortar_si_el_secreto_de_carga_coincide_con_produccion(
            jwt_carga, base._cargar_env_produccion().get("JAX_JWT_SECRET"))

        conn = _conectar(env)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM jacobs_pipelines WHERE status='discarded'")
            (total_admin,) = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM jacobs_pipelines")
            (total_tabla,) = cur.fetchone()
        resultados = {"corrida": etiqueta_corrida, "limite": LIMITE,
                      "jacobs_pipelines_total": total_tabla, "descartados_total": total_admin,
                      "series": []}

        series = []
        for etiqueta, datos in seed["usuarios"].items():
            token = base._token_para(datos["user_id"], datos["tenant_id"], jwt_carga)
            series.append({
                "serie": f"usuario_{etiqueta}", "total": datos["n_descartados"],
                "headers": {"Authorization": f"Bearer {token}"},
                "url": f"{base.BACKEND_URL}/api/pipelines?estado=discarded&limite={LIMITE}&offset={{o}}",
                "sql": SQL_USUARIO, "params": lambda o, d=datos: (str(d["user_id"]), d["tenant_id"], LIMITE + 1, o),
                "sql_keyset": SQL_USUARIO_KEYSET,
                "params_keyset": lambda f, d=datos: (str(d["user_id"]), d["tenant_id"], f[5], f[5], f[0], LIMITE + 1),
            })
        token_sa = base._token_para(seed["superadmin"]["user_id"], seed["superadmin"]["tenant_id"],
                                    jwt_carga, role="superadmin")
        series.append({
            "serie": "admin", "total": total_admin,
            "headers": {"Authorization": f"Bearer {token_sa}"},
            "url": f"{base.BACKEND_URL}/api/admin/pipelines/descartados?limite={LIMITE}&offset={{o}}",
            "sql": SQL_ADMIN, "params": lambda o: (LIMITE + 1, o),
            "sql_keyset": SQL_ADMIN_KEYSET,
            "params_keyset": lambda f: (f[5], f[5], f[0], LIMITE + 1),
        })

        for s in series:
            for o in _offsets(s["total"]):
                url = s["url"].format(o=o)
                r = httpx.get(url, headers=s["headers"], timeout=30.0)
                cuerpo = r.json()
                if r.status_code != 200 or not cuerpo.get("pipelines"):
                    raise RuntimeError(f"verificación previa {s['serie']} offset={o}: "
                                       f"status={r.status_code} pipelines={len(cuerpo.get('pipelines', []))}")
                previa = {"status": r.status_code, "bytes": len(r.content),
                          "pipelines": len(cuerpo["pipelines"]), "has_more": cuerpo.get("has_more")}
                sql = _sql_medir(conn, s["sql"], s["params"](o))
                keyset = None
                if o > 0:
                    # Cursor = última fila de la página ANTERIOR (offset o-LIMITE):
                    # debe devolver exactamente la misma página que OFFSET o.
                    with conn.cursor() as cur:
                        cur.execute(s["sql"], s["params"](o - LIMITE))
                        pag_ant = cur.fetchall()[:LIMITE]
                        cur.execute(s["sql"], s["params"](o))
                        pag_off = [f[0] for f in cur.fetchall()]
                    keyset = _sql_medir(conn, s["sql_keyset"], s["params_keyset"](pag_ant[-1]))
                    with conn.cursor() as cur:
                        cur.execute(s["sql_keyset"], s["params_keyset"](pag_ant[-1]))
                        pag_key = [f[0] for f in cur.fetchall()]
                    keyset["misma_pagina_que_offset"] = pag_key == pag_off
                http = await _http(url, s["headers"])
                fila = {"serie": s["serie"], "offset": o, "previa": previa, "sql": sql,
                        "keyset": keyset, "http": http}
                for k in ("sql", "keyset"):
                    if fila[k]:
                        fila[k].pop("ultima_fila", None)
                resultados["series"].append(fila)
                print(f"[{s['serie']}] offset={o} previa={previa} "
                      f"handler_read={sql['handler_read_total']} sql_p50={sql['sql_ms_p50']}ms "
                      f"keyset_hr={keyset and keyset['handler_read_total']} "
                      f"keyset_igual={keyset and keyset['misma_pagina_que_offset']} "
                      f"c1_p95={http['1']['p95_ms']} c25_p95={http['25']['p95_ms']} c25_rps={http['25']['rps']}")
                print(f"    EXPLAIN={sql['explain']} ANALYZE={sql['analyze']}")
        conn.close()

        salida = base.LOADTEST_DIR / f"_descartados_resultados_offset_{etiqueta_corrida}.json"
        salida.write_text(json.dumps(resultados, indent=2, ensure_ascii=False, default=str))
        print(f"[offset] {salida} escrito")
    finally:
        for proc in (proc_backend, proc_jacobs):
            if proc is not None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except ProcessLookupError:  # fail-soft: ya no existe
                    pass
        for proc in (proc_backend, proc_jacobs):
            if proc is not None:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except ProcessLookupError:  # fail-soft: el SIGTERM ya lo tumbó
                        pass
        log_jacobs.close()
        log_backend.close()
        print("[offset] procesos detenidos; limpiando la siembra (descartados_limpiar.py)...")
        subprocess.run([sys.executable, str(base.LOADTEST_DIR / "descartados_limpiar.py"), str(seed_json)],
                       check=True)


if __name__ == "__main__":
    asyncio.run(main_async())
