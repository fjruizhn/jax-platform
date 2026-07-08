from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin")

MODEL_PRICES = {
    "gpt-4o":            {"in": 5.00,  "out": 30.00},
    "gpt-image-1":       {"in": 0.00,  "out": 0.00, "per_image": 0.04},
    "deepseek-v4-flash": {"in": 0.14,  "out": 0.28},
    "gemini-2.5-flash":  {"in": 0.15,  "out": 0.60},
    "kimi-k2.7-code":    {"in": 0.95,  "out": 4.00},
    "glm-5.2":           {"in": 1.40,  "out": 4.40},
}


async def record_usage(
    user_id: str,
    tenant_id: str,
    facet: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    request_type: str = "chat",
):
    """Llamar desde chat.py y image.py para registrar uso."""
    prices = MODEL_PRICES.get(model, {"in": 0, "out": 0})
    cost = (tokens_in * prices["in"] + tokens_out * prices["out"]) / 1_000_000
    if request_type == "imagen" and "per_image" in prices:
        cost = prices["per_image"]

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, tokens_out, cost_usd, request_type) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (tenant_id, user_id, facet, model, tokens_in, tokens_out, cost, request_type),
                )
    except Exception:
        pass  # usage tracking is best-effort


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
                SELECT facet, model, SUM(tokens_in), SUM(tokens_out), SUM(cost_usd), COUNT(*), request_type
                FROM axioma_usage
                WHERE DATE(created_at) >= %s
                GROUP BY facet, model, request_type
                ORDER BY cost_usd DESC
                """,
                (since.isoformat(),),
            )
            rows = await cur.fetchall()

            # Chart data: requests per facet per day (last 7 days)
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
            "cost_usd": float(r[4] or 0),
            "requests": int(r[5] or 0),
            "request_type": r[6],
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
