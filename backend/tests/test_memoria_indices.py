"""Un indice que existe no es un indice que se usa. Antecedente medido en esta
casa (2026-09-11, jax_memory): el HNSW de `messages` existia y la busqueda
semantica NO lo usaba por un JOIN -- 58,5 ms contra 0,4 ms con 1.149 filas.

Este test mira el EXPLAIN de la consulta que el endpoint corre DE VERDAD, no
una parecida escrita a mano: se la pide al modulo (`memoria.SQL_LISTAR`,
`memoria.ARGS_EJEMPLO`)."""
from api.admin import memoria


async def _explain(sql, args):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("EXPLAIN " + sql, args)
            return await cur.fetchall()


def test_el_listado_usa_el_indice_y_no_ordena_en_memoria(client_superadmin):
    plan = client_superadmin.portal.call(_explain, memoria.SQL_LISTAR, memoria.ARGS_EJEMPLO)
    texto = " ".join(str(c) for fila in plan for c in fila)
    assert "idx_facts_revision" in texto, f"no usa el indice: {texto}"
    assert "Using filesort" not in texto, f"ordena en memoria: {texto}"
    assert "Using temporary" not in texto, f"tabla temporal: {texto}"
