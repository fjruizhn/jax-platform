import logging
from datetime import date, datetime, time, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool
from redaccion import texto_de_error
from uso import cola

logger = logging.getLogger("admin.usage")

router = APIRouter(prefix="/api/admin")

# Task 7 (2026-09-15, hallazgos-laterales.md §4, opcion 1): rastro observable
# de las filas de axioma_usage que record_usage NO pudo escribir. Mismo patron
# que facet_health.write_failure_stats(): en memoria y no en la DB, porque la
# DB es justamente lo que puede estar caido. Por proceso: se reinicia con el
# servicio y cuenta desde el ultimo arranque, no un historico.
#
# Desde la cola durable (2026-09-15, Task 2 del plan 2026-09-15-cola-durable-uso)
# este contador ya NO cuenta cada fallo del INSERT: cuenta las filas que
# ADEMAS no pudieron dejarse en el respaldo de disco (uso/cola.py). Una fila
# encolada no esta perdida -- esta pendiente, y el drenaje la va a insertar.
# Confundir las dos cosas le diria al tablero "total incompleto" cuando el
# total se va a completar solo.
_registros_perdidos = 0
_ultimo_error: str | None = None
_ERROR_MAX = 255

# Marca de vida del drenaje (backend/uso/reintento.py, Task 3): ISO 8601 del
# ultimo ciclo. Vive aca y no en cola.py porque cola.py es el contrato copiado
# al repo jax, y jax NO drena -- solo deposita. Sin este dato, "hay 5
# pendientes" no distingue una cola que avanza de un reintento muerto.
_ultimo_reintento: str | None = None


# Quien deposito la fila en el respaldo. Los otros dos valores del contrato
# ("jacobs", "motor_registry") son de los escritores del repo jax. No es
# configuracion: identifica al proceso, y el frozenset de cola.py lo valida.
ORIGEN = "platform"
assert ORIGEN in cola.ORIGENES


def marcar_reintento(cuando: str | None = None) -> None:
    """La llama el drenaje al terminar un ciclo. Sin argumento, ahora."""
    global _ultimo_reintento
    _ultimo_reintento = cuando or datetime.now(timezone.utc).isoformat()

CODIGO_IDS_INVALIDOS = "ids_de_uso_invalidos"
# Un BIGINT sin signo tiene 20 digitos: mas largo no es un id, y la cota
# mantiene la validacion en O(1).
_ID_MAX_DIGITOS = 20


def registros_perdidos_stats() -> dict:
    """Lo publica GET /api/admin/usage. Dos estados distintos, no uno:

    - `en_cola` / `ultimo_reintento`: PENDIENTE. El total de arriba esta
      incompleto pero se va a completar solo.
    - `registros_perdidos` / `perdidas_por_desborde`: PERDIDO de verdad. El
      total nunca se va a completar y hay que decirlo fuerte.

    `en_cola` y `perdidas_por_desborde` salen de uso/cola.py: `en_cola` es la
    ultima profundidad MEDIDA del disco (no un contador en memoria), porque
    Jacobs y LAS MANOS depositan en el mismo directorio sin pasar por este
    proceso. Se actualiza en cada encolado y en cada ciclo de drenaje.
    """
    respaldo = cola.estadisticas()
    return {
        "registros_perdidos": _registros_perdidos,
        "ultimo_error": _ultimo_error,
        "en_cola": respaldo["en_cola"],
        "perdidas_por_desborde": respaldo["perdidas_por_desborde"],
        "ultimo_reintento": _ultimo_reintento,
    }


def reset_registros_perdidos() -> None:
    """Solo para tests -- que cada test parta de cero sin depender del orden."""
    global _registros_perdidos, _ultimo_error, _ultimo_reintento
    _registros_perdidos = 0
    _ultimo_error = None
    _ultimo_reintento = None


def _es_id(valor) -> bool:
    return (
        isinstance(valor, str)
        and 0 < len(valor) <= _ID_MAX_DIGITOS
        and valor.isascii()
        and valor.isdigit()
    )


def validar_ids_de_uso(user_id, tenant_id) -> None:
    """Task 7: axioma_usage guarda tenant_id/user_id como enteros. Un id no
    numerico hacia fallar el INSERT DESPUES de pagarle al proveedor; se
    rechaza en la entrada, ANTES de resolver credencial o llamar al LLM.
    Pura: sin I/O, O(1) (longitud acotada). Los ids salen del JWT
    (auth/middleware.py): user_id ya viene validado como int, pero tenant_id
    se copia tal cual del token (default "" si falta), asi que un token con
    tenant no numerico si llega aca."""
    if not (_es_id(user_id) and _es_id(tenant_id)):
        raise HTTPException(status_code=400, detail={"code": CODIGO_IDS_INVALIDOS})


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
    (ej. generacion de imagenes).

    Devuelve el id de la fila escrita, o None si no se pudo escribir (fix
    wave final, 2026-09-15: los tests de chat borran exactamente las filas
    que escribieron; los llamadores de produccion lo ignoran)."""
    # Fuera del try: si el fallo es la consulta de precio (tambien va a la
    # base), el `except` todavia tiene que poder encolar la fila. El gasto
    # ocurrio igual; lo que no se sabe es cuanto, y eso es exactamente lo que
    # significa cost_usd NULL (nunca un numero inventado).
    cost = cost_usd_override
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
                fila = cur.lastrowid
            await conn.commit()
        return fila
    except Exception as e:  # fail-soft: el turno ya se pagó y ya respondió; un 500 no recupera el costo y le quita la respuesta al usuario; la fila NO se pierde (va al respaldo de uso/cola.py, que el drenaje reinserta), y lo que no se pudo ni encolar lo hace visible registros_perdidos (GET /api/admin/usage) y el WARNING
        global _registros_perdidos, _ultimo_error
        motivo = texto_de_error(e)[:_ERROR_MAX]
        if await _encolar_la_fila_perdida(
                user_id, tenant_id, facet, model, tokens_in, tokens_out,
                cost, request_type, motivo):
            return None
        _registros_perdidos += 1
        _ultimo_error = motivo
        # Prefijo estable: se cuenta desde journalctl sin depender del endpoint.
        # El texto del error pasa por la redaccion (Task 6): puede venir del
        # proveedor o de la DB.
        logger.warning(
            "record_usage failed facet=%s model=%s total=%d reason=%s",
            facet, model, _registros_perdidos, _ultimo_error,
        )
        return None


async def _encolar_la_fila_perdida(
    user_id, tenant_id, facet, model, tokens_in, tokens_out, cost,
    request_type, motivo: str,
) -> bool:
    """Deja la fila en el respaldo de disco. True si entro.

    `created_at` es la hora del TURNO, fijada aca y no en el reintento: si la
    pusiera el drenaje, una caida de dos horas moveria el costo al dia
    siguiente. `tenant_id`/`user_id` se guardan tal como llegaron (los valida
    validar_ids_de_uso ANTES del LLM); el drenaje los convierte con int(), que
    es lo que hace el INSERT de arriba y lo que ya guardan las copias de jax.

    `encolar` promete no propagar, pero esto corre DENTRO del `except` de
    record_usage: si la promesa se rompiera, la excepcion saldria por arriba y
    el turno ya cobrado terminaria en 500. El try de mas es barato; la
    respuesta del usuario, no.
    """
    try:
        spool_id = await cola.encolar({
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "facet": facet,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
            "request_type": request_type,
            "origen": ORIGEN,
        })
    except Exception as e:  # fail-soft: el respaldo es la red de seguridad, no puede ser lo que tire el turno; si falla, el camino de abajo cuenta la perdida y deja el WARNING
        logger.warning("record_usage: el respaldo de uso falló: %s", texto_de_error(e)[:_ERROR_MAX])
        return False
    if not spool_id:
        return False
    # INFO, no WARNING: nada se perdio. El WARNING queda para lo que si.
    logger.info(
        "record_usage encolada facet=%s model=%s spool_id=%s reason=%s",
        facet, model, spool_id, motivo,
    )
    return True


# Fix wave final, item 8 (2026-09-15): la prueba de carga del gate U29 dio
# NO-GO (102k filas, ~11 rps planos, p95 ~2,7 s con c=25; EXPLAIN `ALL` +
# temporary + filesort). La causa fue que NO HABIA INDICE: axioma_usage solo
# tenia PRIMARY. Lo resuelve idx_axioma_usage_periodo (db/migrations.py),
# cubriente: el rango se lee del indice sin tocar la fila.
# El rango `created_at >= <00:00 del dia>` es sargable A PROPOSITO y no
# depende del optimizador: MariaDB >= 11.1 reescribe por su cuenta
# `DATE(created_at) >= dia` como rango, pero una version anterior, otro
# motor o una forma que la reescritura no cubra no lo harian. Devuelve
# exactamente lo mismo (created_at es TIMESTAMP: DATE() y la comparacion usan
# la misma zona de la sesion). El test del texto del WHERE lo fija
# (tests/test_uso_por_periodo.py), porque el EXPLAIN no distingue las formas. El `Using temporary; Using filesort` que queda es
# sobre los GRUPOS, no sobre las filas (tests/test_uso_por_periodo.py).
# ORDER BY SUM(cost_usd): antes `ORDER BY cost_usd` ordenaba por el costo de
# una fila cualquiera de cada grupo.
SQL_USO_POR_FACETA = """
    SELECT facet, model, SUM(tokens_in), SUM(tokens_out), SUM(cost_usd), COUNT(*), request_type,
           SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced_requests
    FROM axioma_usage
    WHERE created_at >= %s
    GROUP BY facet, model, request_type
    ORDER BY SUM(cost_usd) DESC
"""
SQL_USO_GRAFICO = """
    SELECT facet, DATE(created_at) AS day, COUNT(*) AS cnt
    FROM axioma_usage
    WHERE created_at >= %s
    GROUP BY facet, day
"""


def _inicio_del_dia(dia: date) -> datetime:
    """Limite inferior del rango: `created_at >= 00:00:00 de dia` equivale
    a `DATE(created_at) >= dia`."""
    return datetime.combine(dia, time.min)


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
            await cur.execute(SQL_USO_POR_FACETA, (_inicio_del_dia(since),))
            rows = await cur.fetchall()

            labels = [(date.today() - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
            await cur.execute(SQL_USO_GRAFICO, (_inicio_del_dia(date.today() - timedelta(days=7)),))
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
        # Task 7: filas que record_usage no pudo escribir desde el arranque de
        # ESTE proceso. > 0 = el total de arriba esta incompleto.
        "registros_perdidos": _registros_perdidos,
    }
