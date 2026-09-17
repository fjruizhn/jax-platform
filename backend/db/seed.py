import asyncio
import logging
import os
import secrets

import bcrypt
from .connection import get_pool

logger = logging.getLogger("db.seed")


# bcrypt usa solo los primeros 72 bytes de la contraseña. Hasta bcrypt 4 el
# resto se ignoraba en silencio; bcrypt 5 (el instalado, medido 2026-09-12)
# LANZA ValueError -- login y reset-password respondían 500.
BCRYPT_MAX_BYTES = 72


def _hash(plain: str) -> str:
    datos = plain.encode()
    if len(datos) > BCRYPT_MAX_BYTES:
        raise ValueError(f"contraseña de {len(datos)} bytes: bcrypt admite hasta {BCRYPT_MAX_BYTES}")
    return bcrypt.hashpw(datos, bcrypt.gensalt()).decode()


async def verify_password(plain: str, hashed: str) -> bool:
    # bcrypt de costo 12 son ~150 ms de CPU (medido 2026-09-12): en el hilo
    # del event loop congela todos los requests mientras dura. Desde que el
    # login verifica también los emails inexistentes (contra un hash de
    # relleno), cada intento lo paga -- va a un hilo.
    # Se trunca a 72 bytes: es exactamente lo que hacía el bcrypt viejo, así
    # que un hash hecho con él a partir de una contraseña larga sigue
    # verificando, y una contraseña larga no tumba el login.
    return await asyncio.to_thread(bcrypt.checkpw, plain.encode()[:BCRYPT_MAX_BYTES], hashed.encode())


def email_de_semilla() -> str:
    """A-54 (2026-09-16): el superadmin sembrado sale del entorno. Solo se
    pide cuando hay que sembrar (user_id=1 no existe)."""
    valor = os.environ.get("JAX_SEED_SUPERADMIN_EMAIL", "").strip()
    if not valor:
        raise RuntimeError("JAX_SEED_SUPERADMIN_EMAIL no configurada: hace falta para sembrar user_id=1")
    return valor


def tenant_de_semilla() -> str:
    valor = os.environ.get("JAX_SEED_TENANT_NAME", "").strip()
    if not valor:
        raise RuntimeError("JAX_SEED_TENANT_NAME no configurada: hace falta para sembrar tenant_id=1")
    return valor


def _resolve_seed_admin_password() -> str:
    """JAX_SEED_ADMIN_PASSWORD (mismo prefijo JAX_ que el resto de config de
    infra, ver /etc/jax/.env) fija la contraseña del seed. Sin ella, se
    genera una aleatoria y se loguea UNA sola vez al arranque -- nunca a un
    archivo -- para que quien siembre la DB la capture del log. Nunca cae a
    un default fijo: eso era exactamente el bug (contraseña commiteada) que
    esto reemplaza."""
    env_value = os.getenv("JAX_SEED_ADMIN_PASSWORD")
    if env_value:
        return env_value
    generated = secrets.token_urlsafe(18)
    logger.warning(
        "db.seed: JAX_SEED_ADMIN_PASSWORD no seteada -- generada contraseña "
        f"aleatoria para user_id=1: {generated} "
        "-- anotarla ahora, no se vuelve a mostrar"
    )
    return generated


async def run_seed():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM jax_tenants WHERE tenant_id = 1"
            )
            (count,) = await cur.fetchone()
            if count == 0:
                await cur.execute(
                    "INSERT INTO jax_tenants (tenant_id, name, plan, status) "
                    "VALUES (1, %s, 'superadmin', 'active')",
                    (tenant_de_semilla(),),
                )

            await cur.execute(
                "SELECT COUNT(*) FROM jax_users WHERE user_id = 1"
            )
            (count,) = await cur.fetchone()
            if count == 0:
                hashed = _hash(_resolve_seed_admin_password())
                await cur.execute(
                    "INSERT INTO jax_users "
                    "(user_id, tenant_id, email, password_hash, role, status) "
                    "VALUES (1, 1, %s, %s, 'superadmin', 'active')",
                    (email_de_semilla(), hashed),
                )

            await cur.execute("SELECT COUNT(*) FROM facet_models")
            (count,) = await cur.fetchone()
            if count == 0:
                await cur.execute(
                    "INSERT INTO facet_models "
                    "(facet, provider_id, model_name, is_active) VALUES "
                    "('thot', 'openai', 'gpt-5.5', TRUE), "
                    "('thot', 'openai', 'gpt-5.6-sol', FALSE), "
                    "('thot', 'openai', 'gpt-5.6-terra', FALSE), "
                    "('thot', 'openai', 'gpt-5.6-luna', FALSE), "
                    "('jekyll', 'deepseek', 'deepseek-v4-flash', TRUE), "
                    "('jekyll', 'deepseek', 'deepseek-v4-pro', FALSE), "
                    "('hipatia', 'gemini', 'gemini-2.5-flash', TRUE), "
                    "('hipatia', 'gemini', 'gemini-3.1-pro', FALSE), "
                    "('hipatia', 'gemini', 'gemini-3.5-flash', FALSE), "
                    "('kimi', 'moonshot', 'kimi-k2.7-code', TRUE), "
                    "('ada', 'zhipu', 'glm-5.2', TRUE), "
                    "('jax_local', 'ollama', 'qwen3:14b', TRUE), "
                    "('jax_local', 'ollama', 'qwen3-coder:30b', FALSE), "
                    "('jax_local', 'ollama', 'qwen2.5:7b', FALSE), "
                    "('jax_local', 'ollama', 'llama3.2:3b', FALSE), "
                    "('hyde', 'anthropic', 'sonnet', TRUE), "
                    "('hyde', 'anthropic', 'opus', FALSE), "
                    "('hyde', 'anthropic', 'haiku', FALSE)"
                )
