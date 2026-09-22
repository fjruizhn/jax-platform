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
    import subprocess

    import pymysql
    from jose import jwt as _jwt

    from memoria_levantar_entorno import (
        RUN_DIR, abortar_si_el_secreto_de_carga_coincide_con_produccion, leer_environ_de_proceso,
    )

    r = subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"], capture_output=True, text=True, check=True)
    env = {}
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            env[k.strip()] = v.strip()

    # SEGURIDAD (revision adversarial de jax-platform PR 146, ronda 5;
    # comentario corregido en el cierre, ronda 6, m6): el backend de carga
    # (memoria_levantar_entorno.py) firma con SU PROPIA `JAX_JWT_SECRET`,
    # generada al azar -- NUNCA la de produccion para FIRMAR ni VERIFICAR
    # tokens. Pero `env` (arriba) SI parsea el `/etc/jax/.env` entero, y eso
    # incluye el `JAX_JWT_SECRET` de produccion -- se usa, a proposito, para
    # el chequeo de abajo: COMPARARLO (con `!=`) contra el del backend de
    # carga, nunca para firmar ni para imprimirlo. El resto de `env` sigue
    # haciendo falta para las credenciales de conexion a MariaDB, que son
    # las mismas para cualquier base de esa instancia. El secreto de firma
    # se lee del proceso YA LEVANTADO -- `info.json` (el mismo que escribe
    # el lanzador) trae el `pid`; `/proc/<pid>/environ` es la unica fuente
    # que no requiere volver a escribir el secreto en ningun archivo.
    #
    # MINOR (revision adversarial, ronda 8 -- consistencia): el chequeo
    # ahora vive en `memoria_levantar_entorno.py`, compartido con
    # `memoria_medir_fundir.py`, y tambien rechaza un secreto de CARGA
    # vacio (antes sólo comparaba contra el de produccion -- un vacio no
    # coincide con uno no vacio, asi que pasaba).
    info = json.loads((RUN_DIR / "info.json").read_text())
    environ_de_carga = leer_environ_de_proceso(info["pid"])
    jwt_secret_de_carga = environ_de_carga["JAX_JWT_SECRET"]
    abortar_si_el_secreto_de_carga_coincide_con_produccion(
        jwt_secret_de_carga, env.get("JAX_JWT_SECRET"))

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
        fila_usuario = cur.fetchone()
    if fila_usuario is None:
        raise RuntimeError(
            f"{base_de_prueba} no tiene user_id=1 -- ¿corriste "
            "memoria_levantar_entorno.py, que crea el superadmin?")
    (token_version, rol) = fila_usuario
    conn.close()
    if n_facts < 9000:
        raise RuntimeError(f"{base_de_prueba} tiene solo {n_facts} facts -- ¿corriste memoria_seed.py?")
    print(f"[medir] {base_de_prueba}: {n_facts} facts", file=sys.stderr)

    token = _jwt.encode(
        {"user_id": "1", "tenant_id": "1", "role": rol, "tv": token_version,
         "exp": int(time.time()) + 7200, "type": "access"},
        jwt_secret_de_carga, algorithm="HS256",
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
    #
    # MINOR A-texto (revision adversarial de jax-platform PR 146, ronda 5):
    # `n` sube a >=10 en los tres niveles -- antes c=1/c=3 median con 3/6
    # muestras, donde "p95" es literalmente el maximo de la muestra (no un
    # percentil real).
    #
    # m2 (cierre, ronda 6): `percentil()` usa k=round(p*n/100) (redondeo al
    # mas cercano, con desempate al par -- NO ceil). Para p=95 eso da:
    #   c=1, n=10 -> round(9.5)=10=n  -> p95 SIGUE siendo el maximo de la
    #                muestra (el 9.5 empata y Python redondea al par, 10).
    #   c=3, n=12 -> round(11.4)=11<n -> p95 es el segundo peor, no el max.
    #   c=5, n=15 -> round(14.25)=14<n -> idem, segundo peor.
    # O sea: subir a n>=10 alcanza para c=3/c=5, pero NO para c=1 -- ahi
    # "p95" sigue siendo literalmente el maximo, documentado asi en el
    # reporte de carga.
    for c, n in [(1, 10), (3, 12), (5, 15)]:
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
