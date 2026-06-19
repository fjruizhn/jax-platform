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
                hashed = _hash("JAX2026!")
                await cur.execute(
                    "INSERT INTO jax_users "
                    "(user_id, tenant_id, email, password_hash, role, status) "
                    "VALUES (1, 1, 'fernando@rich-hn.com', %s, 'superadmin', 'active')",
                    (hashed,),
                )
