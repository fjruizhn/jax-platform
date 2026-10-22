"""Browser boundary controls for the public Office surface.

The production change these tests protect is a permissive Origin decision or
an inconsistent refresh cookie attribute.  Either lets a sibling site submit
a cookie-authenticated unsafe request, or leaves a session cookie behind.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest


def _app(*, origins=("https://virtualoffice.axioma-ia.io",)):
    from auth.browser_security import BrowserOriginGuard

    app = FastAPI()
    app.add_middleware(BrowserOriginGuard, allowed_origins=frozenset(origins))

    @app.post("/api/auth/logout")
    async def logout():
        return JSONResponse({"ok": True})

    return app


def test_cookie_unsafe_request_rejects_sibling_origin():
    response = TestClient(_app()).post(
        "/api/auth/logout",
        headers={"Origin": "https://sibling.axioma-ia.io", "Cookie": "refresh_token=present"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "origin_not_allowed"}


def test_cookie_unsafe_request_rejects_missing_origin():
    response = TestClient(_app()).post(
        "/api/auth/logout", headers={"Cookie": "refresh_token=present"}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "origin_required"}


def test_bearer_unsafe_request_without_origin_stays_available_to_non_browser_clients():
    response = TestClient(_app()).post(
        "/api/auth/logout", headers={"Authorization": "Bearer capability"}
    )
    assert response.status_code == 200


def test_any_present_origin_must_match_exact_allowlist_even_without_cookie():
    response = TestClient(_app()).post(
        "/api/auth/logout", headers={"Origin": "null", "Authorization": "Bearer capability"}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "origin_not_allowed"}


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


def test_development_keeps_the_existing_frontend_origin_when_no_public_override_exists(monkeypatch):
    from auth.browser_security import load_browser_security_config

    monkeypatch.setenv("JAX_ENVIRONMENT", "development")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.delenv("JAX_PUBLIC_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("JAX_PUBLIC_ALLOWED_HOSTS", raising=False)
    assert load_browser_security_config().allowed_origins == frozenset({"http://localhost:5173"})


@pytest.mark.parametrize(
    ("account_class", "expected"),
    [
        ("INTERNAL", "https://axioma-ia.io"),
        ("OFFICE_INTERNAL", "https://virtualoffice.axioma-ia.io"),
        ("EXTERNAL_CLIENT", "https://virtualoffice.axioma-ia.io"),
    ],
)
def test_password_reset_origin_comes_from_canonical_account_class_config(monkeypatch, account_class, expected):
    from auth.browser_security import password_reset_origin

    monkeypatch.setenv("FRONTEND_ORIGIN", "https://axioma-ia.io")
    monkeypatch.setenv("OFFICE_PUBLIC_ORIGIN", "https://virtualoffice.axioma-ia.io")
    assert password_reset_origin(account_class) == expected


def test_office_password_reset_fails_closed_without_its_public_origin(monkeypatch):
    from auth.browser_security import BrowserSecurityConfigError, password_reset_origin

    monkeypatch.delenv("OFFICE_PUBLIC_ORIGIN", raising=False)
    with pytest.raises(BrowserSecurityConfigError):
        password_reset_origin("OFFICE_INTERNAL")


@pytest.mark.parametrize("environment, env", [
    ("production", {}),
    ("production", {"JAX_PUBLIC_ALLOWED_ORIGINS": "not-a-url", "JAX_PUBLIC_ALLOWED_HOSTS": "virtualoffice.axioma-ia.io"}),
    ("production", {"JAX_PUBLIC_ALLOWED_ORIGINS": "https://virtualoffice.axioma-ia.io", "JAX_PUBLIC_ALLOWED_HOSTS": "https://virtualoffice.axioma-ia.io"}),
    ("production", {"JAX_PUBLIC_ALLOWED_ORIGINS": "https://virtualoffice.axioma-ia.io", "JAX_PUBLIC_ALLOWED_HOSTS": "*"}),
])
def test_public_browser_config_fails_closed_when_invalid(monkeypatch, environment, env):
    from auth.browser_security import BrowserSecurityConfigError, load_browser_security_config

    monkeypatch.setenv("JAX_ENVIRONMENT", environment)
    for key in ("JAX_PUBLIC_ALLOWED_ORIGINS", "JAX_PUBLIC_ALLOWED_HOSTS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(BrowserSecurityConfigError):
        load_browser_security_config()
