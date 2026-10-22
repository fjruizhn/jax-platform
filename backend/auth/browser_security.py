"""Browser-facing authentication boundary controls.

The proxy and Uvicorn configuration are deliberately separate from this ASGI
layer.  This module only trusts the request headers that reached it and keeps
the refresh cookie host-only by never accepting a Domain attribute.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class BrowserSecurityConfigError(RuntimeError):
    """A public browser deployment has incomplete or unsafe configuration."""


@dataclass(frozen=True)
class BrowserSecurityConfig:
    allowed_origins: frozenset[str]
    allowed_hosts: tuple[str, ...]
    cookie_secure: bool


def _is_production() -> bool:
    return os.getenv("JAX_ENVIRONMENT", "development").strip().lower() == "production"


def _csv(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    values = tuple(value.strip() for value in raw.split(",") if value.strip())
    if len(values) != len(set(values)):
        raise BrowserSecurityConfigError(f"{name} contains duplicate values")
    return values


def _validate_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise BrowserSecurityConfigError("JAX_PUBLIC_ALLOWED_ORIGINS must contain exact HTTP origins")
    if value != f"{parsed.scheme}://{parsed.netloc}":
        raise BrowserSecurityConfigError("JAX_PUBLIC_ALLOWED_ORIGINS must be canonical exact origins")
    return value


def _validate_host(value: str) -> str:
    allowed = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-")
    if (not value or value == "*" or value.startswith((".", "-")) or value.endswith(".")
            or any(char not in allowed for char in value) or value != value.lower()):
        raise BrowserSecurityConfigError("JAX_PUBLIC_ALLOWED_HOSTS must contain lowercase host names")
    return value


def _cookie_secure_from_environment() -> bool:
    raw = os.getenv("JAX_REFRESH_COOKIE_SECURE")
    if raw is None:
        return _is_production()
    normalized = raw.strip().lower()
    if normalized not in {"true", "false"}:
        raise BrowserSecurityConfigError("JAX_REFRESH_COOKIE_SECURE must be true or false")
    secure = normalized == "true"
    if _is_production() and not secure:
        raise BrowserSecurityConfigError("production refresh cookies must be Secure")
    return secure


def load_browser_security_config() -> BrowserSecurityConfig:
    origins = _csv("JAX_PUBLIC_ALLOWED_ORIGINS")
    hosts = _csv("JAX_PUBLIC_ALLOWED_HOSTS")
    if _is_production() and (not origins or not hosts):
        raise BrowserSecurityConfigError(
            "production requires JAX_PUBLIC_ALLOWED_ORIGINS and JAX_PUBLIC_ALLOWED_HOSTS")
    # Local and legacy deployments keep their explicit FRONTEND_ORIGIN until
    # the public deployment supplies the stricter public allowlist.  In
    # production there is no such fallback: missing configuration stops boot.
    if not origins and not _is_production():
        legacy_origin = os.getenv("FRONTEND_ORIGIN", "").strip()
        origins = (legacy_origin,) if legacy_origin else ()
    return BrowserSecurityConfig(
        allowed_origins=frozenset(_validate_origin(origin) for origin in origins),
        allowed_hosts=tuple(_validate_host(host) for host in hosts),
        cookie_secure=_cookie_secure_from_environment(),
    )


def refresh_cookie_secure() -> bool:
    """Read strict cookie policy at each writer so test and dev configs stay explicit."""
    return _cookie_secure_from_environment()


def password_reset_origin(account_class: str) -> str:
    """Select a reset origin from the canonical account class, never Host.

    Office identities are intentionally isolated from the classic frontend.
    A missing Office origin is an operational misconfiguration, not a reason
    to mint a password-reset capability pointing at the wrong surface.
    """
    variable = ("OFFICE_PUBLIC_ORIGIN" if account_class in {"OFFICE_INTERNAL", "EXTERNAL_CLIENT"}
                else "FRONTEND_ORIGIN")
    value = os.getenv(variable, "").strip()
    if not value:
        raise BrowserSecurityConfigError(f"{variable} is required for password reset links")
    return _validate_origin(value)


def set_refresh_cookie(response: Response, value: str, *, max_age: int) -> None:
    response.set_cookie(
        key="refresh_token", value=value, httponly=True, secure=refresh_cookie_secure(),
        max_age=max_age, samesite="lax", path="/",
    )


def delete_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key="refresh_token", httponly=True, secure=refresh_cookie_secure(),
        samesite="lax", path="/",
    )


class BrowserOriginGuard(BaseHTTPMiddleware):
    """Reject cross-origin unsafe browser requests before route handling.

    A bearer client can omit Origin.  A browser request that supplies Origin
    must match exactly, and a cookie-authenticated unsafe request may not omit
    it because that would make a CSRF decision from an absent signal.
    """

    _SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

    def __init__(self, app, *, allowed_origins: frozenset[str]):
        super().__init__(app)
        self._allowed_origins = allowed_origins

    async def dispatch(self, request: Request, call_next):
        if request.method in self._SAFE_METHODS:
            return await call_next(request)
        origin = request.headers.get("origin")
        if origin is not None and origin not in self._allowed_origins:
            return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
        if request.cookies.get("refresh_token") is not None and origin is None:
            return JSONResponse({"detail": "origin_required"}, status_code=403)
        return await call_next(request)
