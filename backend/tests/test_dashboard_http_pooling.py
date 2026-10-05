from tests.identidades import cabeceras
import httpx

import http_client


TENANT_ID = "1"  # DB-backed tenant of the authenticated fixture user.


def _superadmin_headers(client):
    return cabeceras(client, "dashboard-pooling", "superadmin", TENANT_ID)


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
    """get_dashboard() probes LAS MANOS (/health) and, if JAX_PLATFORM_URL is
    set, JAX Engine (/api/health). Both are unreachable in the test env, so
    this only pins zero new httpx.AsyncClient() instantiations."""
    # El cliente COMPARTIDO nace perezoso en la primera llamada. Hasta 2026-10-04 lo
    # creaba de antemano _poll_las_manos de fondo; desde que las tareas de fondo no
    # arrancan bajo pytest (PR #193) se calienta aqui DIRECTAMENTE -- no con una
    # peticion al tablero, que tambien calentaria un cliente privado del tablero y
    # lo dejaria escapar del contador (MINOR-1 de la auditoria del #193).
    client.portal.call(http_client.get_http_client)
    with _ClientInstantiationCounter() as counter:
        resp = client.get("/api/admin/dashboard", headers=_superadmin_headers(client))
        assert resp.status_code == 200

    assert counter.count == 0, (
        f"expected 0 new httpx.AsyncClient() instantiations for the internal "
        f"health checks per dashboard request (1, or 2 with JAX_PLATFORM_URL "
        f"set), got {counter.count}"
    )
