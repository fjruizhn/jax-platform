"""Medición (fix round 4, Ruling 18/19 punto 3, 2026-09-22): SQL_PIPELINES_DEL_USUARIO
con FORCE INDEX (idx_pipelines_visibles) -- la decisión FINAL -- contra el
plan natural (sin ningún hint) y contra el FORCE INDEX (idx_jacobs_pipelines_
duenio) del fix round 3 -- la decisión ANTERIOR, que quedó demostrada
insuficiente. Contra la base de TEST, nunca contra jax_memory.

Historia completa de esta consulta (cuatro rondas sobre el MISMO problema,
para no perder el porqué):

1. **Fix round 1 (Ruling 13(e)):** aceptó un plan con `Using filesort` a
   partir de una medición floja (363 filas, 1,6x, ruido).
2. **Fix round 2 (Ruling 16):** `IGNORE INDEX (idx_pipelines_descartados,
   idx_pipelines_ocultos)` -- determinista, pero acoplaba el deploy a una
   migración de `jax` (jax#257) mergeada-no-desplegada, y no cubría un caso
   extremo (elegía `idx_pipelines_status` con filesort igual).
3. **Fix round 3 (Ruling 17):** `FORCE INDEX (idx_jacobs_pipelines_duenio)`
   -- sin acoplamiento de deploy (ese índice YA estaba en producción) y
   cubría el caso extremo, pero **NO acotaba el costo por el LIMIT**: medido
   4,2-4,4 ms / ~5000 Handler_read con pocas filas vivas entre muchas
   descartadas -- un recorrido lineal del histórico del tenant, documentado
   como "costo conocido, no resuelto".
4. **Fix round 4 (Ruling 18/19, ESTE documento):** `jax` agrega una columna
   GENERADA `visible` (VIRTUAL, `status NOT IN ('discarded','hidden') AND
   owner_ack_at IS NOT NULL`) e `idx_pipelines_visibles (user_id, tenant_id,
   visible, created_at)` -- con `visible` DENTRO del índice, el rango que el
   motor recorre ya viene filtrado: el costo lo acota el LIMIT, NO el
   histórico. `FORCE INDEX (idx_pipelines_visibles)` reemplaza al de la
   ronda anterior. Acopla el deploy a jax#259 (la migración que agrega
   `visible`/`idx_pipelines_visibles`) -- ver el runbook de despliegue en
   docs/.

Por qué esto vive acá y no en loadtest/ (divergencia deliberada, anotada):
los demás loadtest/*.py son procesos standalone que abren su PROPIA conexión
a MariaDB fuera de pytest -- correcto para ELLOS porque miden HTTP de punta a
punta con un backend real levantado aparte. La regla de esta sesión es más
estricta ("DB tests ONLY through pytest ... Never query MariaDB outside
pytest"): esta medición es SQL puro (`EXPLAIN` + `Handler_read` + tiempo de
`cursor.execute`/`fetchall`), así que corre como test de pytest, con el
mismo aislamiento (`base_de_test`) que el resto de la suite.

Se salta por default (siembra hasta 5000+ filas, no es parte del piso de CI
normal): correr con JAX_MEDIR_INDICE_PIPELINES=1, y `JAX_REPO_PATH` apuntando
a un checkout de `jax` con jax#259 incluido (`visible`/`idx_pipelines_visibles`
entre sus índices).
"""
import os
import time
import uuid

import pytest

from api import pipelines as mod
from tests.identidades import sql

pytestmark = pytest.mark.skipif(
    os.environ.get("JAX_MEDIR_INDICE_PIPELINES") != "1",
    reason="medición manual, pesada (siembra 5000+ filas) -- JAX_MEDIR_INDICE_PIPELINES=1 para correrla",
)

#: El plan NATURAL -- sin ningún hint de índice.
SQL_NATURAL = (
    "SELECT pipeline_id, name, status, created_at, updated_at FROM jacobs_pipelines "
    "WHERE user_id=%s AND tenant_id=%s AND owner_ack_at IS NOT NULL "
    "AND status NOT IN ('discarded','hidden') "
    "ORDER BY created_at DESC LIMIT %s OFFSET %s"
)

#: La decisión del fix round 3 (Ruling 17) -- determinista pero NO acotada
#: por el LIMIT. Se sigue midiendo para que el documento compare las TRES
#: decisiones, no sólo la última contra la primera.
SQL_FORCE_DUENIO = (
    "SELECT pipeline_id, name, status, created_at, updated_at FROM jacobs_pipelines "
    "FORCE INDEX (idx_jacobs_pipelines_duenio) "
    "WHERE user_id=%s AND tenant_id=%s AND owner_ack_at IS NOT NULL "
    "AND status NOT IN ('discarded','hidden') "
    "ORDER BY created_at DESC LIMIT %s OFFSET %s"
)

N_MUESTRAS = 15

# El esquema de jax (columnas/índices nuevos, incluido `visible`) lo asegura
# tests/conftest.py::client() (fix round 2, Ruling 16 punto 3) -- ya no hace
# falta un fixture propio acá.


async def _insertar_bulk(filas):
    """`cur.executemany` directo -- 5000 INSERT de a uno vía `sql()` tardan
    minutos; set-based tardaría menos pero el motor SEQUENCE de MariaDB no
    sirve acá (cada fila necesita un pipeline_id UUID distinto, no una
    secuencia numérica). `executemany` es el término medio real: una sola
    ida y vuelta de red por lote."""
    from db.connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO jacobs_pipelines "
                "(pipeline_id, name, invoked_by, mode, status, created_at, updated_at, "
                " user_id, tenant_id, owner_ack_at) "
                "VALUES (%s, 'carga', 'plataforma', 'supervised', %s, %s, %s, %s, %s, %s)",
                filas,
            )
        await conn.commit()


async def _borrar_tenant(tenant_id):
    await sql("DELETE FROM jacobs_pipelines WHERE tenant_id=%s", (tenant_id,))


def _filas(tenant_id, n, status, owner_ack_at_presente=True, offset_id=0):
    ahora = time.time()
    out = []
    for i in range(n):
        pid = str(uuid.uuid4())
        creado = ahora - (offset_id + n - i) * 2
        actualizado = creado + 1.0
        ack = creado if owner_ack_at_presente else None
        out.append((pid, status, creado, actualizado, "x", tenant_id, ack))
    return out


async def _explain_y_handler_read(consulta, args):
    """Mismos tres pasos en la MISMA conexión que
    tests/test_pipelines_descarte.py::_explain_y_handler_read --
    `Handler_read%` es un contador de SESIÓN."""
    from db.connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("EXPLAIN " + consulta, args)
            cols = [d[0] for d in cur.description]
            explain_fila = await cur.fetchone()
            explain = dict(zip(cols, explain_fila))

            await cur.execute("FLUSH STATUS")
            await cur.execute(consulta, args)
            filas = await cur.fetchall()
            await cur.execute("SHOW SESSION STATUS LIKE 'Handler_read%'")
            handler = {k: int(v) for k, v in await cur.fetchall()}
    return explain, handler, filas


async def _medir(consulta, args, n=N_MUESTRAS):
    from db.connection import get_pool

    pool = await get_pool()
    tiempos = []
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for _ in range(n):
                t0 = time.perf_counter()
                await cur.execute(consulta, args)
                await cur.fetchall()
                tiempos.append((time.perf_counter() - t0) * 1000)
    return tiempos


def _p(valores, p):
    ordenados = sorted(valores)
    k = max(1, min(len(ordenados), -(-int(round(p * len(ordenados) / 100.0)))))
    return round(ordenados[k - 1], 4)


def _medir_forma(client, nombre_forma, tenant_id, args):
    """Corre las TRES variantes (natural, FORCE duenio del round 3, FORCE
    visibles del round 4 -- SQL_PIPELINES_DEL_USUARIO real) contra el mismo
    sembrado, imprime EXPLAIN + Handler_read + p50/p95 de cada una."""
    print(f"\n=== {nombre_forma} (tenant={tenant_id}) ===")
    for etiqueta, consulta in [
        ("natural (sin hint)", SQL_NATURAL),
        ("FORCE idx_jacobs_pipelines_duenio (round 3)", SQL_FORCE_DUENIO),
        ("FORCE idx_pipelines_visibles (round 4, DECISIÓN FINAL)", mod.SQL_PIPELINES_DEL_USUARIO),
    ]:
        explain, handler, filas = client.portal.call(_explain_y_handler_read, consulta, args)
        tiempos = client.portal.call(_medir, consulta, args)
        total_handler = sum(handler.values())
        print(f"{etiqueta}:")
        print(f"  EXPLAIN: key={explain['key']!r} type={explain['type']} rows={explain['rows']} "
              f"Extra={explain['Extra']!r}")
        print(f"  filas devueltas={len(filas)}  Handler_read total={total_handler}")
        print(f"  p50={_p(tiempos, 50)} ms  p95={_p(tiempos, 95)} ms  "
              f"min={round(min(tiempos), 4)}  max={round(max(tiempos), 4)}")


def test_medir_historial_largo(client):
    """Forma (a) del controlador: 5000 visibles + 50 descartadas."""
    tenant_id = "CARGA-HISTORIAL-LARGO"
    try:
        client.portal.call(_insertar_bulk, _filas(tenant_id, 5000, "completed"))
        client.portal.call(_insertar_bulk, _filas(tenant_id, 50, "discarded", offset_id=5000))
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)
        args = ("x", tenant_id, mod.LISTA_PIPELINES_MAX, 0)
        _medir_forma(client, "historial largo: 5000 visibles + 50 descartadas", tenant_id, args)
    finally:
        client.portal.call(_borrar_tenant, tenant_id)


def test_medir_muchos_descartados(client):
    """Forma (b) del controlador: 5000 descartadas + 3 vivas -- la forma
    que pagaba el costo lineal con FORCE INDEX (idx_jacobs_pipelines_duenio)."""
    tenant_id = "CARGA-MUCHOS-DESCARTADOS"
    try:
        client.portal.call(_insertar_bulk, _filas(tenant_id, 5000, "discarded"))
        client.portal.call(_insertar_bulk, _filas(tenant_id, 3, "completed", offset_id=5000))
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)
        args = ("x", tenant_id, mod.LISTA_PIPELINES_MAX, 0)
        _medir_forma(client, "muchos descartados: 5000 descartadas + 3 vivas", tenant_id, args)
    finally:
        client.portal.call(_borrar_tenant, tenant_id)


def test_medir_hijos_sin_ack(client):
    """Forma (c) del controlador (nueva en esta ronda): 5000 hijos de Ada
    sin `owner_ack_at` + 3 vivas -- `visible` los excluye igual que a las
    descartadas (su definición incluye `owner_ack_at IS NOT NULL`)."""
    tenant_id = "CARGA-HIJOS-SIN-ACK"
    try:
        client.portal.call(_insertar_bulk,
                           _filas(tenant_id, 5000, "running", owner_ack_at_presente=False))
        client.portal.call(_insertar_bulk, _filas(tenant_id, 3, "completed", offset_id=5000))
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)
        args = ("x", tenant_id, mod.LISTA_PIPELINES_MAX, 0)
        _medir_forma(client, "hijos sin ack: 5000 sin ack + 3 vivas", tenant_id, args)
    finally:
        client.portal.call(_borrar_tenant, tenant_id)
