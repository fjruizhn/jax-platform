"""Descartados INTERCALADOS de muchos usuarios: plan por índice y cursor vs offset (2026-09-23).

Cierra lo que docs/carga-descartados-offset-2026-09-23.md (jax-platform#157)
dejó "inferido del mecanismo, NO medido": en la siembra de esa corrida cada
usuario tenía sus descartados en un bloque CONTIGUO de tiempo, así que el plan
malo de `SQL_DESCARTADOS_DEL_USUARIO` (el índice GLOBAL `idx_pipelines_ocultos`)
sólo pagó +1.000 lecturas. En producción los descartes de todos los usuarios
se INTERCALAN en el tiempo: acá se siembran así (dueños barajados con semilla
fija, `descartado_at` decreciente) y se mide.

Dos modos:

- `sql` (parte A): SQL solo, sin HTTP. Por cada usuario medido y cada
  offset, la consulta de master (sin hint) y la misma con `FORCE INDEX` de
  cada uno de los dos índices; además la página por cursor (el SQL nuevo de
  api/paginacion_descartados.py) con cada índice forzado. Por variante:
  `EXPLAIN` (clave elegida), `ANALYZE` (`r_rows`), `Handler_read` real de la
  sesión y p50/p95 de REPETICIONES ejecuciones.
- `http` (parte E): levanta el backend REAL de este checkout (mismo arnés y
  barreras que descartados_orquestar.py: `jax_memory_test` fijo, puertos
  18081/17778, JWT propio comparado contra el de producción releído de
  /proc/<pid>/environ) y mide la página profunda pedida por `offset` y por
  `cursor` a c=1 y c=25, en la vista del dueño y en la del superadmin.

Barreras: sólo escribe en `jax_memory_test` (verificado con
`SELECT DATABASE()` antes de la primera fila); borra lo sembrado POR ID en un
`finally` y verifica que no quede ninguna fila ni usuario; nunca toca
`jax_memory` ni los puertos 7777/8080. El esquema de jax se asegura con
`descartados_seed._asegurar_esquema` (JAX_REPO_PATH).

USO (desde la raíz del repo):
    JAX_REPO_PATH=/ruta/a/jax python3 loadtest/descartados_intercalados_medir.py sql r1
    JAX_REPO_PATH=/ruta/a/jax python3 loadtest/descartados_intercalados_medir.py http r1

Tamaños (variables de entorno): CARGA_INT_OTROS (50000 descartados de
CARGA_INT_USUARIOS=500 usuarios), CARGA_INT_PESADO (5000, el usuario medido
en profundidad), CARGA_INT_LIVIANO (3). Resultados en
loadtest/_descartados_resultados_intercalados_<modo>_<etiqueta>.json (ignorado
por git).
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import pymysql

LOADTEST_DIR = Path(__file__).parent
sys.path.insert(0, str(LOADTEST_DIR))
sys.path.insert(0, str(LOADTEST_DIR.parent / "backend"))
import descartados_orquestar as base  # noqa: E402  (mismo arnés, mismas barreras)
from descartados_seed import (  # noqa: E402
    BASE_DE_PRUEBA, _asegurar_esquema, _cargar_env_produccion, _crear_usuario, _insertar,
)
from api.paginacion_descartados import (  # noqa: E402  (el SQL de la aplicación, no una copia)
    DESPUES_DEL_CURSOR, ORDEN,
)

assert base.BASE_DE_PRUEBA == BASE_DE_PRUEBA

N_OTROS = int(os.environ.get("CARGA_INT_OTROS", "50000"))
N_USUARIOS = int(os.environ.get("CARGA_INT_USUARIOS", "500"))
N_PESADO = int(os.environ.get("CARGA_INT_PESADO", "5000"))
N_LIVIANO = int(os.environ.get("CARGA_INT_LIVIANO", "3"))
LIMITE = 50
REPETICIONES = 50
CONCURRENCIAS = {1: 200, 25: 500}
SEMILLA = 20260923
TENANT_OTROS = "carga-intercalados"

COLS = "pipeline_id, name, status, created_at, updated_at, descartado_at "
WHERE_USUARIO = ("WHERE user_id=%s AND tenant_id=%s AND owner_ack_at IS NOT NULL "
                 "AND status='discarded' ")
HINTS = {
    "sin_hint": "",
    "force_descartados": "FORCE INDEX (idx_pipelines_descartados) ",
    "force_ocultos": "FORCE INDEX (idx_pipelines_ocultos) ",
}


def sql_master(hint: str) -> str:
    """La consulta de master ANTES de este cambio (orden sólo por fecha),
    con o sin hint."""
    return (f"SELECT {COLS}FROM jacobs_pipelines {hint}{WHERE_USUARIO}"
            "ORDER BY descartado_at DESC LIMIT %s OFFSET %s")


def sql_cursor(hint: str) -> str:
    return f"SELECT {COLS}FROM jacobs_pipelines {hint}{WHERE_USUARIO}{DESPUES_DEL_CURSOR}{ORDEN}LIMIT %s"


def _conectar(env: dict, autocommit=True):
    conn = pymysql.connect(host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]),
                           user=env["JAX_DB_USER"], password=env["JAX_DB_PASSWORD"],
                           database=BASE_DE_PRUEBA, autocommit=autocommit, charset="utf8mb4")
    with conn.cursor() as cur:
        cur.execute("SELECT DATABASE()")
        (db,) = cur.fetchone()
    if db != BASE_DE_PRUEBA:
        conn.close()
        raise RuntimeError(f"conectado a {db!r}, no a {BASE_DE_PRUEBA!r} -- ABORTANDO sin escribir nada")
    return conn


# ---------------------------------------------------------------------------
# Siembra / limpieza
# ---------------------------------------------------------------------------
def sembrar(conn, sembrado: dict) -> None:
    """Tres usuarios reales (jax_users, tenant '1': el backend los autentica)
    -- pesado, liviano y el superadmin -- más N_OTROS descartados de
    N_USUARIOS usuarios sin cuenta (el SQL no hace JOIN con jax_users). Los
    dueños se BARAJAN con semilla fija antes de asignar `descartado_at`
    decreciente: cada descarte de cualquier usuario cae entre descartes de
    otros, como en producción."""
    # `sembrado` se llena A MEDIDA que se escribe: si algo falla a la
    # mitad, la limpieza del `finally` sabe qué borrar.
    sembrado["ids"] = []
    with conn.cursor() as cur:
        pesado = sembrado["pesado"] = _crear_usuario(cur, "1", "int-pesado")
        liviano = sembrado["liviano"] = _crear_usuario(cur, "1", "int-liviano")
        sembrado["superadmin"] = _crear_usuario(cur, "1", "int-superadmin", role="superadmin")
    conn.commit()
    sembrado["tipico"] = "carga-int-u7"
    duenios = ([(str(pesado), "1")] * N_PESADO + [(str(liviano), "1")] * N_LIVIANO
               + [(f"carga-int-u{i % N_USUARIOS}", TENANT_OTROS) for i in range(N_OTROS)])
    random.Random(SEMILLA).shuffle(duenios)
    ahora = time.time()
    filas, ids = [], sembrado["ids"]
    for k, (user_id, tenant_id) in enumerate(duenios):
        pid = str(uuid.uuid4())
        descartado_at = ahora - k * 1.5
        creado = descartado_at - 60.0
        filas.append((pid, "carga descartados intercalados", "plataforma", "supervised",
                      "discarded", creado, descartado_at, user_id, tenant_id, creado,
                      "aborted", user_id, descartado_at))
        ids.append(pid)
    for i in range(0, len(filas), 5000):
        with conn.cursor() as cur:
            _insertar(cur, filas[i:i + 5000])
        conn.commit()
    with conn.cursor() as cur:
        cur.execute("ANALYZE TABLE jacobs_pipelines")
        cur.fetchall()


def limpiar(env: dict, sembrado: dict, total_antes: int) -> None:
    conn = _conectar(env, autocommit=False)
    try:
        ids = sembrado.get("ids", [])
        for i in range(0, len(ids), 2000):
            lote = ids[i:i + 2000]
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM jacobs_pipelines WHERE pipeline_id IN ({', '.join(['%s'] * len(lote))})",
                            lote)
            conn.commit()
        usuarios = [sembrado[k] for k in ("pesado", "liviano", "superadmin") if k in sembrado]
        if usuarios:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM jax_users WHERE user_id IN ({', '.join(['%s'] * len(usuarios))})",
                            usuarios)
            conn.commit()
        # Red de seguridad por si la siembra se cortó entre el INSERT y el
        # registro de ids (ya verificado arriba que la base es la de test).
        with conn.cursor() as cur:
            cur.execute("DELETE FROM jacobs_pipelines WHERE name='carga descartados intercalados'")
            cur.execute("DELETE FROM jax_users WHERE email LIKE 'carga-descartados-int-%%'")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM jacobs_pipelines WHERE name='carga descartados intercalados'")
            (quedan,) = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM jacobs_pipelines")
            (total,) = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM jax_users WHERE email LIKE 'carga-descartados-int-%%'")
            (usuarios_quedan,) = cur.fetchone()
        print(f"[limpieza] filas_sembradas_restantes={quedan} usuarios_restantes={usuarios_quedan} "
              f"jacobs_pipelines={total} (antes={total_antes})")
        if quedan or usuarios_quedan or total != total_antes:
            raise RuntimeError("la limpieza NO dejó la base como estaba -- revisar a mano")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Medición SQL
# ---------------------------------------------------------------------------
def _handler_read(cur) -> dict:
    cur.execute("SHOW SESSION STATUS LIKE 'Handler_read%'")
    return {k: int(v) for k, v in cur.fetchall()}


def medir_sql(conn, sql: str, params: tuple) -> dict:
    with conn.cursor() as cur:
        cur.execute("EXPLAIN " + sql, params)
        cols = [d[0] for d in cur.description]
        explain = dict(zip(cols, cur.fetchone()))
        cur.execute("ANALYZE " + sql, params)
        cols = [d[0] for d in cur.description]
        analyze = dict(zip(cols, cur.fetchone()))
        antes = _handler_read(cur)
        cur.execute(sql, params)
        filas = cur.fetchall()
        despues = _handler_read(cur)
        b0 = _handler_read(cur)
        b1 = _handler_read(cur)
        delta = {k: despues[k] - antes[k] - (b1[k] - b0[k]) for k in despues}
        tiempos = []
        for _ in range(REPETICIONES):
            t0 = time.perf_counter()
            cur.execute(sql, params)
            cur.fetchall()
            tiempos.append((time.perf_counter() - t0) * 1000)
    return {
        "key": explain.get("key"), "extra": explain.get("Extra"),
        "r_rows": float(analyze["r_rows"]) if analyze.get("r_rows") is not None else None,
        "handler_read": sum(delta.values()),
        "filas": len(filas), "ids": [f[0] for f in filas],
        "p50_ms": round(statistics.median(tiempos), 3),
        "p95_ms": round(base.percentil(tiempos, 95), 3),
    }


def modo_sql(env: dict, sembrado: dict, etiqueta: str) -> dict:
    conn = _conectar(env)
    usuarios = {
        "pesado": (str(sembrado["pesado"]), "1", N_PESADO),
        "tipico": (sembrado["tipico"], TENANT_OTROS, N_OTROS // N_USUARIOS),
        "liviano": (str(sembrado["liviano"]), "1", N_LIVIANO),
    }
    resultados = []
    for nombre, (user_id, tenant_id, total) in usuarios.items():
        ultima = max(0, total - LIMITE)
        offsets = sorted({o for o in (0, 100, 1000, 2500) if o < ultima} | {ultima})
        for o in offsets:
            fila = {"usuario": nombre, "descartados_del_usuario": total, "offset": o, "variantes": {}}
            ids_ref = None
            for nombre_hint, hint in HINTS.items():
                m = medir_sql(conn, sql_master(hint), (user_id, tenant_id, LIMITE + 1, o))
                ids = m.pop("ids")
                ids_ref = ids if ids_ref is None else ids_ref
                m["misma_pagina"] = ids == ids_ref
                fila["variantes"][f"offset_{nombre_hint}"] = m
            if o > 0:
                # Cursor = última fila de la página anterior, con el orden
                # total nuevo (el de la aplicación).
                with conn.cursor() as cur:
                    cur.execute(f"SELECT {COLS}FROM jacobs_pipelines FORCE INDEX (idx_pipelines_descartados) "
                                f"{WHERE_USUARIO}{ORDEN}LIMIT %s OFFSET %s", (user_id, tenant_id, LIMITE, o - LIMITE))
                    previa = cur.fetchall()
                    cur.execute(f"SELECT {COLS}FROM jacobs_pipelines FORCE INDEX (idx_pipelines_descartados) "
                                f"{WHERE_USUARIO}{ORDEN}LIMIT %s OFFSET %s", (user_id, tenant_id, LIMITE + 1, o))
                    esperada = [f[0] for f in cur.fetchall()]
                d, p = previa[-1][5], previa[-1][0]
                for nombre_hint in ("force_descartados", "force_ocultos"):
                    m = medir_sql(conn, sql_cursor(HINTS[nombre_hint]), (user_id, tenant_id, d, d, p, LIMITE + 1))
                    m["misma_pagina"] = m.pop("ids") == esperada
                    fila["variantes"][f"cursor_{nombre_hint}"] = m
            resultados.append(fila)
            resumen = " | ".join(f"{k}: key={v['key']} r_rows={v['r_rows']} hr={v['handler_read']} "
                                 f"p95={v['p95_ms']}ms igual={v['misma_pagina']}"
                                 for k, v in fila["variantes"].items())
            print(f"[sql {nombre} offset={o}] {resumen}")
    conn.close()
    return {"modo": "sql", "series": resultados}


# ---------------------------------------------------------------------------
# Medición HTTP
# ---------------------------------------------------------------------------
async def modo_http(env: dict, sembrado: dict, tmp: Path) -> dict:
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
        print(f"[http] VERIFICADO /proc/{proc_backend.pid}/environ: JAX_DB_NAME={db_name} "
              f"cwd={os.readlink(f'/proc/{proc_backend.pid}/cwd')}")
        jwt_carga = pares.get(b"JAX_JWT_SECRET", b"").decode()
        base._abortar_si_el_secreto_de_carga_coincide_con_produccion(
            jwt_carga, _cargar_env_produccion().get("JAX_JWT_SECRET"))

        tok_p = base._token_para(sembrado["pesado"], "1", jwt_carga)
        tok_sa = base._token_para(sembrado["superadmin"], "1", jwt_carga, role="superadmin")
        total_admin = N_OTROS + N_PESADO + N_LIVIANO
        conn = _conectar(env)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM jacobs_pipelines WHERE status='discarded'")
            (total_admin,) = cur.fetchone()
        conn.close()
        series = [
            ("usuario_pesado", f"{base.BACKEND_URL}/api/pipelines?estado=discarded&limite={LIMITE}",
             {"Authorization": f"Bearer {tok_p}"}, N_PESADO),
            ("admin", f"{base.BACKEND_URL}/api/admin/pipelines/descartados?limite={LIMITE}",
             {"Authorization": f"Bearer {tok_sa}"}, total_admin),
        ]
        resultados = []
        for nombre, url_base, headers, total in series:
            ultima = max(0, total - LIMITE)
            for o in sorted({x for x in (0, 2500, 25000) if x < ultima} | {ultima}):
                url_off = f"{url_base}&offset={o}"
                r_off = httpx.get(url_off, headers=headers, timeout=30.0)
                r_off.raise_for_status()
                pag_off = r_off.json()
                fila = {"serie": nombre, "offset": o, "total": total,
                        "offset_http": await _tandas(url_off, headers)}
                if o > 0:
                    r_prev = httpx.get(f"{url_base}&offset={o - LIMITE}", headers=headers, timeout=30.0)
                    r_prev.raise_for_status()
                    cur_sig = r_prev.json()["cursor_siguiente"]
                    url_cur = f"{url_base}&cursor={cur_sig}"
                    r_cur = httpx.get(url_cur, headers=headers, timeout=30.0)
                    r_cur.raise_for_status()
                    pag_cur = r_cur.json()
                    fila["misma_pagina"] = ([p["pipeline_id"] for p in pag_cur["pipelines"]]
                                            == [p["pipeline_id"] for p in pag_off["pipelines"]]
                                            and pag_cur["has_more"] == pag_off["has_more"])
                    fila["cursor_http"] = await _tandas(url_cur, headers)
                resultados.append(fila)
                txt = (f"[http {nombre} offset={o}] offset c1_p95={fila['offset_http']['1']['p95_ms']} "
                       f"c25_p95={fila['offset_http']['25']['p95_ms']} c25_rps={fila['offset_http']['25']['rps']}")
                if "cursor_http" in fila:
                    txt += (f" | cursor c1_p95={fila['cursor_http']['1']['p95_ms']} "
                            f"c25_p95={fila['cursor_http']['25']['p95_ms']} "
                            f"c25_rps={fila['cursor_http']['25']['rps']} igual={fila['misma_pagina']}")
                print(txt)
        return {"modo": "http", "series": resultados}
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


async def _tandas(url: str, headers: dict) -> dict:
    salida = {}
    for c, n in CONCURRENCIAS.items():
        t = await base.correr_tanda(url, headers, c, n)
        if t["errores"] or set(t["codigos"]) != {200}:
            raise RuntimeError(f"tanda con errores en {url}: {t}")
        salida[str(c)] = t
    return salida


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("sql", "http"):
        raise SystemExit("uso: descartados_intercalados_medir.py {sql|http} [etiqueta]")
    modo = sys.argv[1]
    etiqueta = sys.argv[2] if len(sys.argv) > 2 else time.strftime("%H%M%S")
    tmp = Path(tempfile.mkdtemp(prefix="carga-intercalados-"))

    env_prod = _cargar_env_produccion()
    print("[intercalados] asegurando esquema de jax en jax_memory_test (idempotente)...")
    _asegurar_esquema(env_prod)
    env = base.construir_env(tmp)
    base._verificar_no_apunta_a_produccion(env)

    conn = _conectar(env, autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM jacobs_pipelines")
        (total_antes,) = cur.fetchone()
    sembrado: dict = {}
    try:
        print(f"[intercalados] sembrando {N_OTROS} de {N_USUARIOS} usuarios + pesado={N_PESADO} "
              f"+ liviano={N_LIVIANO}, intercalados (semilla {SEMILLA})...")
        sembrar(conn, sembrado)
        conn.close()
        if modo == "sql":
            res = modo_sql(env, sembrado, etiqueta)
        else:
            res = asyncio.run(modo_http(env, sembrado, tmp))
        res.update({"corrida": etiqueta, "n_otros": N_OTROS, "n_usuarios": N_USUARIOS,
                    "n_pesado": N_PESADO, "n_liviano": N_LIVIANO, "jacobs_pipelines_antes": total_antes})
        salida = LOADTEST_DIR / f"_descartados_resultados_intercalados_{modo}_{etiqueta}.json"
        salida.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        print(f"[intercalados] {salida} escrito")
    finally:
        if conn.open:
            conn.close()
        limpiar(env, sembrado, total_antes)


if __name__ == "__main__":
    main()
