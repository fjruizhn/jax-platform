"""Carga de GET /admin/models/sync/estado (2026-09-27, pedido de Fernando:
avance real + última actualización siempre visible) -- regla 4 del
rendimiento (LAS CUATRO DEL RENDIMIENTO, CLAUDE.md): "ningún endpoint llega a
producción sin una prueba de carga", peor caso, con concurrencia real.

ALCANCE DE ESTA MEDICIÓN -- declarado, no escondido (Principio VIII). A
diferencia de otros scripts de `loadtest/` (p. ej. `memoria_medir.py`), este
NO levanta un backend uvicorn real ni mide HTTP de punta a punta: mide,
directo contra `aiomysql`, las DOS consultas SQL reales que
`api/admin/models.py::sync_estado()` ejecuta (la fila 'corriendo', si hay
alguna, y la última terminada -- el LEFT JOIN contra `jax_users` incluido).
Es la parte que este PR introduce y la única con riesgo de escala nuevo (una
tabla que crece con cada sync); el resto del costo del endpoint (auth JWT,
serialización FastAPI) ya es un costo COMPARTIDO por todos los endpoints
`/api/admin/*` de este repo, no algo nuevo de esta feature, y no se
remidió acá por tiempo. Si hiciera falta el número end-to-end con HTTP real,
`loadtest/memoria_levantar_entorno.py` es el patrón a seguir.

PEOR CASO medido: `RETENCION_FILAS` (200, el tope real de
`catalogo_sync_registro.RETENCION_FILAS`) filas terminadas en
`catalogo_sync_ejecucion` MÁS una fila 'corriendo' -- el estado más lleno que
el propio código permite en producción (la retención borra el resto).

USO (nunca contra producción -- créa y borra su PROPIA base de prueba,
sufijo reconocible):
    JAX_TEST_DB_SUFIJO=cargasyncestado python3 loadtest/catalogo_sync_estado_medir.py
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import aiomysql

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

CONCURRENCIAS = {1: 200, 25: 500}
FILAS_TERMINADAS = 200  # RETENCION_FILAS real -- ver catalogo_sync_registro.py

SQL_CORRIENDO = (
    "SELECT e.id, e.origen, e.iniciado_por, e.estado, e.paso_actual, e.pasos_total, "
    "e.detalle_paso, e.iniciado_en, e.terminado_en, e.resultado, u.email "
    "FROM catalogo_sync_ejecucion e LEFT JOIN jax_users u ON u.user_id = e.iniciado_por "
    "WHERE e.estado='corriendo' LIMIT 1"
)
SQL_ULTIMA = (
    "SELECT e.id, e.origen, e.iniciado_por, e.estado, e.paso_actual, e.pasos_total, "
    "e.detalle_paso, e.iniciado_en, e.terminado_en, e.resultado, u.email "
    "FROM catalogo_sync_ejecucion e LEFT JOIN jax_users u ON u.user_id = e.iniciado_por "
    "ORDER BY e.terminado_en DESC LIMIT 1"
)


def percentil(valores, p):
    if not valores:
        return None
    ordenados = sorted(valores)
    k = max(1, min(len(ordenados), -(-int(round(p * len(ordenados) / 100.0)))))
    return ordenados[k - 1]


async def _sembrar(pool, marca: str) -> list[int]:
    ids = []
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for i in range(FILAS_TERMINADAS):
                await cur.execute(
                    "INSERT INTO catalogo_sync_ejecucion "
                    "(origen, estado, pasos_total, paso_actual, iniciado_en, terminado_en, resultado) "
                    "VALUES ('manual', 'ok', 9, 9, UTC_TIMESTAMP() - INTERVAL %s SECOND, "
                    "UTC_TIMESTAMP() - INTERVAL %s SECOND, %s)",
                    (FILAS_TERMINADAS - i + 10, FILAS_TERMINADAS - i, json.dumps({"marca": marca, "ok": True})),
                )
                ids.append(cur.lastrowid)
            # iniciado_en=UTC_TIMESTAMP() (fresco, MAJOR-A tercera ronda): sin
            # esto la fila 'corriendo' se marcaría huérfana en la PRIMERA
            # lectura de la medición -- el criterio nuevo es candado de
            # trabajo libre Y `iniciado_en` más viejo que el margen de
            # gracia; con `iniciado_en` recién puesto, ninguna corrida de
            # `marcar_huerfanas_interrumpidas()` (el candado está libre de
            # verdad -- este script no llama a `model_catalog.sync_all()`)
            # la toca durante toda la medición.
            await cur.execute(
                "INSERT INTO catalogo_sync_ejecucion "
                "(origen, estado, pasos_total, paso_actual, iniciado_en) "
                "VALUES ('programado', 'corriendo', 9, 4, UTC_TIMESTAMP())"
            )
            ids.append(cur.lastrowid)
        await conn.commit()
    return ids


async def _limpiar(pool, ids: list[int]) -> None:
    if not ids:
        return
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            marcas = ", ".join(["%s"] * len(ids))
            await cur.execute(f"DELETE FROM catalogo_sync_ejecucion WHERE id IN ({marcas})", tuple(ids))
        await conn.commit()


async def _una_lectura(pool) -> float:
    t0 = time.perf_counter()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_CORRIENDO)
            await cur.fetchone()
            await cur.execute(SQL_ULTIMA)
            await cur.fetchone()
    return (time.perf_counter() - t0) * 1000


async def _tanda(pool, c: int, n: int) -> dict:
    sem = asyncio.Semaphore(c)
    latencias = []
    errores = 0

    async def tarea():
        nonlocal errores
        async with sem:
            try:
                latencias.append(await _una_lectura(pool))
            except Exception:  # fail-soft: una lectura de carga que revienta cuenta como error de ESA lectura, no aborta la tanda
                errores += 1

    t0 = time.perf_counter()
    await asyncio.gather(*(tarea() for _ in range(n)))
    segundos = time.perf_counter() - t0
    total = len(latencias) + errores
    return {
        "c": c, "n": n, "ok": len(latencias), "errores": errores, "segundos": round(segundos, 3),
        "rps": round(total / segundos, 2) if segundos > 0 else None,
        "p50_ms": round(percentil(latencias, 50), 3) if latencias else None,
        "p95_ms": round(percentil(latencias, 95), 3) if latencias else None,
        "p99_ms": round(percentil(latencias, 99), 3) if latencias else None,
        "max_ms": round(max(latencias), 3) if latencias else None,
    }


def _cargar_env_produccion_minimo() -> None:
    """Sólo lo que `asegurar_base_de_test()`/`run_migrations()` necesitan
    para conectarse y migrar -- MISMA fuente que `tests/conftest.py`
    (`sudo -n cat /etc/jax/.env`), nunca un valor inventado. `setdefault`:
    una variable ya puesta en el entorno de quien invoca este script gana."""
    import subprocess

    r = subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"no se pudo leer /etc/jax/.env: {r.stderr.strip()[:200]}")
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


async def main_async(base: str) -> None:
    pool = await aiomysql.create_pool(
        host=os.environ["JAX_DB_HOST"], port=int(os.environ.get("JAX_DB_PORT", 3306)),
        user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"],
        db=base, minsize=1, maxsize=520, autocommit=False,
    )
    ids = []
    try:
        ids = await _sembrar(pool, marca="loadtest-catalogo-sync-estado")
        resultados = []
        for c, n in CONCURRENCIAS.items():
            resultados.append(await _tanda(pool, c, n))
        print(json.dumps({"base": base, "filas_terminadas_sembradas": FILAS_TERMINADAS, "resultados": resultados}, indent=2))
    finally:
        await _limpiar(pool, ids)
        pool.close()
        await pool.wait_closed()


def main() -> None:
    _cargar_env_produccion_minimo()
    if not os.environ.get("JAX_REPO_PATH"):
        raise RuntimeError(
            "JAX_REPO_PATH no está fijado -- apuntalo a un checkout PROPIO de jax "
            "(nunca /home/fruiz/jax ni /srv/jax-prod/jax), run_migrations() lo necesita.")

    # fijar_base_de_test()/asegurar_base_de_test() son SÍNCRONAS y corren su
    # PROPIO asyncio.run() por dentro -- tienen que llamarse ANTES de entrar
    # al loop de main_async(), nunca desde adentro (asyncio.run() anidado
    # revienta con "cannot be called from a running event loop").
    from base_de_test import asegurar_base_de_test, fijar_base_de_test

    fijar_base_de_test()
    base = os.environ.get("JAX_DB_NAME", "")
    if not base.startswith("jax_memory_test"):
        raise RuntimeError(f"base resuelta ({base!r}) no es una base de test -- me niego a medir contra ella")
    asegurar_base_de_test()

    asyncio.run(main_async(base))


if __name__ == "__main__":
    main()
