import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from auth.middleware import require_superadmin
from auth.models import AuthUser

router = APIRouter(prefix="/api")

AUDIT_LOG = Path.home() / "jax" / "las_manos" / "logs" / "audit.jsonl"


# Task 6 S3 (2026-09-15): el log forense de LAS MANOS (hosts, capacidades,
# el motivo de cada rechazo de politica y stdout/stderr de lo que ejecutan
# las facetas) solo exigia sesion; un viewer lo leia entero. Solo superadmin.
@router.get("/audit")
async def get_audit(user: AuthUser = Depends(require_superadmin)):
    if not AUDIT_LOG.exists():
        return {"events": []}
    try:
        text = AUDIT_LOG.read_text().strip()
        if not text:
            return {"events": []}
        lines = [l for l in text.split("\n") if l.strip()]
        last_20 = lines[-20:]
        events = []
        for line in reversed(last_20):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:  # fail-soft: descarta una linea JSONL corrupta entre las ultimas 20 mostradas; el resto del log se muestra igual, no es un fallo total silencioso
                pass
        return {"events": events}
    except (OSError, UnicodeDecodeError) as exc:
        # Task 3 (2026-09-15, clase b): antes `except Exception` devolvia
        # {"events": []} -- un audit ilegible se veia igual que "no hubo
        # eventos". Un fallo del camino de auditoria no se disfraza de sano.
        raise HTTPException(status_code=503, detail="auditoria_ilegible") from exc
