"""Medición (fix round 3, Ruling 17 punto 3, 2026-09-22): SQL_PIPELINES_DEL_USUARIO
con FORCE INDEX (idx_jacobs_pipelines_duenio) versus el plan natural (sin
FORCE INDEX), en las TRES formas de dato que pidió el controlador -- contra
la base de TEST, nunca contra jax_memory.

Historia de esta consulta, para no perder el porqué: fix round 1 (Ruling
13(e)) aceptó un plan con `Using filesort` a partir de UNA medición floja
(363 filas, 1,6x, ruido). Fix round 2 (Ruling 16) lo reemplazó por
`IGNORE INDEX (idx_pipelines_descartados, idx_pipelines_ocultos)` -- medido
acá abajo como `SQL_IGNORE` -- que SÍ daba el plan determinista, pero tenía
dos problemas: acoplaba el deploy de jax-platform a una migración de `jax`
(jax#257) mergeada pero NO desplegada en producción a la fecha de este
archivo (`IGNORE INDEX` con un nombre que no existe es un ERROR de MariaDB,
1176, no un hint que se ignora), y no cubría la forma EXTREMA de abajo
(elegía `idx_pipelines_status`, fuera de la lista de índices ignorados, con
filesort igual). Fix round 3 (Ruling 17, ESTE archivo) lo reemplaza por
`FORCE INDEX (idx_jacobs_pipelines_duenio)` -- ese índice lo crea una
migración de `jax` de una semana ANTES de jax#257 (Ruling T6-6,
2026-09-15) que sí está desplegada en producción hoy.

Por qué esto vive acá y no en loadtest/ (divergencia deliberada, anotada):
los demás loadtest/*.py son procesos standalone que abren su PROPIA conexión
a MariaDB fuera de pytest -- correcto para ELLOS porque miden HTTP de punta a
punta con un backend real levantado aparte. La regla de esta sesión es más
estricta ("DB tests ONLY through pytest ... Never query MariaDB outside
pytest"): esta medición es SQL puro (EXPLAIN + tiempo de
`cur.execute`/`fetchall`), así que corre como test de pytest, con el mismo
mecanismo de aislamiento (`base_de_test`) que el resto de la suite. El
"comando exacto" documentado en docs/ es la invocación de pytest, no un
script aparte.

Se salta por default (siembra hasta 5000+ filas, no es parte del piso de CI
normal): correr con JAX_MEDIR_INDICE_PIPELINES=1.
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

# El plan NATURAL -- SQL_PIPELINES_DEL_USUARIO tal como estaba ANTES del fix
# round 2 (sin ningún hint de índice). Copia literal, no una
# reconstrucción: si el texto de mod.SQL_PIPELINES_DEL_USUARIO cambia, este
# sigue siendo el plan que se está comparando -- el punto de esta medición
# es esa comparación, no el estado actual del código.
SQL_NATURAL = (
    "SELECT pipeline_id, name, status, created_at, updated_at FROM jacobs_pipelines "
    "WHERE user_id=%s AND tenant_id=%s AND owner_ack_at IS NOT NULL "
    "AND status NOT IN ('discarded','hidden') "
    "ORDER BY created_at DESC LIMIT %s OFFSET %s"
)

N_MUESTRAS = 15

# El esquema de jax (columnas/índices nuevos) lo asegura
# tests/conftest.py::client() (fix round 3, Ruling 16 punto 3 -- ya no hace
# falta un fixture propio acá).


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
                " user_id, tenant_id, owner_ack_at, status_previo, descartado_por, descartado_at) "
                "VALUES (%s, 'carga', 'plataforma', 'supervised', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                filas,
            )
        await conn.commit()


async def _borrar_tenant(tenant_id):
    await sql("DELETE FROM jacobs_pipelines WHERE tenant_id=%s", (tenant_id,))


def _filas_de(tenant_id, n, status, descartado=False, offset_id=0):
    ahora = time.time()
    out = []
    for i in range(n):
        pid = str(uuid.uuid4())
        creado = ahora - (offset_id + n - i) * 2
        actualizado = creado + 1.0
        if descartado:
            out.append((pid, "discarded", creado, actualizado, "x", tenant_id, creado,
                        "aborted", "x", creado))
        else:
            out.append((pid, status, creado, actualizado, "x", tenant_id, creado,
                        None, None, None))
    return out


async def _explain(consulta, args):
    from db.connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("EXPLAIN " + consulta, args)
            fila = await cur.fetchone()
    campos = ("id", "select_type", "table", "type", "possible_keys", "key",
              "key_len", "ref", "rows", "Extra")
    return dict(zip(campos, fila))


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


def _reportar(nombre_forma, tenant_id, explain_natural, explain_forzado, t_natural, t_forzado):
    print(f"\n=== {nombre_forma} (tenant={tenant_id}) ===")
    print(f"EXPLAIN natural: key={explain_natural['key']!r} type={explain_natural['type']} "
          f"rows={explain_natural['rows']} Extra={explain_natural['Extra']!r}")
    print(f"EXPLAIN FORCE  : key={explain_forzado['key']!r} type={explain_forzado['type']} "
          f"rows={explain_forzado['rows']} Extra={explain_forzado['Extra']!r}")
    print(f"natural (n={len(t_natural)}): p50={_p(t_natural, 50)} ms  p95={_p(t_natural, 95)} ms  "
          f"min={round(min(t_natural), 4)} max={round(max(t_natural), 4)}")
    print(f"FORCE   (n={len(t_forzado)}): p50={_p(t_forzado, 50)} ms  p95={_p(t_forzado, 95)} ms  "
          f"min={round(min(t_forzado), 4)} max={round(max(t_forzado), 4)}")


def test_medir_historial_largo(client):
    """Forma (a) del controlador: 5000 pipelines terminados + 50 descartados
    -- el historial NO tiene techo (a diferencia de MAX_PIPELINES, que sólo
    acota los CONCURRENTES), así que esta es la forma que crece sin límite
    en producción real con el tiempo."""
    tenant_id = "CARGA-HISTORIAL-LARGO"
    try:
        client.portal.call(_insertar_bulk, _filas_de(tenant_id, 5000, "completed"))
        client.portal.call(_insertar_bulk, _filas_de(tenant_id, 50, "discarded", descartado=True, offset_id=5000))
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)

        args = ("x", tenant_id, mod.LISTA_PIPELINES_MAX, 0)
        explain_natural = client.portal.call(_explain, SQL_NATURAL, args)
        explain_forzado = client.portal.call(_explain, mod.SQL_PIPELINES_DEL_USUARIO, args)
        t_natural = client.portal.call(_medir, SQL_NATURAL, args)
        t_forzado = client.portal.call(_medir, mod.SQL_PIPELINES_DEL_USUARIO, args)

        _reportar("historial largo: 5000 terminados + 50 descartados", tenant_id,
                  explain_natural, explain_forzado, t_natural, t_forzado)

        assert explain_forzado["key"] == "idx_jacobs_pipelines_duenio"
        assert "filesort" not in (explain_forzado["Extra"] or "")
    finally:
        client.portal.call(_borrar_tenant, tenant_id)


def test_medir_muchos_descartados(client):
    """Forma (b) del controlador: 60 descartados + 3 vivos -- la forma que
    ya había mostrado `Using filesort` en el plan natural en el fix round 1."""
    tenant_id = "CARGA-MUCHOS-DESCARTADOS"
    try:
        client.portal.call(_insertar_bulk, _filas_de(tenant_id, 60, "discarded", descartado=True))
        client.portal.call(_insertar_bulk, _filas_de(tenant_id, 3, "completed", offset_id=60))
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)

        args = ("x", tenant_id, mod.LISTA_PIPELINES_MAX, 0)
        explain_natural = client.portal.call(_explain, SQL_NATURAL, args)
        explain_forzado = client.portal.call(_explain, mod.SQL_PIPELINES_DEL_USUARIO, args)
        t_natural = client.portal.call(_medir, SQL_NATURAL, args)
        t_forzado = client.portal.call(_medir, mod.SQL_PIPELINES_DEL_USUARIO, args)

        _reportar("muchos descartados: 60 descartados + 3 vivos", tenant_id,
                  explain_natural, explain_forzado, t_natural, t_forzado)

        assert explain_forzado["key"] == "idx_jacobs_pipelines_duenio"
        assert "filesort" not in (explain_forzado["Extra"] or "")
    finally:
        client.portal.call(_borrar_tenant, tenant_id)


def test_medir_extremo_muchos_descartados_tenant_aislado(client):
    """Forma "extrema" (fix round 3, Ruling 17 -- el hallazgo que destapó el
    fix round 2): 5000 descartados + 3 vivos, en un tenant AISLADO (sin
    fondo de otros tenants). Con `IGNORE INDEX` (fix round 2) esta forma
    elegía `idx_pipelines_status` -- fuera de la lista de índices
    ignorados, sin `user_id`/`tenant_id`, escala con el total de pipelines
    "vivos" de TODOS los tenants -- con `Using filesort` igual. Es la forma
    que `FORCE INDEX (idx_jacobs_pipelines_duenio)` sí cubre, nombrando el
    índice correcto directo en vez de una lista de exclusiones."""
    tenant_id = "CARGA-EXTREMO-AISLADO"
    try:
        client.portal.call(_insertar_bulk, _filas_de(tenant_id, 5000, "discarded", descartado=True))
        client.portal.call(_insertar_bulk, _filas_de(tenant_id, 3, "completed", offset_id=5000))
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)

        args = ("x", tenant_id, mod.LISTA_PIPELINES_MAX, 0)
        explain_natural = client.portal.call(_explain, SQL_NATURAL, args)
        explain_forzado = client.portal.call(_explain, mod.SQL_PIPELINES_DEL_USUARIO, args)
        t_natural = client.portal.call(_medir, SQL_NATURAL, args)
        t_forzado = client.portal.call(_medir, mod.SQL_PIPELINES_DEL_USUARIO, args)

        _reportar("extremo: 5000 descartados + 3 vivos, tenant aislado", tenant_id,
                  explain_natural, explain_forzado, t_natural, t_forzado)

        assert explain_forzado["key"] == "idx_jacobs_pipelines_duenio"
        assert "filesort" not in (explain_forzado["Extra"] or "")
    finally:
        client.portal.call(_borrar_tenant, tenant_id)
