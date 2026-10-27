"""The dedicated Office listener has no classic JAX HTTP surface."""
import asyncio

from fastapi.testclient import TestClient
import pytest

from office_main import app, lifespan
from virtual_office.db_identity import require_dedicated_db


def test_office_process_exposes_only_bounded_browser_routes():
    browser = TestClient(app)
    for method, path in (
        ("GET", "/api/admin/users"),
        ("POST", "/api/auth/mfa/root-reset"),
        ("GET", "/api/projects"),
        ("GET", "/openapi.json"),
        ("GET", "/ws"),
    ):
        assert browser.request(method, path).status_code == 404
    response = browser.get("/api/virtual-office/session")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    smtp = browser.get("/api/admin/smtp")
    assert smtp.status_code == 401
    assert smtp.headers["cache-control"] == "no-store"


def test_office_process_rejects_missing_dedicated_database_marker(monkeypatch):
    monkeypatch.delenv("OFFICE_DEDICATED_DB", raising=False)

    async def startup():
        async with lifespan(app):
            pass

    with pytest.raises(RuntimeError, match="dedicated Office database"):
        asyncio.run(startup())


def test_office_process_rejects_the_measured_jax_production_target(monkeypatch):
    for key, value in {
        "OFFICE_DEDICATED_DB": "1", "JAX_DB_HOST": "127.0.0.1",
        "JAX_DB_PORT": "3308", "JAX_DB_NAME": "jax_memory",
        "JAX_DB_USER": "office", "JAX_DB_PASSWORD": "fixture-only",
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="JAX production"):
        require_dedicated_db()
