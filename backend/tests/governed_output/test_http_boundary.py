"""Actual FastAPI HTTP boundary coverage for F2-E structured output."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from auth.models import AuthUser


def _paired_root() -> Path:
    return Path(__file__).resolve().parents[4] / "f2e-general-jax"


def test_registered_http_route_replaces_fastapi_serialization_with_exact_canonical_bytes(monkeypatch):
    """Removing the boundary must expose FastAPI's non-canonical JSON encoding."""
    root = _paired_root()
    if not root.is_dir():
        pytest.skip("paired F2-E JAX worktree is not available")
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from governed_output import http
    monkeypatch.setattr(http, "commit_structured_wire", _commit_memory)

    app = FastAPI()

    async def principal():
        return AuthUser(user_id="7", tenant_id="3", role="viewer")

    @app.get("/dynamic")
    async def dynamic(user: AuthUser = Depends(principal)):
        # Deliberately reverse the keys: FastAPI normally preserves this order,
        # while the governed renderer emits immutable canonical JSON.
        return {"z": "tool", "a": [True, 2]}

    http.install_governed_http_boundary(app, contracts=(
        http.HTTPRouteContract("test-dynamic.v1", "GET", "/dynamic", http.HTTPOutputClassification.GOVERNED_TOOL_DATA),
    ), repository_factory=lambda: _MemoryRepository())
    response = TestClient(app).get("/dynamic")

    assert response.status_code == 200
    assert response.content == b'{"a":[true,2],"z":"tool"}'
    assert response.headers["content-type"].startswith("application/json")
    assert _MemoryRepository.prepared[-1] == response.content
    assert _MemoryRepository.committed[-1] == response.content


def test_unknown_route_is_not_silently_given_a_runtime_contract(monkeypatch):
    """A future endpoint must fail closed until the server table names it."""
    root = _paired_root()
    if not root.is_dir():
        pytest.skip("paired F2-E JAX worktree is not available")
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from governed_output import http
    monkeypatch.setattr(http, "commit_structured_wire", _commit_memory)

    app = FastAPI()

    @app.get("/not-registered")
    async def unknown():
        return {"raw": "must-not-leak"}

    http.install_governed_http_boundary(app, contracts=(), repository_factory=lambda: _MemoryRepository())
    response = TestClient(app).get("/not-registered")

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "OUTPUT_LIFECYCLE_UNAVAILABLE"}}
    assert b"must-not-leak" not in response.content


def test_native_json_response_keeps_server_cookie_and_crosses_the_structured_boundary(monkeypatch):
    """Removing JSONResponse handling would turn this native auth output into 503."""
    root = _paired_root()
    if not root.is_dir():
        pytest.skip("paired F2-E JAX worktree is not available")
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from governed_output import http
    monkeypatch.setattr(http, "commit_structured_wire", _commit_memory)

    app = FastAPI()

    @app.post("/login")
    async def login():
        response = JSONResponse({"access_token": "opaque", "token_type": "bearer"})
        response.set_cookie("refresh_token", "server-issued", httponly=True)
        response.headers["X-Axioma-Governed-Output"] = "producer-spoof"
        return response

    http.install_governed_http_boundary(app, contracts=(
        http.HTTPRouteContract("test-login.v1", "POST", "/login", http.HTTPOutputClassification.GOVERNED_TOOL_DATA),
    ), repository_factory=lambda: _MemoryRepository())
    response = TestClient(app).post("/login")

    assert response.status_code == 200
    assert response.content == b'{"access_token":"opaque","token_type":"bearer"}'
    assert "refresh_token=server-issued" in response.headers["set-cookie"]
    assert response.headers["x-axioma-governed-output"] == "f2-e.output.1"
    assert response.headers["x-axioma-body-sha256"] == "sha256:" + hashlib.sha256(response.content).hexdigest()
    assert response.headers["x-axioma-provenance-profile"] == "test-login.v1.layout.1"
    assert response.headers["cache-control"] == "no-transform"


def test_health_contract_uses_current_engine_status_claim_before_rendering(monkeypatch):
    """Changing the accredited probe state makes the stale DTO fail closed."""
    root = _paired_root()
    if not root.is_dir():
        pytest.skip("paired F2-E JAX worktree is not available")
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from datetime import datetime, timezone
    from governed_output import http
    from governed_output.composition import runtime_output_composer
    from governed_output.registry import PLATFORM_HTTP_ROUTE_CONTRACTS
    from jax_engine import status_resolution
    from jax_engine.state import JAXEngineState

    state = JAXEngineState()
    state._commit_las_manos_health_observation(True, datetime.now(timezone.utc))
    monkeypatch.setattr(status_resolution, "engine_state", state)
    runtime_output_composer.cache_clear()
    monkeypatch.setattr(http, "commit_structured_wire", _commit_memory)
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"service": "JAX Platform", "status": "alive", "las_manos": "alive"}

    http.install_governed_http_boundary(app, contracts=PLATFORM_HTTP_ROUTE_CONTRACTS,
                                        repository_factory=lambda: _MemoryRepository())
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["las_manos"] == "alive"


def test_registered_result_number_pattern_preserves_decimal_strings(monkeypatch):
    """A global numeric contract would coerce cost_usd or reject the array float."""
    root = _paired_root()
    if not root.is_dir():
        pytest.skip("paired F2-E JAX worktree is not available")
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from governed_output.http import prepare_http_output
    from governed_output.registry import PLATFORM_HTTP_ROUTE_CONTRACTS

    contract = next(item for item in PLATFORM_HTTP_ROUTE_CONTRACTS
                    if item.route_id == "platform.get.api.pipelines.pipeline_id.results.v1")
    prepared = asyncio.run(prepare_http_output(
        contract=contract,
        value={"duration_seconds": 2.5, "steps": [{"duration_seconds": 1.5}, {"duration_seconds": None}], "cost_usd": "1.50"},
        user=AuthUser(user_id="1", tenant_id="1", role="viewer"), request=None,
    ))

    assert prepared.canonical_bytes == b'{"cost_usd":"1.50","duration_seconds":2.5,"steps":[{"duration_seconds":1.5},{"duration_seconds":null}]}'


def test_image_contract_keeps_large_native_data_uri_without_decoding(monkeypatch):
    """A JSON-looking image leaf must remain typed tool data, not be reparsed."""
    root = _paired_root()
    if not root.is_dir():
        pytest.skip("paired F2-E JAX worktree is not available")
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from governed_output.http import prepare_http_output
    from governed_output.registry import PLATFORM_HTTP_ROUTE_CONTRACTS

    contract = next(item for item in PLATFORM_HTTP_ROUTE_CONTRACTS if item.route_id == "platform-image-generate.v1")
    data_uri = "data:image/png;base64," + "A" * 120_000
    prepared = asyncio.run(prepare_http_output(
        contract=contract, value={"url": data_uri, "revised_prompt": "imagen revisada"},
        user=AuthUser(user_id="1", tenant_id="1", role="viewer"), request=None,
    ))

    assert data_uri.encode() in prepared.canonical_bytes
    assert len(prepared.canonical_bytes) > 120_000


class _MemoryAuthorization:
    def __init__(self, payload: bytes):
        self.payload = payload


class _MemoryRepository:
    """The real adapter and ASGI boundary remain exercised; only MariaDB is external."""
    prepared: list[bytes] = []
    committed: list[bytes] = []

    async def prepare(self, unit):
        self.prepared.append(unit.wire_bytes)
        return _MemoryAuthorization(unit.wire_bytes)


async def _commit_memory(*, authorization, repository, send_wire):
    await send_wire(authorization.payload)
    repository.committed.append(authorization.payload)
