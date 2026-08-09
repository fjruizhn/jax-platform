import json
import uuid

import httpx
import pytest

from auth.jwt import create_access_token

USER_ID = "test-pipelines-pooling-user"
TENANT_ID = "test-pipelines-pooling-tenant"


def _headers():
    token = create_access_token(USER_ID, TENANT_ID, "operator")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _isolated_pipelines_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("api.pipelines.PIPELINES_DIR", tmp_path)


@pytest.fixture
def owned_pipeline_id(_isolated_pipelines_dir):
    from api.pipelines import _pipeline_owner_file

    pipeline_id = str(uuid.uuid4())
    _pipeline_owner_file(pipeline_id).write_text(
        json.dumps({"tenant_id": TENANT_ID, "user_id": USER_ID})
    )
    return pipeline_id


class _ClientInstantiationCounter:
    """Wraps httpx.AsyncClient.__init__ to count NEW instantiations, without
    touching the already-running shared client created at app startup."""

    def __init__(self):
        self.count = 0
        self._original = httpx.AsyncClient.__init__

    def __enter__(self):
        original = self._original
        counter = self

        def wrapped(self_client, *args, **kwargs):
            counter.count += 1
            return original(self_client, *args, **kwargs)

        httpx.AsyncClient.__init__ = wrapped
        return self

    def __exit__(self, *exc):
        httpx.AsyncClient.__init__ = self._original


def test_pipeline_endpoints_do_not_create_a_new_client_per_request(client, owned_pipeline_id):
    """LAS MANOS may or may not be reachable in the environment this runs
    in (connection refused -> 502, or a real response -> 200) — either way
    this only pins that no NEW httpx.AsyncClient() is instantiated per
    request now that all 6 sites share the app-startup client.

    Uses a real owner file for this test's own identity so the by-id
    requests actually reach the shared client (past the ownership check)
    instead of short-circuiting at a 404."""
    with _ClientInstantiationCounter() as counter:
        resp = client.get("/api/pipelines", headers=_headers())
        assert resp.status_code == 200

        resp = client.get(f"/api/pipelines/{owned_pipeline_id}", headers=_headers())
        assert resp.status_code in (200, 502)

        resp = client.get(f"/api/pipelines/{owned_pipeline_id}/results", headers=_headers())
        assert resp.status_code in (200, 502)

    assert counter.count == 0, (
        f"expected 0 new httpx.AsyncClient() instantiations across 3 pipeline "
        f"requests (shared client created once at app startup), got {counter.count}"
    )


def test_by_id_endpoint_404s_before_touching_the_client_when_not_the_owner(client):
    """A pipeline_id with no matching owner record 404s at the ownership
    check, before get_http_client() is ever called."""
    with _ClientInstantiationCounter() as counter:
        resp = client.get(f"/api/pipelines/{uuid.uuid4()}", headers=_headers())
        assert resp.status_code == 404

    assert counter.count == 0
