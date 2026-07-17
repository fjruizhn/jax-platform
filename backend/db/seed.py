import bcrypt
from .connection import get_pool


def _hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


async def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


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
                    "VALUES (1, 'Inversiones Diamante Negro', 'superadmin', 'active')"
                )

            await cur.execute(
                "SELECT COUNT(*) FROM jax_users WHERE user_id = 1"
            )
            (count,) = await cur.fetchone()
            if count == 0:
                hashed = _hash("***REMOVED-SEE-JAX-RONDA9-2026-08-20***")
                await cur.execute(
                    "INSERT INTO jax_users "
                    "(user_id, tenant_id, email, password_hash, role, status) "
                    "VALUES (1, 1, 'fernando@rich-hn.com', %s, 'superadmin', 'active')",
                    (hashed,),
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
