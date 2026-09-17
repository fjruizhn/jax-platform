import asyncio
import json
from collections import deque

from fastapi import APIRouter, Depends, HTTPException

from auth.middleware import require_superadmin
from auth.models import AuthUser
from config_de_entorno import ruta_requerida

router = APIRouter(prefix="/api")

AUDIT_LOG = ruta_requerida("JAX_AUDIT_LOG_PATH")


def _ultimas_20_lineas(ruta) -> list[str]:
    """A-41 (2026-09-16): recorre el archivo una vez guardando solo 20 líneas.
    Las vacías se filtran ANTES de entrar al deque; si no, contarían entre las
    20 y saldrían menos eventos."""
    with open(ruta, encoding="utf-8") as archivo:
        return list(deque((linea for linea in archivo if linea.strip()), maxlen=20))


# Task 6 S3 (2026-09-15): el log forense de LAS MANOS (hosts, capacidades,
# el motivo de cada rechazo de politica y stdout/stderr de lo que ejecutan
# las facetas) solo exigia sesion; un viewer lo leia entero. Solo superadmin.
@router.get("/audit")
async def get_audit(user: AuthUser = Depends(require_superadmin)):
    if not AUDIT_LOG.exists():
        return {"events": []}
    try:
        lineas = await asyncio.to_thread(_ultimas_20_lineas, AUDIT_LOG)
    except (OSError, UnicodeDecodeError) as exc:
        # Task 3 (2026-09-15, clase b): antes `except Exception` devolvia
        # {"events": []} -- un audit ilegible se veia igual que "no hubo
        # eventos". Un fallo del camino de auditoria no se disfraza de sano.
        raise HTTPException(status_code=503, detail="auditoria_ilegible") from exc
    events = []
    for line in reversed(lineas):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:  # fail-soft: descarta una linea JSONL corrupta entre las ultimas 20 mostradas; el resto del log se muestra igual, no es un fallo total silencioso
            pass
    return {"events": events}
