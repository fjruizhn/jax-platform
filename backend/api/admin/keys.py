import os
import time
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth.middleware import require_superadmin
from auth.models import AuthUser
from crypto_secrets import encrypt_secret, decrypt_secret, decrypt_db_secret
from db.connection import get_pool
from http_client import get_http_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin")

ENV_PATH = "/etc/jax/.env"

PROVIDERS = [
    {"id": "openai",    "name": "OpenAI",    "facet": "thot",   "model": "gpt-4o",           "env_key": "OPENAI_API_KEY",   "test_url": "https://api.openai.com/v1/models"},
    {"id": "deepseek",  "name": "DeepSeek",  "facet": "jekyll", "model": "deepseek-v4-flash", "env_key": "DEEPSEEK_API_KEY", "test_url": "https://api.deepseek.com/v1/models"},
    {"id": "gemini",    "name": "Gemini",    "facet": "hipatia","model": "gemini-2.5-flash",  "env_key": "GEMINI_API_KEY",   "test_url": None},
    {"id": "moonshot",  "name": "Moonshot",  "facet": "kimi",   "model": "kimi-k2.7-code",   "env_key": "KIMI_API_KEY",     "test_url": "https://api.moonshot.ai/v1/models"},
    {"id": "zhipu",     "name": "Z.ai",      "facet": "ada",    "model": "glm-5.2",            "env_key": "ZAI_API_KEY",      "test_url": "https://api.z.ai/api/paas/v4/models"},
]

_PROVIDER_MAP = {p["id"]: p for p in PROVIDERS}


def _load_env() -> dict:
    env = {}
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
    except FileNotFoundError:  # fail-soft: mismo patron que chat.py/image.py: FileNotFoundError acotado a 'no existe .env todavia', no oculta otros errores de lectura
        pass
    return env


def _write_env_key(env_key: str, value: str):
    env = _load_env()
    # En disco siempre cifrado (o vacío al borrar); en memoria queda el
    # valor plano para que el proceso actual siga funcionando sin reinicio.
    env[env_key] = encrypt_secret(value) if value else value
    lines = [f"{k}={v}\n" for k, v in env.items()]
    with open(ENV_PATH, "w") as f:
        f.writelines(lines)
    os.environ[env_key] = value


async def _seed_keys_from_env(pool, user_id: int = 1):
    env = _load_env()
    rows = []
    for p in PROVIDERS:
        raw = env.get(p["env_key"], "")
        if not raw:
            continue
        # raw puede venir cifrado (post-migración) o en texto plano
        # (legacy, aún no migrado) — decrypt_secret soporta ambos.
        encrypted = encrypt_secret(decrypt_secret(raw))
        rows.append((user_id, p["id"], p["env_key"], encrypted))

    if not rows:
        return

    placeholders = ", ".join(["(%s, %s, %s, %s)"] * len(rows))
    params = [v for row in rows for v in row]
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO user_api_keys (user_id, provider_id, env_key, encrypted_value) "
                f"VALUES {placeholders} "
                "ON DUPLICATE KEY UPDATE encrypted_value = VALUES(encrypted_value)",
                params,
            )


async def _get_db_key(pool, user_id: int, provider_id: str) -> str:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT encrypted_value FROM user_api_keys WHERE user_id = %s AND provider_id = %s",
                (user_id, provider_id),
            )
            row = await cur.fetchone()
    if not row:
        return ""
    return decrypt_db_secret(row[0])


async def _get_db_keys_batch(pool, user_id: int, provider_ids: list) -> dict:
    if not provider_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(provider_ids))
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT provider_id, encrypted_value FROM user_api_keys "
                f"WHERE user_id = %s AND provider_id IN ({placeholders})",
                (user_id, *provider_ids),
            )
            rows = await cur.fetchall()
    return {provider_id: decrypt_db_secret(encrypted) for provider_id, encrypted in rows}


async def _get_active_models_batch(pool, facets: list) -> dict:
    if not facets:
        return {}
    placeholders = ", ".join(["%s"] * len(facets))
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT facet, model_name FROM facet_models "
                f"WHERE facet IN ({placeholders}) AND is_active = TRUE",
                tuple(facets),
            )
            rows = await cur.fetchall()
    return {facet: model_name for facet, model_name in rows}


async def _get_binding_models_batch(pool, facets: list) -> dict:
    """Bloque D (D0/D1.5) — fuente real post-Bloque-C: facet_binding/model,
    la MISMA que usan REPL/Mesa web/Jacobs via facet_resolver.py. Cierra el
    bug de admin/keys.py:18 (PROVIDERS['model']='gpt-4o' hardcodeado para
    thot, real gpt-5.5): esta consulta gana sobre facet_models (legacy) y
    sobre el literal, sin tocar ninguna de las dos."""
    if not facets:
        return {}
    placeholders = ", ".join(["%s"] * len(facets))
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT b.facet_key, m.model_id FROM facet_binding b "
                "JOIN model m ON m.id = b.model_ref "
                f"WHERE b.facet_key IN ({placeholders}) AND b.role = 'primary'",
                tuple(facets),
            )
            rows = await cur.fetchall()
    return {facet_key: model_id for facet_key, model_id in rows}


@router.get("/keys")
async def list_keys(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    await _seed_keys_from_env(pool, user_id=1)

    keys_by_provider = await _get_db_keys_batch(pool, user_id=1, provider_ids=[p["id"] for p in PROVIDERS])
    models_by_facet = await _get_active_models_batch(pool, facets=[p["facet"] for p in PROVIDERS])
    binding_models_by_facet = await _get_binding_models_batch(pool, facets=[p["facet"] for p in PROVIDERS])

    result = []
    for p in PROVIDERS:
        raw = keys_by_provider.get(p["id"], "")
        # Orden de preferencia: facet_binding (Bloque C/D, fuente real y
        # actual) > facet_models (legacy) > literal hardcodeado (ultimo
        # recurso, sabido stale — ver D0).
        model = (
            binding_models_by_facet.get(p["facet"])
            or models_by_facet.get(p["facet"])
            or p["model"]
        )
        result.append({
            "id": p["id"],
            "name": p["name"],
            "facet": p["facet"],
            "model": model,
            "key_last4": raw[-4:] if len(raw) >= 4 else ("****" if raw else ""),
            "has_key": bool(raw),
            "status": "active" if raw else "missing",
        })
    return {"providers": result}


@router.post("/keys/{provider_id}/test")
async def test_key(provider_id: str, user: AuthUser = Depends(require_superadmin)):
    prov = _PROVIDER_MAP.get(provider_id)
    if not prov:
        raise HTTPException(status_code=404, detail="Provider no encontrado")

    pool = await get_pool()
    api_key = await _get_db_key(pool, user_id=1, provider_id=provider_id)
    if not api_key:
        return {"ok": False, "latency_ms": None, "error": "API key no configurada"}

    if not prov["test_url"]:
        if provider_id == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            try:
                t0 = time.time()
                client = await get_http_client()
                r = await client.get(url, timeout=10.0)
                ms = int((time.time() - t0) * 1000)
                return {"ok": r.status_code == 200, "latency_ms": ms, "error": None if r.status_code == 200 else r.text[:100]}
            except Exception as e:
                return {"ok": False, "latency_ms": None, "error": str(e)[:100]}
        return {"ok": False, "latency_ms": None, "error": "Test no disponible para este provider"}

    try:
        t0 = time.time()
        client = await get_http_client()
        r = await client.get(prov["test_url"], headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0)
        ms = int((time.time() - t0) * 1000)
        return {"ok": r.status_code < 400, "latency_ms": ms, "error": None if r.status_code < 400 else r.text[:100]}
    except Exception as e:
        return {"ok": False, "latency_ms": None, "error": str(e)[:100]}


class UpdateKeyRequest(BaseModel):
    api_key: str


@router.put("/keys/{provider_id}")
async def update_key(
    provider_id: str,
    req: UpdateKeyRequest,
    user: AuthUser = Depends(require_superadmin),
):
    prov = _PROVIDER_MAP.get(provider_id)
    if not prov:
        raise HTTPException(status_code=404, detail="Provider no encontrado")

    encrypted = encrypt_secret(req.api_key)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO user_api_keys (user_id, provider_id, env_key, encrypted_value) "
                "VALUES (1, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE encrypted_value = VALUES(encrypted_value), updated_at = NOW()",
                (provider_id, prov["env_key"], encrypted),
            )

    _write_env_key(prov["env_key"], req.api_key)
    return {"ok": True}


@router.delete("/keys/{provider_id}")
async def delete_key(provider_id: str, user: AuthUser = Depends(require_superadmin)):
    prov = _PROVIDER_MAP.get(provider_id)
    if not prov:
        raise HTTPException(status_code=404, detail="Provider no encontrado")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM user_api_keys WHERE user_id = 1 AND provider_id = %s",
                (provider_id,),
            )

    _write_env_key(prov["env_key"], "")
    return {"ok": True}
