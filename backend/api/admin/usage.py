import logging
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

logger = logging.getLogger("admin.usage")

router = APIRouter(prefix="/api/admin")


async def _lookup_model_price(provider_id: str, model: str) -> tuple[float | None, float | None]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT price_input_per_1m_usd, price_output_per_1m_usd "
                "FROM model WHERE provider_id=%s AND model_id=%s",
                (provider_id, model),
            )
            row = await cur.fetchone()
    if not row:
        return None, None
    return row[0], row[1]


async def record_usage(
    user_id: str,
    tenant_id: str,
    facet: str,
    provider_id: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    request_type: str = "chat",
    cost_usd_override: float | None = None,
):
    """Llamar desde chat.py e image.py para registrar uso. Costo real desde
    `model` (Bloque D) — nunca un dict hardcodeado. Si el modelo no esta en
    el catalogo (nunca corrio un sync), cost_usd queda NULL con el motivo
    visible en el propio dato (nunca un numero inventado). cost_usd_override
    es para pricing plano-por-request que no encaja en precio-por-token
    (ej. generacion de imagenes)."""
    try:
        if cost_usd_override is not None:
            cost = cost_usd_override
        else:
            price_in, price_out = await _lookup_model_price(provider_id, model)
            if price_in is None or price_out is None:
                cost = None
            else:
                # round(): la división en float casi siempre produce más
                # decimales de los que cost_usd DECIMAL(10,6) puede guardar
                # exactos -- sin esto MariaDB redondea igual al insertar
                # pero emite "Data truncated for column 'cost_usd'" en cada
                # request (no es perdida de magnitud, el valor guardado ya
                # era correcto; ensanchar la columna no lo evita, cualquier
                # float sigue excediendo una precision fija en algun punto).
                cost = round((tokens_in * float(price_in) + tokens_out * float(price_out)) / 1_000_000, 6)

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, tokens_out, cost_usd, request_type) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (int(tenant_id), int(user_id), facet, model, tokens_in, tokens_out, cost, request_type),
                )
            await conn.commit()
    except Exception as e:
        logger.warning(f"record_usage failed facet={facet} model={model} reason={type(e).__name__}: {e}")
        # usage tracking is best-effort — no re-raise, pero el fallo queda visible en logs


@router.get("/usage")
async def get_usage(
    period: str = Query("day", pattern="^(day|week|month)$"),
    user: AuthUser = Depends(require_superadmin),
):
    days = {"day": 1, "week": 7, "month": 30}[period]
    since = date.today() - timedelta(days=days)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT facet, model, SUM(tokens_in), SUM(tokens_out), SUM(cost_usd), COUNT(*), request_type,
                       SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced_requests
                FROM axioma_usage
                WHERE DATE(created_at) >= %s
                GROUP BY facet, model, request_type
                ORDER BY cost_usd DESC
                """,
                (since.isoformat(),),
            )
            rows = await cur.fetchall()

            labels = [(date.today() - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
            await cur.execute(
                """
                SELECT facet, DATE(created_at) as day, COUNT(*) as cnt
                FROM axioma_usage
                WHERE DATE(created_at) >= %s
                GROUP BY facet, day
                """,
                ((date.today() - timedelta(days=7)).isoformat(),),
            )
            chart_rows = await cur.fetchall()

    by_facet = [
        {
            "facet": r[0],
            "model": r[1],
            "tokens_in": int(r[2] or 0),
            "tokens_out": int(r[3] or 0),
            "cost_usd": float(r[4]) if r[4] is not None else None,
            "requests": int(r[5] or 0),
            "request_type": r[6],
            "unpriced_requests": int(r[7] or 0),
        }
        for r in rows
    ]

    chart_map: dict = {}
    for facet, day, cnt in chart_rows:
        if facet not in chart_map:
            chart_map[facet] = {l: 0 for l in labels}
        if day.isoformat() in chart_map[facet]:
            chart_map[facet][day.isoformat()] = int(cnt)

    datasets = {f: [chart_map.get(f, {}).get(l, 0) for l in labels] for f in chart_map}

    return {
        "by_facet": by_facet,
        "chart_data": {"labels": labels, "datasets": datasets},
        "period": period,
    }
