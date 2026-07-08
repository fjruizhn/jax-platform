import os
import time
import logging
import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

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


def _get_fernet() -> Fernet:
    key = os.getenv("FERNET_KEY", "")
    if not key:
        raise RuntimeError("FERNET_KEY no configurada en /etc/jax/.env")
    return Fernet(key.encode())


def _encrypt(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def _decrypt(token: str) -> str:
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except (InvalidToken, Exception):
        return ""


def _load_env() -> dict:
    env = {}
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


def _write_env_key(env_key: str, value: str):
    env = _load_env()
    env[env_key] = value
    lines = [f"{k}={v}\n" for k, v in env.items()]
    with open(ENV_PATH, "w") as f:
        f.writelines(lines)
    os.environ[env_key] = value


async def _seed_keys_from_env(pool, user_id: int = 1):
    env = _load_env()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for p in PROVIDERS:
                raw = env.get(p["env_key"], "")
                if not raw:
                    continue
                encrypted = _encrypt(raw)
                await cur.execute(
                    "INSERT INTO user_api_keys (user_id, provider_id, env_key, encrypted_value) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE encrypted_value = VALUES(encrypted_value)",
                    (user_id, p["id"], p["env_key"], encrypted),
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
    return _decrypt(row[0])


@router.get("/keys")
async def list_keys(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    await _seed_keys_from_env(pool, user_id=1)

    result = []
    for p in PROVIDERS:
        raw = await _get_db_key(pool, user_id=1, provider_id=p["id"])
        result.append({
            "id": p["id"],
            "name": p["name"],
            "facet": p["facet"],
            "model": p["model"],
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
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(url)
                ms = int((time.time() - t0) * 1000)
                return {"ok": r.status_code == 200, "latency_ms": ms, "error": None if r.status_code == 200 else r.text[:100]}
            except Exception as e:
                return {"ok": False, "latency_ms": None, "error": str(e)[:100]}
        return {"ok": False, "latency_ms": None, "error": "Test no disponible para este provider"}

    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(prov["test_url"], headers={"Authorization": f"Bearer {api_key}"})
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

    encrypted = _encrypt(req.api_key)
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
