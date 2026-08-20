"""JAX_SEED_ADMIN_PASSWORD (fix del hardcode "JAX2026!" en db/seed.py, ronda
9 de apertura de repos). Patrón `client.portal.call` de test_motor_migrations.py."""
import os

from db.seed import _resolve_seed_admin_password


def test_resolve_seed_admin_password_uses_env_var(monkeypatch):
    monkeypatch.setenv("JAX_SEED_ADMIN_PASSWORD", "una-fija-de-test")
    assert _resolve_seed_admin_password() == "una-fija-de-test"


def test_resolve_seed_admin_password_generates_and_logs_when_unset(monkeypatch, caplog):
    monkeypatch.delenv("JAX_SEED_ADMIN_PASSWORD", raising=False)
    with caplog.at_level("WARNING", logger="db.seed"):
        generated = _resolve_seed_admin_password()
    assert generated
    assert len(generated) >= 20  # secrets.token_urlsafe(18) -> ~24 chars
    assert generated != "JAX2026!"
    assert any(generated in record.message for record in caplog.records)


def test_resolve_seed_admin_password_never_returns_known_default(monkeypatch):
    monkeypatch.delenv("JAX_SEED_ADMIN_PASSWORD", raising=False)
    seen = {_resolve_seed_admin_password() for _ in range(5)}
    assert "JAX2026!" not in seen
    assert len(seen) == 5  # cada llamada genera una distinta, no un default fijo


async def _delete_user(user_id):
    """Solo el usuario, no el tenant: jax_tenants tiene FKs dependientes en
    otras tablas (pipelines, credenciales, etc.) que romperían con un DELETE
    acá. El branch de contraseña bajo prueba está gateado por
    `COUNT(*) FROM jax_users WHERE user_id = 1`, independiente del tenant.

    `facet_binding.approved_by` referencia user_id=1 (aprobó bindings reales
    en jax_memory_test), así que el DELETE necesita FK_CHECKS=0 -- se
    reactiva antes de soltar la conexión. La ventana sin fila user_id=1 dura
    lo que tarda este test en llamar a run_seed() a continuación (mismo
    proceso, sin paralelismo en esta suite)."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SET FOREIGN_KEY_CHECKS=0")
            await cur.execute("DELETE FROM jax_users WHERE user_id = %s", (user_id,))
            await cur.execute("SET FOREIGN_KEY_CHECKS=1")


async def _fetch_seeded_user(user_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT email, role, password_hash FROM jax_users WHERE user_id = %s",
                (user_id,),
            )
            return await cur.fetchone()


async def _run_seed():
    from db.seed import run_seed
    await run_seed()


async def _verify(plain, hashed):
    from db.seed import verify_password
    return await verify_password(plain, hashed)


def test_run_seed_reseeds_admin_from_env_var_on_empty_db(client, monkeypatch):
    """Simula user_id=1 ausente (COUNT(*) == 0, el gate real de seed.py) y
    verifica que run_seed() lo recrea usando JAX_SEED_ADMIN_PASSWORD, no un
    hardcode."""
    monkeypatch.setenv("JAX_SEED_ADMIN_PASSWORD", "verificacion-seed-vacio-2026")
    client.portal.call(_delete_user, 1)
    try:
        client.portal.call(_run_seed)
        row = client.portal.call(_fetch_seeded_user, 1)
        assert row is not None
        email, role, password_hash = row
        assert email == "fernando@rich-hn.com"
        assert role == "superadmin"
        assert client.portal.call(_verify, "verificacion-seed-vacio-2026", password_hash)
        assert not client.portal.call(_verify, "JAX2026!", password_hash)
    finally:
        os.environ.pop("JAX_SEED_ADMIN_PASSWORD", None)
        monkeypatch.delenv("JAX_SEED_ADMIN_PASSWORD", raising=False)
        # deja la DB de test re-sembrada (estado normal), no huérfana sin admin
        client.portal.call(_run_seed)
