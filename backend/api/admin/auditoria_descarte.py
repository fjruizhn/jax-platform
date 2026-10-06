"""Feed global de solo lectura de los eventos de descarte para superadmin."""
from __future__ import annotations

import base64
import binascii
import json
import math
import os
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.middleware import require_superadmin
from db.connection import get_pool

router = APIRouter(prefix="/api/admin")

LIMITE_MAX = 50
_CURSOR_MAX = 128
_RANGO_MAX_DIAS = 366
TIPOS_EVENTO = (
    "PIPELINE_DISCARDED",
    "PIPELINE_RECOVERED",
    "PIPELINE_HIDDEN",
    "PIPELINE_RESTORED",
)


# Cuatro consultas por tipo permiten que MariaDB recorra el índice en orden
# sin ordenar ni materializar el conjunto combinado. Cada consulta trae
# limite+1; el merge de Python queda acotado a 4*(limite+1) filas.
SQL_GLOBAL = (
    "SELECT e.id, e.pipeline_id, e.event_type, e.payload, e.ts, p.name, p.tenant_id "
    "FROM jacobs_events AS e FORCE INDEX (idx_events_auditoria_fecha) "
    "LEFT JOIN jacobs_pipelines AS p FORCE INDEX (PRIMARY) ON p.pipeline_id=e.pipeline_id "
    "WHERE e.event_type=%s AND e.ts >= %s AND e.ts < %s "
)
SQL_PIPELINE_GLOBAL = (
    "SELECT e.id, e.pipeline_id, e.event_type, e.payload, e.ts, p.name, p.tenant_id "
    "FROM jacobs_events AS e FORCE INDEX (idx_events_pipeline_auditoria_fecha) "
    "LEFT JOIN jacobs_pipelines AS p FORCE INDEX (PRIMARY) ON p.pipeline_id=e.pipeline_id "
    "WHERE e.pipeline_id=%s AND e.event_type=%s AND e.ts >= %s AND e.ts < %s "
)


@lru_cache(maxsize=1)
def _zona_plataforma():
    """Cachea la zona por proceso; un reinicio toma cambios de TZ o /etc/localtime."""
    nombre = os.environ.get("TZ")
    if nombre:
        try:
            return ZoneInfo(nombre)
        except ZoneInfoNotFoundError as exc:
            raise RuntimeError(f"Zona horaria TZ inválida: {nombre}") from exc
    try:
        with open("/etc/localtime", "rb") as archivo:
            return ZoneInfo.from_file(archivo, key="local")
    except OSError as exc:
        raise RuntimeError("No se pudo leer la zona horaria local de la plataforma") from exc


def _codificar_cursor(ts: float, event_id: int) -> str:
    datos = json.dumps([float(ts), int(event_id)], separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(datos).decode("ascii").rstrip("=")


def _decodificar_cursor(cursor: str | None) -> tuple[float, int] | None:
    if cursor is None:
        return None
    try:
        crudo = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        datos = json.loads(crudo)
        if (not isinstance(datos, list) or len(datos) != 2
                or isinstance(datos[0], bool) or not isinstance(datos[0], (int, float))
                or not math.isfinite(float(datos[0]))
                or isinstance(datos[1], bool) or not isinstance(datos[1], int) or datos[1] < 1):
            raise ValueError("forma inválida")
        return float(datos[0]), datos[1]
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="cursor_invalido") from exc


def _rango_epoch(desde: date | None, hasta: date | None) -> tuple[float, float]:
    zona = _zona_plataforma()
    hoy_local = datetime.now(zona).date()
    fecha_hasta = hasta or hoy_local
    try:
        fecha_desde = desde or (fecha_hasta - timedelta(days=30))
    except OverflowError as exc:
        raise HTTPException(status_code=422, detail="rango_fechas_invalido") from exc
    if fecha_desde > fecha_hasta:
        raise HTTPException(status_code=422, detail="rango_fechas_invalido")
    if (fecha_hasta - fecha_desde).days > _RANGO_MAX_DIAS:
        raise HTTPException(status_code=422, detail="rango_fechas_maximo")
    try:
        inicio = datetime.combine(fecha_desde, time.min, tzinfo=zona)
        fin_exclusivo = datetime.combine(fecha_hasta + timedelta(days=1), time.min, tzinfo=zona)
        return inicio.timestamp(), fin_exclusivo.timestamp()
    except (OverflowError, OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="rango_fechas_invalido") from exc


def _payload(payload) -> dict:
    if isinstance(payload, dict):
        return payload
    try:
        datos = json.loads(payload) if payload else {}
    except (TypeError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


@router.get("/auditoria-descarte", dependencies=[Depends(require_superadmin)])
async def listar_auditoria_descarte(
    evento: str | None = Query(None, pattern="^(PIPELINE_DISCARDED|PIPELINE_RECOVERED|PIPELINE_HIDDEN|PIPELINE_RESTORED)$"),
    pipeline_id: str | None = Query(None, min_length=1, max_length=36),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    limite: int = Query(LIMITE_MAX, ge=1, le=LIMITE_MAX),
    cursor: str | None = Query(None, min_length=1, max_length=_CURSOR_MAX),
):
    """Superadmin consulta todos los tenants, incluidos pipelines sin tenant."""
    inicio, fin = _rango_epoch(desde, hasta)
    posicion = _decodificar_cursor(cursor)
    tipos = (evento,) if evento else TIPOS_EVENTO
    filas = []
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for tipo in tipos:
                if pipeline_id:
                    consulta = SQL_PIPELINE_GLOBAL
                    parametros: list = [pipeline_id, tipo, inicio, fin]
                else:
                    consulta = SQL_GLOBAL
                    parametros = [tipo, inicio, fin]
                if posicion is not None:
                    ts_cursor, id_cursor = posicion
                    consulta += "AND (e.ts < %s OR (e.ts = %s AND e.id < %s)) "
                    parametros.extend((ts_cursor, ts_cursor, id_cursor))
                consulta += "ORDER BY e.ts DESC, e.id DESC LIMIT %s"
                parametros.append(limite + 1)
                await cur.execute(consulta, parametros)
                filas.extend(await cur.fetchall())

    filas.sort(key=lambda fila: (float(fila[4]), int(fila[0])), reverse=True)
    pagina = filas[:limite]
    hay_mas = len(filas) > limite
    eventos = []
    for event_id, pid, tipo, payload, ts, nombre, tenant_id in pagina:
        datos = _payload(payload)
        eventos.append({
            "id": int(event_id),
            "pipeline_id": str(pid),
            "pipeline_name": nombre,
            "tenant_id": tenant_id,
            "event_type": tipo,
            "actor": datos.get("user_id"),
            "ts": float(ts),
        })
    cursor_siguiente = _codificar_cursor(pagina[-1][4], pagina[-1][0]) if hay_mas and pagina else None
    return {"eventos": eventos, "has_more": hay_mas, "cursor_siguiente": cursor_siguiente}
