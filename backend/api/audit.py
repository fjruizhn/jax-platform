import json
from pathlib import Path

from fastapi import APIRouter, Depends

from auth.middleware import get_current_user
from auth.models import AuthUser

router = APIRouter(prefix="/api")

AUDIT_LOG = Path.home() / "jax" / "las_manos" / "logs" / "audit.jsonl"


@router.get("/audit")
async def get_audit(user: AuthUser = Depends(get_current_user)):
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
            except json.JSONDecodeError:
                pass
        return {"events": events}
    except Exception:
        return {"events": []}
