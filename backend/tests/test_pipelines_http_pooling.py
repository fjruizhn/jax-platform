import httpx

from auth.jwt import create_access_token

USER_ID = "test-pipelines-pooling-user"
TENANT_ID = "test-pipelines-pooling-tenant"


def _headers():
    token = create_access_token(USER_ID, TENANT_ID, "operator")
    return {"Authorization": f"Bearer {token}"}


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


def test_pipeline_endpoints_do_not_create_a_new_client_per_request(client):
    """LAS MANOS is not running in the test environment, so every call
    degrades gracefully (connection refused -> caught Exception branch) —
    this only pins that no NEW httpx.AsyncClient() is instantiated per
    request now that all 6 sites share the app-startup client.

    "fake-id" has no ownership record, so the by-id endpoints now 404
    before ever reaching the shared client (see the pipeline-ownership
    check) — still 0 new client instantiations, just earlier."""
    with _ClientInstantiationCounter() as counter:
        resp = client.get("/api/pipelines", headers=_headers())
        assert resp.status_code == 200

        resp = client.get("/api/pipelines/fake-id", headers=_headers())
        assert resp.status_code == 404

        resp = client.get("/api/pipelines/fake-id/results", headers=_headers())
        assert resp.status_code == 404

    assert counter.count == 0, (
        f"expected 0 new httpx.AsyncClient() instantiations across 3 pipeline "
        f"requests (shared client created once at app startup), got {counter.count}"
    )
