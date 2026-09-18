import time
import logging
from fastapi import APIRouter, Depends, HTTPException
from auth.middleware import require_superadmin
from auth.models import AuthUser
from crypto_secrets import encrypt_secret, decrypt_secret, decrypt_db_secret
from db.connection import get_pool
from http_client import cabeceras_gemini, get_http_client
from redaccion import redactar_secretos

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin")

ENV_PATH = "/etc/jax/.env"
# SOLO LECTURA (B1.4, 2026-09-17). Este modulo escribia el archivo con un
# volcado del diccionario parseado: perdia comentarios y estructura, y
# reintroducia las *_API_KEY que B1.4 retira. Las llaves vivas se rotan y
# revocan por /api/admin/credentials, contra la tabla `credential`.

# Sin modelo (PR-L ronda 2, 2026-09-14): el modelo de cada faceta sale SOLO de
# facet_binding (_get_binding_models_batch). Los literales que vivían acá
# (gpt-4o, kimi-k2.7-code, glm-5.2...) eran el último recurso de /keys, ya
# desfasados con la semilla; sin binding la respuesta trae model=None.
PROVIDERS = [
    {"id": "openai",    "name": "OpenAI",    "facet": "thot",   "env_key": "OPENAI_API_KEY",   "test_url": "https://api.openai.com/v1/models"},
    {"id": "deepseek",  "name": "DeepSeek",  "facet": "jekyll", "env_key": "DEEPSEEK_API_KEY", "test_url": "https://api.deepseek.com/v1/models"},
    {"id": "gemini",    "name": "Gemini",    "facet": "hipatia","env_key": "GEMINI_API_KEY",   "test_url": None},
    {"id": "moonshot",  "name": "Moonshot",  "facet": "kimi",   "env_key": "KIMI_API_KEY",     "test_url": "https://api.moonshot.ai/v1/models"},
    {"id": "zhipu",     "name": "Z.ai",      "facet": "ada",    "env_key": "ZAI_API_KEY",      "test_url": "https://api.z.ai/api/paas/v4/models"},
]

_PROVIDER_MAP = {p["id"]: p for p in PROVIDERS}

# El almacén legado `user_api_keys` guarda las llaves de proveedor a nombre
# del superadmin sembrado (id 1, ver db/seed.py), no del que hace el
# pedido: es la "red de seguridad" de credentials.py:4 (A-46, 2026-09-16).
USUARIO_LLAVES_LEGADO = 1


def _load_env() -> dict:
    env = {}
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
    except (FileNotFoundError, PermissionError):  # fail-soft: 'no existe todavia' o 'no es mio' (desde 2026-09-17 el .env es root:jaxsvc 640 y solo lo lee el servicio). En los dos casos no hay llaves que sembrar: la base es la unica fuente desde B1.4
        pass
    return env


async def _seed_keys_from_env(pool, user_id: int = USUARIO_LLAVES_LEGADO):
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


async def _get_binding_models_batch(pool, facets: list) -> dict:
    """Bloque D (D0/D1.5) — fuente real post-Bloque-C: facet_binding/model,
    la MISMA que usan REPL/Mesa web/Jacobs via facet_resolver.py. Cierra el
    bug de admin/keys.py:18 (PROVIDERS['model']='gpt-4o' hardcodeado para
    thot, real gpt-5.5). Desde PR-L ronda 2 es la UNICA fuente: el fallback
    a facet_models (legacy) y al literal se eliminaron."""
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
    await _seed_keys_from_env(pool, user_id=USUARIO_LLAVES_LEGADO)

    keys_by_provider = await _get_db_keys_batch(
        pool, user_id=USUARIO_LLAVES_LEGADO, provider_ids=[p["id"] for p in PROVIDERS])
    binding_models_by_facet = await _get_binding_models_batch(pool, facets=[p["facet"] for p in PROVIDERS])

    result = []
    for p in PROVIDERS:
        raw = keys_by_provider.get(p["id"], "")
        # SOLO facet_binding (PR-L ronda 2): ni facet_models (legacy, ya no
        # la lee ningún dispatch) ni un literal. Sin binding, None: "sin dato"
        # es la verdad, un nombre de modelo inventado no.
        model = binding_models_by_facet.get(p["facet"])
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
    api_key = await _get_db_key(pool, user_id=USUARIO_LLAVES_LEGADO, provider_id=provider_id)
    if not api_key:
        return {"ok": False, "latency_ms": None, "error": "API key no configurada"}

    if not prov["test_url"]:
        if provider_id == "gemini":
            # T6-2: la key va en la cabecera x-goog-api-key, nunca en la URL.
            url = "https://generativelanguage.googleapis.com/v1beta/models"
            try:
                t0 = time.time()
                client = await get_http_client()
                r = await client.get(url, headers=cabeceras_gemini(api_key), timeout=10.0)
                ms = int((time.time() - t0) * 1000)
                return {"ok": r.status_code == 200, "latency_ms": ms,
                        "error": None if r.status_code == 200 else redactar_secretos(r.text, [api_key])[:100]}
            except Exception as e:  # fail-soft: el fallo del test ES el resultado (ok=False con error) que se le muestra al superadmin
                # Task 6 S1: defensa en profundidad -- se redacta antes de devolver.
                return {"ok": False, "latency_ms": None, "error": redactar_secretos(str(e), [api_key])[:100]}
        return {"ok": False, "latency_ms": None, "error": "Test no disponible para este provider"}

    try:
        t0 = time.time()
        client = await get_http_client()
        r = await client.get(prov["test_url"], headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0)
        ms = int((time.time() - t0) * 1000)
        return {"ok": r.status_code < 400, "latency_ms": ms,
                "error": None if r.status_code < 400 else redactar_secretos(r.text, [api_key])[:100]}
    except Exception as e:  # fail-soft: el fallo del test ES el resultado (ok=False con error) que se le muestra al superadmin
        return {"ok": False, "latency_ms": None, "error": redactar_secretos(str(e), [api_key])[:100]}
