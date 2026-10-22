"""Browser boundary controls for the public Office surface.

The production changes protected here reject a classic origin at the Office
host, startup without an explicit production mode, and a proxy command that
would trust forwarded headers from arbitrary peers.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest


OFFICE_HOST = "virtualoffice.axioma-ia.io"
OFFICE_ORIGIN = f"https://{OFFICE_HOST}"
CLASSIC_HOST = "axioma-ia.io"
CLASSIC_ORIGIN = f"https://{CLASSIC_HOST}"


def _app():
    from auth.browser_security import BrowserOriginGuard, HostBoundCORSMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    bindings = {CLASSIC_HOST: CLASSIC_ORIGIN, OFFICE_HOST: OFFICE_ORIGIN}
    app = FastAPI()
    app.add_middleware(HostBoundCORSMiddleware, host_origins=bindings)
    app.add_middleware(BrowserOriginGuard, host_origins=bindings)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[*bindings, "127.0.0.1", "localhost"])

    @app.post("/api/auth/logout")
    async def logout():
        return JSONResponse({"ok": True})

    @app.get("/api/auth/me")
    async def me():
        return JSONResponse({"ok": True})

    return app


def test_classic_origin_cannot_submit_cookie_request_to_office_host():
    response = TestClient(_app()).post(
        "/api/auth/logout",
        headers={"Host": OFFICE_HOST, "Origin": CLASSIC_ORIGIN, "Cookie": "refresh_token=present"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "origin_not_allowed"}
    assert "access-control-allow-origin" not in response.headers


def test_matching_office_origin_receives_credentialed_cors_only_at_office_host():
    response = TestClient(_app()).post(
        "/api/auth/logout",
        headers={"Host": OFFICE_HOST, "Origin": OFFICE_ORIGIN, "Cookie": "refresh_token=present"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == OFFICE_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cross_host_preflight_is_rejected_before_it_advertises_credentials():
    response = TestClient(_app()).options(
        "/api/auth/logout",
        headers={"Host": OFFICE_HOST, "Origin": CLASSIC_ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_cookie_unsafe_request_rejects_missing_origin():
    response = TestClient(_app()).post(
        "/api/auth/logout", headers={"Host": OFFICE_HOST, "Cookie": "refresh_token=present"}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "origin_required"}


def test_bearer_unsafe_request_without_origin_stays_available_to_non_browser_clients():
    response = TestClient(_app()).post(
        "/api/auth/logout", headers={"Host": OFFICE_HOST, "Authorization": "Bearer capability"}
    )
    assert response.status_code == 200


def test_operational_health_host_is_trusted_without_receiving_browser_cors_capability():
    response = TestClient(_app()).get("/api/auth/me", headers={"Host": "127.0.0.1"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_operational_host_rejects_browser_origin_on_unsafe_requests():
    response = TestClient(_app()).post(
        "/api/auth/logout", headers={"Host": "localhost", "Origin": OFFICE_ORIGIN}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "origin_not_allowed"}
    assert "access-control-allow-origin" not in response.headers


def test_refresh_cookie_set_and_delete_share_browser_security_attributes(monkeypatch):
    from auth import browser_security
    from starlette.responses import Response

    monkeypatch.setattr(browser_security, "refresh_cookie_secure", lambda: True)
    issued, cleared = Response(), Response()
    browser_security.set_refresh_cookie(issued, "token", max_age=120)
    browser_security.delete_refresh_cookie(cleared)

    issue = issued.headers.get("set-cookie", "")
    delete = cleared.headers.get("set-cookie", "")
    for header in (issue, delete):
        lower = header.lower()
        assert "httponly" in lower
        assert "samesite=lax" in lower
        assert "secure" in lower
        assert "path=/" in lower
        assert "domain=" not in lower
    assert "max-age=120" in issue.lower()
    assert "max-age=0" in delete.lower()


def _production_environment(monkeypatch, bindings=f"{CLASSIC_HOST}={CLASSIC_ORIGIN};{OFFICE_HOST}={OFFICE_ORIGIN}"):
    monkeypatch.setenv("JAX_ENVIRONMENT", "production")
    monkeypatch.setenv("JAX_PUBLIC_HOST_ORIGIN_BINDINGS", bindings)
    monkeypatch.setenv("OFFICE_PUBLIC_ORIGIN", OFFICE_ORIGIN)
    monkeypatch.setenv("JAX_REFRESH_COOKIE_SECURE", "true")
    monkeypatch.setenv("JAX_TRUSTED_PROXIES", "127.0.0.1")
    monkeypatch.setenv("JAX_OPERATIONAL_TRUSTED_HOSTS", "127.0.0.1,localhost")


def test_development_accepts_canonical_vite_origin_with_port(monkeypatch):
    from auth.browser_security import load_browser_security_config

    monkeypatch.setenv("JAX_ENVIRONMENT", "development")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.delenv("JAX_PUBLIC_HOST_ORIGIN_BINDINGS", raising=False)
    monkeypatch.delenv("OFFICE_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("JAX_OPERATIONAL_TRUSTED_HOSTS", raising=False)
    config = load_browser_security_config()
    assert dict(config.host_origins) == {"localhost": "http://localhost:5173"}
    assert config.allowed_hosts == ("localhost",)


def test_production_requires_separate_operational_host_allowlist(monkeypatch):
    from auth.browser_security import BrowserSecurityConfigError, load_browser_security_config

    _production_environment(monkeypatch)
    monkeypatch.delenv("JAX_OPERATIONAL_TRUSTED_HOSTS")
    with pytest.raises(BrowserSecurityConfigError):
        load_browser_security_config()


@pytest.mark.parametrize("environment", (None, "typo"))
def test_public_security_config_requires_explicit_production_mode(monkeypatch, environment):
    from auth.browser_security import BrowserSecurityConfigError, load_browser_security_config

    _production_environment(monkeypatch)
    if environment is None:
        monkeypatch.delenv("JAX_ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("JAX_ENVIRONMENT", environment)
    with pytest.raises(BrowserSecurityConfigError):
        load_browser_security_config()


@pytest.mark.parametrize(
    "bindings, office_origin",
    [
        (f"{OFFICE_HOST}=http://{OFFICE_HOST}", f"http://{OFFICE_HOST}"),
        (f"{CLASSIC_HOST}={CLASSIC_ORIGIN};{OFFICE_HOST}=https://{OFFICE_HOST}:8443", f"https://{OFFICE_HOST}:8443"),
        (f"{CLASSIC_HOST}={CLASSIC_ORIGIN};{OFFICE_HOST}=https://other.example.test", OFFICE_ORIGIN),
        ("127.0.0.1=https://127.0.0.1", "https://127.0.0.1"),
    ],
)
def test_production_rejects_insecure_or_unbound_office_origin(monkeypatch, bindings, office_origin):
    from auth.browser_security import BrowserSecurityConfigError, load_browser_security_config

    _production_environment(monkeypatch, bindings)
    monkeypatch.setenv("OFFICE_PUBLIC_ORIGIN", office_origin)
    with pytest.raises(BrowserSecurityConfigError):
        load_browser_security_config()


def test_production_requires_a_literal_trusted_proxy_address(monkeypatch):
    from auth.browser_security import BrowserSecurityConfigError, load_browser_security_config

    _production_environment(monkeypatch)
    monkeypatch.setenv("JAX_TRUSTED_PROXIES", "*")
    with pytest.raises(BrowserSecurityConfigError):
        load_browser_security_config()


def test_operational_host_allowlist_rejects_ports_and_public_hosts(monkeypatch):
    from auth.browser_security import BrowserSecurityConfigError, load_browser_security_config

    _production_environment(monkeypatch)
    monkeypatch.setenv("JAX_OPERATIONAL_TRUSTED_HOSTS", f"127.0.0.1,{OFFICE_HOST}")
    with pytest.raises(BrowserSecurityConfigError):
        load_browser_security_config()
    monkeypatch.setenv("JAX_OPERATIONAL_TRUSTED_HOSTS", "127.0.0.1:8080")
    with pytest.raises(BrowserSecurityConfigError):
        load_browser_security_config()


def test_versioned_service_enables_proxy_headers_only_for_configured_proxies():
    unit = (Path(__file__).resolve().parents[2] / "ops/migration/systemd-units/jax-platform.service").read_text()
    assert "--proxy-headers" in unit
    assert "--forwarded-allow-ips=${JAX_TRUSTED_PROXIES}" in unit
