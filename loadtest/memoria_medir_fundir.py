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
usaba el `JAX_JWT_SECRET` de `/etc/jax/.env` -- la llave de PRODUCCIÓN --
PARA FIRMAR el token con el que medía. Ahora vive en el repo, junto a
`memoria_medir.py`, y FIRMA con el secreto del backend de carga YA
LEVANTADO, leído vía `/proc/<pid>/environ` (mismo patrón que
`memoria_medir.py`) -- nunca con el de producción.

m6/m4 (cierre jax-platform#146, texto corregido en la ronda 7): esto NO
quiere decir que `/etc/jax/.env` deje de leerse -- SÍ se lee entero
(`_run` de abajo, `sudo -n cat /etc/jax/.env`), `JAX_JWT_SECRET` de
producción incluido, y ese valor de producción SÍ se usa: sólo para
COMPARARLO (`!=`) contra el secreto de carga, como barrera de seguridad
antes de medir (si algún día coincidieran, el script aborta en vez de
medir sobre un entorno que también podría emitir tokens válidos contra
producción). Lo que nunca pasa es que ese secreto de producción se use
para FIRMAR, ni que se imprima. El JSON crudo de resultados queda en
`loadtest/_memoria_resultados_fundir.json`, mismo lugar y misma
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

from memoria_levantar_entorno import (  # noqa: E402
    RUN_DIR, abortar_si_el_secreto_de_carga_coincide_con_produccion, leer_environ_de_proceso,
)


def percentil(valores, p):
    if not valores:
        return None
    ordenados = sorted(valores)
    k = max(1, min(len(ordenados), -(-int(round(p * len(ordenados) / 100.0)))))
    return ordenados[k - 1]


def armar_resultado(base_de_prueba: str, clusters_disponibles: list, clusters_intentados: list,
                     latencias: list[float], errores: int, codigos: dict) -> dict:
    """Arma el JSON de resultado. Función pura (sin red, sin DB) para poder
    testearla aislada -- ver `test_memoria_medir_fundir.py`.

    m5 (cierre jax-platform#146, ronda 6): `n_clusters_disponibles` se
    calculaba ANTES de esta corrección con `len(clusters)` DESPUÉS de
    truncar a `n_max` (`clusters = clusters[:n_max]`) -- con 757 clusters
    reales y `n_max=200` (el default), el JSON decía "200 disponibles", no
    757 (reproducido: `_resultados_r5_peor_caso_fundir.json`,
    `n_clusters_disponibles: 200`). `clusters_disponibles` es la lista
    COMPLETA que devolvió `/grupos`, ANTES de truncar; `clusters_intentados`
    es la que de verdad se usó para medir (`clusters_disponibles[:n_max]`)."""
    return {
        "base": base_de_prueba,
        "n_clusters_disponibles": len(clusters_disponibles),
        "n_intentados": len(clusters_intentados),
        "ok": len(latencias), "errores": errores, "codigos": codigos,
        "p50_ms": round(percentil(latencias, 50), 2) if latencias else None,
        "p95_ms": round(percentil(latencias, 95), 2) if latencias else None,
        "max_ms": round(max(latencias), 2) if latencias else None,
        "min_ms": round(min(latencias), 2) if latencias else None,
        "n_muestras": len(latencias),
    }


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

    # SEGURIDAD (ronda 5; comentario corregido en el cierre, ronda 6, m6):
    # el secreto de FIRMA sale del proceso de carga YA LEVANTADO, nunca de
    # producción -- pero `env` (arriba) parsea el `/etc/jax/.env` entero, y
    # eso incluye el `JAX_JWT_SECRET` de producción, usado a propósito acá
    # sólo para COMPARARLO (`!=`) contra el de carga, nunca para firmar ni
    # imprimirlo (mismo patrón y misma nota que `memoria_medir.py`).
    #
    # MINOR (revision adversarial, ronda 8 -- consistencia): el chequeo
    # ahora vive en `memoria_levantar_entorno.py`, compartido con
    # `memoria_medir.py`, y tambien rechaza un secreto de CARGA vacio
    # (antes sólo comparaba contra el de produccion -- un vacio no
    # coincide con uno no vacio, asi que pasaba).
    info = json.loads((RUN_DIR / "info.json").read_text())
    environ_de_carga = leer_environ_de_proceso(info["pid"])
    jwt_secret_de_carga = environ_de_carga["JAX_JWT_SECRET"]
    abortar_si_el_secreto_de_carga_coincide_con_produccion(
        jwt_secret_de_carga, env.get("JAX_JWT_SECRET"))

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
    clusters_disponibles = []
    for g in grupos:
        for c in g.get("casi_duplicados", []):
            clusters_disponibles.append(c)
    print(f"[fundir] clusters disponibles: {len(clusters_disponibles)}", file=sys.stderr)
    if len(clusters_disponibles) < 10:
        raise RuntimeError(
            f"sólo {len(clusters_disponibles)} clusters disponibles -- se necesitan >=10 "
            "para una muestra con percentiles reales (LAS CUATRO DEL "
            "RENDIMIENTO #4)")
    # m5 (cierre jax-platform#146, ronda 6): `clusters_disponibles` es el
    # total ANTES de truncar -- se guarda entero para `armar_resultado()`.
    # `clusters` (abajo) es sólo lo que de verdad se intenta fundir.
    clusters = clusters_disponibles[:n_max]

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

    resultado = armar_resultado(base_de_prueba, clusters_disponibles, clusters, latencias, errores, codigos)
    salida = LOADTEST_DIR / "_memoria_resultados_fundir.json"
    salida.write_text(json.dumps(resultado, indent=2, ensure_ascii=False))
    print(json.dumps(resultado, indent=2))
    print(f"[medir] {salida} escrito", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit("uso: memoria_medir_fundir.py <base_de_prueba> <url_backend> [n_max]")
    asyncio.run(main_async(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 200))
