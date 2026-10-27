"""Require an explicit, separate MariaDB target for Office processes."""
from __future__ import annotations

import os


async def assert_dedicated_server(pool) -> None:
    """Check the connected server, not merely its configured address."""
    require_dedicated_db()
    expected_id = os.getenv("OFFICE_DB_SERVER_ID")
    expected_hostname = os.getenv("OFFICE_DB_HOSTNAME")
    if not expected_id or not expected_hostname or not expected_id.isdecimal():
        raise RuntimeError("dedicated Office database identity is required")
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT @@server_id,@@hostname,DATABASE()")
            actual_id, actual_hostname, actual_schema = await cur.fetchone()
    if (int(actual_id) != int(expected_id) or actual_hostname != expected_hostname
            or actual_schema != os.environ["JAX_DB_NAME"]):
        raise RuntimeError("connected MariaDB is not the dedicated Office server")


def require_dedicated_db() -> None:
    required = ("JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_NAME", "JAX_DB_USER", "JAX_DB_PASSWORD")
    if (os.getenv("OFFICE_DEDICATED_DB") != "1"
            or any(not os.getenv(key) for key in required)):
        raise RuntimeError("dedicated Office database configuration is required")
    # Current Hall9000 JAX production target, measured on 2026-10-27.  This
    # also catches accidentally loading /etc/jax/runtime.env in the Office
    # service, where a migration would otherwise mutate the shared schema.
    if (os.environ["JAX_DB_NAME"] == "jax_memory"
            or (os.environ["JAX_DB_HOST"] == "127.0.0.1"
                and os.environ["JAX_DB_PORT"] == "3308")):
        raise RuntimeError("Office database still points to JAX production")

