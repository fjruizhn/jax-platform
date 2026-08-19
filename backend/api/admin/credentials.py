"""
Fase 1 — API Keys sobre las tablas nuevas (provider/credential/credential_audit).

api/admin/keys.py (user_api_keys) queda INTACTO — es la red de seguridad
hasta que el corte de doble lectura (B1.4) esté verificado. Este router es
el reemplazo de UI: rotar y revocar como acciones separadas (antes un solo
botón "Guardar" hacía las dos cosas mal), salud persistida en DB (antes
vivía en estado de React y se perdía al recargar).

Ver jax-platform/docs/fase1-credenciales-diseno.md.
"""
import time
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth.middleware import require_superadmin
from auth.models import AuthUser
from crypto_secrets import encrypt_secret
from db.connection import get_pool
from http_client import get_http_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin")

# test_url por provider — mismo dato que api/admin/keys.py:17-23 (PROVIDERS),
# no se unifica en esta fase (fuera de alcance: catalogo de modelos).
_TEST_URLS = {
    "openai":   "https://api.openai.com/v1/models",
    "deepseek": "https://api.deepseek.com/v1/models",
    "moonshot": "https://api.moonshot.ai/v1/models",
    "zhipu":    "https://api.z.ai/api/paas/v4/models",
    "gemini":   None,  # requiere key en query string, caso especial abajo
}

_MAX_ACTIVE_PER_PROVIDER = 2  # solapamiento acotado — nunca ilimitado


async def _audit(cur, credential_id, provider_id, action, user: AuthUser, request: Request, detail=None):
    await cur.execute(
        "INSERT INTO credential_audit (credential_id, provider_id, action, performed_by, performed_from_ip, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (credential_id, provider_id, action, int(user.user_id), request.client.host if request.client else "unknown", detail),
    )


@router.get("/credentials")
async def list_credentials(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, display_name, auth_type, is_local FROM provider ORDER BY id"
            )
            providers = await cur.fetchall()
            await cur.execute(
                "SELECT id, provider_id, state, created_at, activated_at, revoked_at, "
                "last_verified_at, last_health_status, last_health_detail "
                "FROM credential ORDER BY provider_id, activated_at DESC"
            )
            creds = await cur.fetchall()

    by_provider: dict = {}
    for row in creds:
        by_provider.setdefault(row[1], []).append({
            "id": row[0], "state": row[2], "created_at": str(row[3]),
            "activated_at": str(row[4]) if row[4] else None,
            "revoked_at": str(row[5]) if row[5] else None,
            "last_verified_at": str(row[6]) if row[6] else None,
            "last_health_status": row[7], "last_health_detail": row[8],
        })

    result = []
    for pid, display_name, auth_type, is_local in providers:
        result.append({
            "id": pid, "display_name": display_name, "auth_type": auth_type,
            "is_local": bool(is_local), "credentials": by_provider.get(pid, []),
        })
    return {"providers": result}


class RotateRequest(BaseModel):
    api_key: str


@router.post("/credentials/{provider_id}/rotate")
async def rotate_credential(
    provider_id: str, req: RotateRequest, request: Request,
    user: AuthUser = Depends(require_superadmin),
):
    """Rotar = agregar una credencial nueva activa SIN tocar la anterior
    (solapamiento con gracia). Nunca sobreescribe — eso es lo que rompia
    hoy: un solo boton hacia rotar y revocar mal a la vez."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM provider WHERE id=%s", (provider_id,))
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="Provider no encontrado")

            await cur.execute(
                "SELECT COUNT(*) FROM credential WHERE provider_id=%s AND state='active'",
                (provider_id,),
            )
            (active_count,) = await cur.fetchone()
            if active_count >= _MAX_ACTIVE_PER_PROVIDER:
                raise HTTPException(
                    status_code=409,
                    detail=f"Ya hay {active_count} credenciales activas (máximo {_MAX_ACTIVE_PER_PROVIDER}) — revocá una antes de rotar de nuevo",
                )

            encrypted = encrypt_secret(req.api_key)
            env_key_map = {
                "openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
                "gemini": "GEMINI_API_KEY", "moonshot": "KIMI_API_KEY", "zhipu": "ZAI_API_KEY",
            }
            await cur.execute(
                "INSERT INTO credential (provider_id, env_key, encrypted_value, state, activated_at, created_by) "
                "VALUES (%s, %s, %s, 'active', NOW(), %s)",
                (provider_id, env_key_map.get(provider_id, ""), encrypted, int(user.user_id)),
            )
            new_id = cur.lastrowid
            await _audit(cur, new_id, provider_id, "rotate", user, request)
        await conn.commit()
    return {"ok": True, "credential_id": new_id}


@router.post("/credentials/{provider_id}/revoke")
async def revoke_credential(
    provider_id: str, request: Request,
    user: AuthUser = Depends(require_superadmin),
):
    """Revocar = corte inmediato, sin gracia. Distinto de rotar: esto
    invalida TODAS las credenciales activas de este provider ya mismo."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM credential WHERE provider_id=%s AND state='active'",
                (provider_id,),
            )
            active_ids = [r[0] for r in await cur.fetchall()]
            if not active_ids:
                raise HTTPException(status_code=404, detail="No hay credenciales activas para revocar")

            await cur.execute(
                "UPDATE credential SET state='revoked', revoked_at=NOW() "
                "WHERE provider_id=%s AND state='active'",
                (provider_id,),
            )
            for cred_id in active_ids:
                await _audit(cur, cred_id, provider_id, "revoke", user, request)
        await conn.commit()
    return {"ok": True, "revoked_count": len(active_ids)}


@router.post("/credentials/{provider_id}/test")
async def test_credential(
    provider_id: str, request: Request,
    user: AuthUser = Depends(require_superadmin),
):
    """Health check real, PERSISTIDO en la credencial activa mas reciente
    (last_verified_at/last_health_status/last_health_detail) — antes vivia
    solo en estado de React y se perdia al recargar la pagina."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, encrypted_value FROM credential "
                "WHERE provider_id=%s AND state='active' ORDER BY activated_at DESC LIMIT 1",
                (provider_id,),
            )
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No hay credencial activa para este provider")
            cred_id, encrypted = row

    from crypto_secrets import decrypt_db_secret
    api_key = decrypt_db_secret(encrypted)

    ok, latency_ms, error = False, None, None
    try:
        t0 = time.time()
        client = await get_http_client()
        if provider_id == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            r = await client.get(url, timeout=10.0)
        else:
            test_url = _TEST_URLS.get(provider_id)
            if not test_url:
                raise HTTPException(status_code=400, detail="Test no disponible para este provider")
            r = await client.get(test_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0)
        latency_ms = int((time.time() - t0) * 1000)
        ok = r.status_code < 400
        error = None if ok else r.text[:200]
    except HTTPException:
        raise
    except Exception as e:
        error = str(e)[:200]

    status = "ok" if ok else "failed"
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE credential SET last_verified_at=NOW(), last_health_status=%s, last_health_detail=%s "
                "WHERE id=%s",
                (status, error, cred_id),
            )
            await _audit(cur, cred_id, provider_id, "test", user, request, detail=status)
        await conn.commit()

    return {"ok": ok, "latency_ms": latency_ms, "error": error}
