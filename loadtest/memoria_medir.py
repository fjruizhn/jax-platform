"""Carga de la pantalla de Memoria (Task 7, 2026-09-20) -- MEDICION contra un
backend YA LEVANTADO (loadtest/memoria_levantar_entorno.py) sobre la base ya
sembrada (loadtest/memoria_seed.py). A diferencia de
loadtest/historial_orquestar.py, este script NO levanta ni mata el backend:
el encargo pide dejarlo EN PIE para que Fernando mire la pantalla despues.

Mide, con concurrencia real (httpx.AsyncClient, HTTP real, nunca
ASGITransport):
    GET /api/admin/memoria/grupos                          (agrupamiento por tema)
    GET /api/admin/memoria/hechos?verificado=false&limite=500  (cola de revision, usa idx_facts_revision)
    GET /api/admin/memoria/hechos?limite=500                   (SIN filtrar -- filesort, sin medir hasta hoy)

Antes de medir, VERIFICA que cada camino trae los datos reales (no un
literal ni una lista vacia -- antecedente del 2026-09-16, un p95 de 2,99 ms
resulto ser FastAPI devolviendo un literal).

USO:
    python3 loadtest/memoria_medir.py <base_de_prueba> <url_backend>
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

LOADTEST_DIR = Path(__file__).parent


async def _una(cliente: httpx.AsyncClient, url: str, params: dict, headers: dict):
    t0 = time.perf_counter()
    try:
        r = await cliente.get(url, params=params, headers=headers)
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code >= 400:
            return None, True, r.status_code, 0
        return ms, False, r.status_code, len(r.content)
    except Exception:  # fail-soft: una peticion de carga que revienta (timeout, conexion cerrada) cuenta como fallo de esa peticion, no aborta el resto de la medicion -- el llamador ya interpreta (None, True, ...) como error
        return None, True, None, 0


def percentil(valores, p):
    if not valores:
        return None
    ordenados = sorted(valores)
    k = max(1, min(len(ordenados), -(-int(round(p * len(ordenados) / 100.0)))))
    return ordenados[k - 1]


async def correr_tanda(cliente_factory, url: str, params: dict, headers: dict, c: int, n: int) -> dict:
    limits = httpx.Limits(max_connections=c + 5, max_keepalive_connections=c + 5)
    async with httpx.AsyncClient(limits=limits, timeout=120.0) as cliente:
        sem = asyncio.Semaphore(c)
        latencias, tamanos, codigos = [], [], {}
        errores = 0

        async def tarea():
            nonlocal errores
            async with sem:
                ms, fallo, code, size = await _una(cliente, url, params, headers)
                if fallo:
                    errores += 1
                else:
                    latencias.append(ms)
                    tamanos.append(size)
                codigos[code] = codigos.get(code, 0) + 1

        t0 = time.perf_counter()
        await asyncio.gather(*(tarea() for _ in range(n)))
        segundos = time.perf_counter() - t0

    total = len(latencias) + errores
    return {
        "c": c, "n": n, "ok": len(latencias), "errores": errores,
        "segundos": round(segundos, 3),
        "rps": round(total / segundos, 2) if segundos > 0 else None,
        "p50_ms": round(percentil(latencias, 50), 2) if latencias else None,
        "p95_ms": round(percentil(latencias, 95), 2) if latencias else None,
        "p99_ms": round(percentil(latencias, 99), 2) if latencias else None,
        "max_ms": round(max(latencias), 2) if latencias else None,
        "codigos": codigos,
        "bytes_min": min(tamanos) if tamanos else None,
        "bytes_max": max(tamanos) if tamanos else None,
    }


async def main_async(base_de_prueba: str, backend_url: str) -> None:
    import os

    import pymysql
    from jose import jwt as _jwt

    import subprocess
    r = subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"], capture_output=True, text=True, check=True)
    env = {}
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            env[k.strip()] = v.strip()

    # Barrera dura: no mide si el backend real (segun /proc/<pid>/environ, no
    # esta llamada) no apunta a la base esperada. Se vuelve a verificar por
    # una via independiente: una consulta de solo lectura a la base misma.
    conn = pymysql.connect(host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]),
                            user=env["JAX_DB_USER"], password=env["JAX_DB_PASSWORD"],
                            database=base_de_prueba, charset="utf8mb4")
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM facts")
        (n_facts,) = cur.fetchone()
        # `token_version` y `role` se LEEN de la base, no se inventan: estaban
        # fijos en `tv=0` y sin rol, y el token salia 401 en cuanto el usuario
        # habia iniciado sesion alguna vez (cada login sube la version). Un
        # medidor que no puede autenticarse mide un 401 rapido -- justo lo que
        # la verificacion previa de mas abajo existe para impedir.
        cur.execute("SELECT token_version, role FROM jax_users WHERE user_id = 1")
        (token_version, rol) = cur.fetchone()
    conn.close()
    if n_facts < 9000:
        raise RuntimeError(f"{base_de_prueba} tiene solo {n_facts} facts -- ¿corriste memoria_seed.py?")
    print(f"[medir] {base_de_prueba}: {n_facts} facts", file=sys.stderr)

    token = _jwt.encode(
        {"user_id": "1", "tenant_id": "1", "role": rol, "tv": token_version,
         "exp": int(time.time()) + 7200, "type": "access"},
        env["JAX_JWT_SECRET"], algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}

    # --- Verificacion previa: el camino real, no un literal (leccion de U5, 2026-09-16) ---
    verificacion = {}
    async with httpx.AsyncClient(timeout=60.0) as cliente:
        r = await cliente.get(f"{backend_url}/api/admin/memoria/hechos",
                              params={"verificado": "false", "limite": 500}, headers=headers)
        cuerpo = r.json()
        verificacion["hechos_no_verificados"] = {
            "status": r.status_code, "bytes": len(r.content),
            "n_hechos": len(cuerpo.get("hechos", [])), "total": cuerpo.get("total"),
            "todos_no_verificados": all(not h["verificado"] for h in cuerpo.get("hechos", [])),
        }

        r = await cliente.get(f"{backend_url}/api/admin/memoria/hechos",
                              params={"limite": 500}, headers=headers)
        cuerpo = r.json()
        verificacion["hechos_sin_filtro"] = {
            "status": r.status_code, "bytes": len(r.content),
            "n_hechos": len(cuerpo.get("hechos", [])), "total": cuerpo.get("total"),
        }

        t0 = time.perf_counter()
        r = await cliente.get(f"{backend_url}/api/admin/memoria/grupos", headers=headers)
        dt = time.perf_counter() - t0
        cuerpo = r.json()
        grupos = cuerpo.get("grupos", [])
        verificacion["grupos"] = {
            "status": r.status_code, "bytes": len(r.content), "segundos_1a_llamada": round(dt, 3),
            "n_grupos": len(grupos),
            "grupo_mas_grande": max((len(g["hechos"]) for g in grupos), default=0),
            "con_casi_duplicados": sum(1 for g in grupos if g.get("casi_duplicados")),
        }

    print("[medir] verificación previa:", file=sys.stderr)
    print(json.dumps(verificacion, indent=2, ensure_ascii=False), file=sys.stderr)

    for clave, chequeo in (
        ("hechos_no_verificados", verificacion["hechos_no_verificados"]["status"] == 200
         and verificacion["hechos_no_verificados"]["n_hechos"] > 0
         and verificacion["hechos_no_verificados"]["todos_no_verificados"]),
        ("hechos_sin_filtro", verificacion["hechos_sin_filtro"]["status"] == 200
         and verificacion["hechos_sin_filtro"]["n_hechos"] > 0),
        ("grupos", verificacion["grupos"]["status"] == 200
         and verificacion["grupos"]["n_grupos"] > 0
         and verificacion["grupos"]["grupo_mas_grande"] > 50),
    ):
        if not chequeo:
            raise RuntimeError(f"verificación previa falló para {clave} -- no se mide sobre un endpoint roto")
    print("[medir] verificación previa OK: las tres respuestas traen datos reales, no un literal", file=sys.stderr)

    resultados = {"verificacion": verificacion, "hechos_no_verificados": [], "hechos_sin_filtro": [], "grupos": []}

    for c, n in [(1, 200), (25, 1000), (50, 1000), (100, 1000)]:
        r = await correr_tanda(None, f"{backend_url}/api/admin/memoria/hechos",
                               {"verificado": "false", "limite": 500}, headers, c, n)
        print(f"[HECHOS verificado=false] c={c} n={n} -> {r}")
        resultados["hechos_no_verificados"].append(r)

    for c, n in [(1, 200), (25, 1000), (50, 1000), (100, 1000)]:
        r = await correr_tanda(None, f"{backend_url}/api/admin/memoria/hechos",
                               {"limite": 500}, headers, c, n)
        print(f"[HECHOS sin filtro] c={c} n={n} -> {r}")
        resultados["hechos_sin_filtro"].append(r)

    # /grupos: cada request evalua ~9.000 vecinos con un semaforo interno de 6
    # contra el mismo pool de 10 conexiones -- niveles chicos, a propósito
    # (ver docstring del módulo: ronda anterior, un solo request ya tarda ~7s).
    for c, n in [(1, 3), (3, 6), (5, 10)]:
        r = await correr_tanda(None, f"{backend_url}/api/admin/memoria/grupos", {}, headers, c, n)
        print(f"[GRUPOS] c={c} n={n} -> {r}")
        resultados["grupos"].append(r)

    salida = LOADTEST_DIR / "_memoria_resultados.json"
    salida.write_text(json.dumps(resultados, indent=2, ensure_ascii=False))
    print(f"[medir] {salida} escrito")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("uso: memoria_medir.py <base_de_prueba> <url_backend>")
    asyncio.run(main_async(sys.argv[1], sys.argv[2]))
