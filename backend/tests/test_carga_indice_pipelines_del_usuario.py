"""Medición (fix round 2, Ruling 16 punto 5, 2026-09-22): SQL_PIPELINES_DEL_USUARIO
con IGNORE INDEX (idx_pipelines_descartados, idx_pipelines_ocultos) versus el
plan natural (sin IGNORE INDEX), en las DOS formas de dato que pidió el
controlador -- contra la base de TEST, nunca contra jax_memory.

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

Se salta por default (siembra 5000+ filas, no es parte del piso de CI
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

# El plan NATURAL -- SQL_PIPELINES_DEL_USUARIO tal como estaba ANTES de
# Ruling 16 (sin IGNORE INDEX). Copia literal, no una reconstrucción: si el
# texto de mod.SQL_PIPELINES_DEL_USUARIO cambia, este sigue siendo el plan
# que se está comparando -- el punto de esta medición es esa comparación,
# no el estado actual del código.
SQL_NATURAL = (
    "SELECT pipeline_id, name, status, created_at, updated_at FROM jacobs_pipelines "
    "WHERE user_id=%s AND tenant_id=%s AND owner_ack_at IS NOT NULL "
    "AND status NOT IN ('discarded','hidden') "
    "ORDER BY created_at DESC LIMIT %s OFFSET %s"
)

N_MUESTRAS = 15

# El esquema de jax (columnas/índices nuevos) lo asegura
# tests/conftest.py::_esquema_de_jax_en_la_base_de_test (fix round 2,
# Ruling 16 punto 3) -- ya no hace falta un fixture propio acá.


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


def _reportar(nombre_forma, tenant_id, args, explain_natural, explain_ignore, t_natural, t_ignore):
    print(f"\n=== {nombre_forma} (tenant={tenant_id}) ===")
    print(f"EXPLAIN natural : key={explain_natural['key']!r} type={explain_natural['type']} "
          f"rows={explain_natural['rows']} Extra={explain_natural['Extra']!r}")
    print(f"EXPLAIN IGNORE  : key={explain_ignore['key']!r} type={explain_ignore['type']} "
          f"rows={explain_ignore['rows']} Extra={explain_ignore['Extra']!r}")
    print(f"natural (n={len(t_natural)}): p50={_p(t_natural, 50)} ms  p95={_p(t_natural, 95)} ms  "
          f"min={round(min(t_natural), 4)} max={round(max(t_natural), 4)}")
    print(f"IGNORE  (n={len(t_ignore)}): p50={_p(t_ignore, 50)} ms  p95={_p(t_ignore, 95)} ms  "
          f"min={round(min(t_ignore), 4)} max={round(max(t_ignore), 4)}")


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
        explain_ignore = client.portal.call(_explain, mod.SQL_PIPELINES_DEL_USUARIO, args)
        t_natural = client.portal.call(_medir, SQL_NATURAL, args)
        t_ignore = client.portal.call(_medir, mod.SQL_PIPELINES_DEL_USUARIO, args)

        _reportar("historial largo: 5000 terminados + 50 descartados", tenant_id, args,
                  explain_natural, explain_ignore, t_natural, t_ignore)

        assert explain_ignore["key"] == "idx_jacobs_pipelines_duenio"
        assert "filesort" not in (explain_ignore["Extra"] or "")
    finally:
        client.portal.call(_borrar_tenant, tenant_id)


def test_medir_muchos_descartados(client):
    """Forma (b) del controlador: 60 descartados + 3 vivos -- la forma que
    ya había mostrado `Using filesort` en el plan natural en la ronda
    anterior."""
    tenant_id = "CARGA-MUCHOS-DESCARTADOS"
    try:
        client.portal.call(_insertar_bulk, _filas_de(tenant_id, 60, "discarded", descartado=True))
        client.portal.call(_insertar_bulk, _filas_de(tenant_id, 3, "completed", offset_id=60))
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)

        args = ("x", tenant_id, mod.LISTA_PIPELINES_MAX, 0)
        explain_natural = client.portal.call(_explain, SQL_NATURAL, args)
        explain_ignore = client.portal.call(_explain, mod.SQL_PIPELINES_DEL_USUARIO, args)
        t_natural = client.portal.call(_medir, SQL_NATURAL, args)
        t_ignore = client.portal.call(_medir, mod.SQL_PIPELINES_DEL_USUARIO, args)

        _reportar("muchos descartados: 60 descartados + 3 vivos", tenant_id, args,
                  explain_natural, explain_ignore, t_natural, t_ignore)

        assert explain_ignore["key"] == "idx_jacobs_pipelines_duenio"
        assert "filesort" not in (explain_ignore["Extra"] or "")
    finally:
        client.portal.call(_borrar_tenant, tenant_id)
