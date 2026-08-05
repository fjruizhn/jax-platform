import httpx

from auth.jwt import create_access_token

USER_ID = "test-dashboard-pooling-user"
TENANT_ID = "test-dashboard-pooling-tenant"


def _superadmin_headers():
    token = create_access_token(USER_ID, TENANT_ID, "superadmin")
    return {"Authorization": f"Bearer {token}"}


class _ClientInstantiationCounter:
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


def test_dashboard_health_checks_do_not_create_new_clients(client):
    """get_dashboard() calls _check_http() twice per request (LAS MANOS +
    JAX Engine health). Both services are unreachable in the test env, so
    this only pins zero new httpx.AsyncClient() instantiations."""
    with _ClientInstantiationCounter() as counter:
        resp = client.get("/api/admin/dashboard", headers=_superadmin_headers())
        assert resp.status_code == 200

    assert counter.count == 0, (
        f"expected 0 new httpx.AsyncClient() instantiations for 2 internal "
        f"health checks per dashboard request, got {counter.count}"
    )
