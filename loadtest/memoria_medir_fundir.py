"""Carga de `POST /api/admin/memoria/hechos/fundir` (Task 7 / D7, MAJOR A
de la revisión adversarial de jax-platform#146). Complementa a
`memoria_medir.py` (que mide las rutas de LECTURA) con la única ruta de
ESCRITURA de esta pantalla que el brief pedía medir aparte: cada llamada
muta filas (`superseded_by`/`is_verified`), así que no puede repetirse
sobre el mismo cluster -- la medición es SECUENCIAL, un cluster real por
llamada, nunca concurrente contra el mismo id (mediría contención de
`FOR UPDATE`, no el costo real del endpoint).

MINOR A-texto (revisión adversarial de jax-platform PR 146, ronda 5): antes
este script vivía SOLO en el scratchpad de la sesión (`medir_fundir.py`) y
leía `JAX_JWT_SECRET` de `/etc/jax/.env` -- la llave de PRODUCCIÓN. Ahora
vive en el repo, junto a `memoria_medir.py`, y lee el secreto del backend
de carga YA LEVANTADO vía `/proc/<pid>/environ` (mismo patrón que
`memoria_medir.py`) -- nunca de producción. El JSON crudo de resultados
queda en `loadtest/_memoria_resultados_fundir.json`, mismo lugar y misma
convención que `loadtest/_memoria_resultados.json` (de `/grupos`).

USO:
    python3 loadtest/memoria_medir_fundir.py <base_de_prueba> <url_backend> [n_max]
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

LOADTEST_DIR = Path(__file__).parent

from memoria_levantar_entorno import RUN_DIR, leer_environ_de_proceso  # noqa: E402


def percentil(valores, p):
    if not valores:
        return None
    ordenados = sorted(valores)
    k = max(1, min(len(ordenados), -(-int(round(p * len(ordenados) / 100.0)))))
    return ordenados[k - 1]


async def main_async(base_de_prueba: str, backend_url: str, n_max: int) -> None:
    import pymysql
    from jose import jwt as _jwt

    r = subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"], capture_output=True, text=True, check=True)
    env = {}
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            env[k.strip()] = v.strip()

    # SEGURIDAD (ronda 5): ver el docstring del módulo -- el secreto de
    # firma sale del proceso de carga YA LEVANTADO, nunca de producción.
    info = json.loads((RUN_DIR / "info.json").read_text())
    environ_de_carga = leer_environ_de_proceso(info["pid"])
    jwt_secret_de_carga = environ_de_carga["JAX_JWT_SECRET"]
    assert jwt_secret_de_carga != env.get("JAX_JWT_SECRET"), (
        "el backend de carga esta firmando con la llave de PRODUCCION -- "
        "ABORTANDO, no se mide sobre un entorno que puede emitir tokens "
        "validos tambien contra produccion")

    conn = pymysql.connect(host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]), user=env["JAX_DB_USER"],
                            password=env["JAX_DB_PASSWORD"], database=base_de_prueba, charset="utf8mb4")
    with conn.cursor() as cur:
        cur.execute("SELECT token_version, role FROM jax_users WHERE user_id=1")
        fila = cur.fetchone()
    conn.close()
    if fila is None:
        raise RuntimeError(
            f"{base_de_prueba} no tiene user_id=1 -- ¿corriste memoria_levantar_entorno.py?")
    tv, role = fila

    token = _jwt.encode(
        {"user_id": "1", "tenant_id": "1", "role": role, "tv": tv,
         "exp": int(time.time()) + 7200, "type": "access"},
        jwt_secret_de_carga, algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=60.0) as cliente:
        r = await cliente.get(f"{backend_url}/api/admin/memoria/grupos", headers=headers)
        grupos = r.json()["grupos"]
    clusters = []
    for g in grupos:
        for c in g.get("casi_duplicados", []):
            clusters.append(c)
    print(f"[fundir] clusters disponibles: {len(clusters)}", file=sys.stderr)
    if len(clusters) < 10:
        raise RuntimeError(
            f"sólo {len(clusters)} clusters disponibles -- se necesitan >=10 "
            "para una muestra con percentiles reales (LAS CUATRO DEL "
            "RENDIMIENTO #4)")
    clusters = clusters[:n_max]

    latencias, errores, codigos = [], 0, {}
    async with httpx.AsyncClient(timeout=60.0) as cliente:
        for c in clusters:
            absorbidos = [i for i in c["ids"] if i != c["superviviente_id"]]
            if not absorbidos:
                continue
            t0 = time.perf_counter()
            r = await cliente.post(f"{backend_url}/api/admin/memoria/hechos/fundir",
                                    json={"superviviente_id": c["superviviente_id"], "absorbidos": absorbidos},
                                    headers=headers)
            dt = (time.perf_counter() - t0) * 1000
            codigos[str(r.status_code)] = codigos.get(str(r.status_code), 0) + 1
            if r.status_code == 200:
                latencias.append(dt)
            else:
                errores += 1

    resultado = {
        "base": base_de_prueba, "n_clusters_disponibles": len(clusters),
        "n_intentados": len(clusters), "ok": len(latencias), "errores": errores, "codigos": codigos,
        "p50_ms": round(percentil(latencias, 50), 2) if latencias else None,
        "p95_ms": round(percentil(latencias, 95), 2) if latencias else None,
        "max_ms": round(max(latencias), 2) if latencias else None,
        "min_ms": round(min(latencias), 2) if latencias else None,
        "n_muestras": len(latencias),
    }
    salida = LOADTEST_DIR / "_memoria_resultados_fundir.json"
    salida.write_text(json.dumps(resultado, indent=2, ensure_ascii=False))
    print(json.dumps(resultado, indent=2))
    print(f"[medir] {salida} escrito", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit("uso: memoria_medir_fundir.py <base_de_prueba> <url_backend> [n_max]")
    asyncio.run(main_async(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 200))
