"""Qué facetas aceptan imágenes HOY (frente D, 2026-09-16). Mismo JOIN que
facet_resolver._query_facet: facet -> facet_binding(primary) -> model.

Medido con EXPLAIN sobre jax_memory_test (ledger, ronda R13): `f` y `m` salen
`eq_ref` por `PRIMARY`. `b` (facet_binding) sale con un escaneo completo
elegido por el optimizador — NO porque `uk_facet_role (facet_key, role)`
esté roto o sin usar: forzado con `FORCE INDEX (uk_facet_role)` sigue dando
`ALL` en el orden de join que el optimizador prefiere (b primero, sin valor
de facet_key todavía), pero con `STRAIGHT_JOIN` desde `f` el mismo índice
responde `eq_ref` (`ref=f.key,const`). La consulta no tiene predicado
selectivo sobre `b` más allá de `role='primary'` (que sin `facet_key` no
alcanza para un seek), y `facet_binding` es hoy un catálogo de 7 filas
(facetas × roles con binding) — del mismo orden que `facet`. El índice
existe y es usable; el optimizador prefiere no usarlo a esta escala.
`FIND_IN_SET` sobre el SET de `model.input_modalities` tampoco puede usar
índice por diseño.

Sin caché: se pide una vez al montar la barra, y el 422 del chat es la
verdad si el binding cambió después."""
from db.connection import get_pool

_CONSULTA = (
    "SELECT f.`key` FROM facet f "
    "JOIN facet_binding b ON b.facet_key = f.`key` AND b.role = 'primary' "
    "JOIN model m ON m.id = b.model_ref "
    "WHERE f.status = 'active' AND FIND_IN_SET('image', m.input_modalities) > 0 "
    "ORDER BY f.`key`"
)


async def facetas_con_imagen() -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_CONSULTA)
            return [fila[0] for fila in await cur.fetchall()]
