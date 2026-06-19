import os
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth.middleware import require_superadmin
from auth.models import AuthUser

router = APIRouter(prefix="/api/admin")

ENV_PATH = "/etc/jax/.env"

PROVIDERS = [
    {"id": "openai",    "name": "OpenAI",    "facet": "thot",   "model": "gpt-4o",            "env_key": "OPENAI_API_KEY",   "test_url": "https://api.openai.com/v1/models"},
    {"id": "deepseek",  "name": "DeepSeek",  "facet": "jekyll", "model": "deepseek-v4-flash",  "env_key": "DEEPSEEK_API_KEY", "test_url": "https://api.deepseek.com/v1/models"},
    {"id": "gemini",    "name": "Gemini",    "facet": "hipatia","model": "gemini-2.5-flash",   "env_key": "GEMINI_API_KEY",   "test_url": None},
    {"id": "moonshot",  "name": "Moonshot",  "facet": "kimi",   "model": "kimi-k2.7-code",    "env_key": "KIMI_API_KEY",     "test_url": "https://api.moonshot.ai/v1/models"},
    {"id": "zhipu",     "name": "Z.ai",      "facet": "ada",    "model": "glm-4-flash",        "env_key": "ZHIPU_API_KEY",    "test_url": None},
]


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


def _write_env(env: dict):
    lines = []
    for k, v in env.items():
        lines.append(f"{k}={v}\n")
    with open(ENV_PATH, "w") as f:
        f.writelines(lines)


@router.get("/keys")
async def list_keys(user: AuthUser = Depends(require_superadmin)):
    env = _load_env()
    result = []
    for p in PROVIDERS:
        key_val = env.get(p["env_key"], "")
        key_last4 = key_val[-4:] if len(key_val) >= 4 else ("****" if key_val else "")
        result.append({
            "id": p["id"],
            "name": p["name"],
            "facet": p["facet"],
            "model": p["model"],
            "key_last4": key_last4,
            "has_key": bool(key_val),
            "status": "active" if key_val else "missing",
        })
    return {"providers": result}


@router.post("/keys/{provider_id}/test")
async def test_key(provider_id: str, user: AuthUser = Depends(require_superadmin)):
    prov = next((p for p in PROVIDERS if p["id"] == provider_id), None)
    if not prov:
        raise HTTPException(status_code=404, detail="Provider no encontrado")

    env = _load_env()
    api_key = env.get(prov["env_key"], "")
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
    prov = next((p for p in PROVIDERS if p["id"] == provider_id), None)
    if not prov:
        raise HTTPException(status_code=404, detail="Provider no encontrado")

    env = _load_env()
    env[prov["env_key"]] = req.api_key
    _write_env(env)
    os.environ[prov["env_key"]] = req.api_key
    return {"ok": True}
