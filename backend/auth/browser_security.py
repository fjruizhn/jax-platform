"""Host-bound browser authentication controls for public surfaces."""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class BrowserSecurityConfigError(RuntimeError):
    """Public browser configuration is incomplete or unsafe."""


@dataclass(frozen=True)
class BrowserSecurityConfig:
    host_origins: MappingProxyType
    operational_hosts: tuple[str, ...]
    office_origin: str | None
    cookie_secure: bool
    trusted_proxies: tuple[str, ...]

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.host_origins, *self.operational_hosts)))


def _environment() -> str:
    value = os.getenv("JAX_ENVIRONMENT", "").strip().lower()
    if value not in {"production", "development", "test"}:
        raise BrowserSecurityConfigError("JAX_ENVIRONMENT must be production, development, or test")
    return value


def _validate_origin(value: str, *, production: bool) -> tuple[str, str]:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise BrowserSecurityConfigError("public origins must use valid ports") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise BrowserSecurityConfigError("public origins must be exact scheme and host pairs")
    host = _validate_host(parsed.hostname)
    if production and (parsed.scheme != "https" or port is not None):
        raise BrowserSecurityConfigError("production public origins must use HTTPS without a port")
    if production:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise BrowserSecurityConfigError("production public origins must not use private addresses")
    canonical = f"{parsed.scheme}://{host}"
    if port is not None:
        canonical = f"{canonical}:{port}"
    if value != canonical:
        raise BrowserSecurityConfigError("public origins must be canonical exact origins")
    return host, canonical


def _validate_host(value: str) -> str:
    host = value.strip().lower()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:  # fail-soft: a non-IP hostname remains subject to the strict hostname validator below
        pass
    if not host or len(host) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
        raise BrowserSecurityConfigError("trusted hosts must be exact hostnames or literal IP addresses")
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
           for label in host.split(".")):
        raise BrowserSecurityConfigError("trusted hosts must be exact hostnames or literal IP addresses")
    return host


def _bindings_from_environment(*, production: bool) -> dict[str, str]:
    raw = os.getenv("JAX_PUBLIC_HOST_ORIGIN_BINDINGS", "").strip()
    if not raw:
        if production:
            raise BrowserSecurityConfigError("production requires JAX_PUBLIC_HOST_ORIGIN_BINDINGS")
        legacy = os.getenv("FRONTEND_ORIGIN", "").strip()
        if not legacy:
            return {}
        host, origin = _validate_origin(legacy, production=False)
        return {host: origin}
    bindings: dict[str, str] = {}
    for item in raw.split(";"):
        host, separator, origin = item.strip().partition("=")
        if not separator or not host or not origin:
            raise BrowserSecurityConfigError("JAX_PUBLIC_HOST_ORIGIN_BINDINGS uses host=origin pairs")
        origin_host, canonical_origin = _validate_origin(origin.strip(), production=production)
        host = _validate_host(host)
        if host != origin_host or host in bindings:
            raise BrowserSecurityConfigError("each public host must bind once to its own exact origin")
        bindings[host] = canonical_origin
    return bindings


def _operational_hosts(*, production: bool, public_hosts: set[str]) -> tuple[str, ...]:
    values = tuple(
        _validate_host(value)
        for value in os.getenv("JAX_OPERATIONAL_TRUSTED_HOSTS", "").split(",")
        if value.strip()
    )
    if production and not values:
        raise BrowserSecurityConfigError("production requires JAX_OPERATIONAL_TRUSTED_HOSTS")
    if len(values) != len(set(values)):
        raise BrowserSecurityConfigError("JAX_OPERATIONAL_TRUSTED_HOSTS contains duplicates")
    if public_hosts.intersection(values):
        raise BrowserSecurityConfigError("operational trusted hosts must be separate from public hosts")
    return values


def _trusted_proxies(*, production: bool) -> tuple[str, ...]:
    values = tuple(value.strip() for value in os.getenv("JAX_TRUSTED_PROXIES", "").split(",") if value.strip())
    if production and not values:
        raise BrowserSecurityConfigError("production requires JAX_TRUSTED_PROXIES")
    try:
        for value in values:
            ipaddress.ip_address(value)
    except ValueError as exc:
        raise BrowserSecurityConfigError("JAX_TRUSTED_PROXIES must contain literal IP addresses") from exc
    if len(values) != len(set(values)):
        raise BrowserSecurityConfigError("JAX_TRUSTED_PROXIES contains duplicates")
    return values


def _cookie_secure_from_environment(*, production: bool) -> bool:
    raw = os.getenv("JAX_REFRESH_COOKIE_SECURE")
    if raw is None:
        if production:
            raise BrowserSecurityConfigError("production requires JAX_REFRESH_COOKIE_SECURE=true")
        return False
    normalized = raw.strip().lower()
    if normalized not in {"true", "false"}:
        raise BrowserSecurityConfigError("JAX_REFRESH_COOKIE_SECURE must be true or false")
    secure = normalized == "true"
    if production and not secure:
        raise BrowserSecurityConfigError("production refresh cookies must be Secure")
    return secure


def load_browser_security_config() -> BrowserSecurityConfig:
    environment = _environment()
    production = environment == "production"
    bindings = _bindings_from_environment(production=production)
    office_origin = os.getenv("OFFICE_PUBLIC_ORIGIN", "").strip() or None
    if production and office_origin is None:
        raise BrowserSecurityConfigError("production requires OFFICE_PUBLIC_ORIGIN")
    if office_origin is not None:
        office_host, office_origin = _validate_origin(office_origin, production=production)
        if bindings.get(office_host) != office_origin:
            raise BrowserSecurityConfigError("OFFICE_PUBLIC_ORIGIN must match a declared host-origin binding")
    return BrowserSecurityConfig(
        host_origins=MappingProxyType(bindings),
        operational_hosts=_operational_hosts(production=production, public_hosts=set(bindings)),
        office_origin=office_origin,
        cookie_secure=_cookie_secure_from_environment(production=production),
        trusted_proxies=_trusted_proxies(production=production),
    )


def refresh_cookie_secure() -> bool:
    return load_browser_security_config().cookie_secure


def password_reset_origin(account_class: str) -> str:
    config = load_browser_security_config()
    if account_class in {"OFFICE_INTERNAL", "EXTERNAL_CLIENT"}:
        if config.office_origin is None:
            raise BrowserSecurityConfigError("OFFICE_PUBLIC_ORIGIN is required for Office password reset links")
        return config.office_origin
    classic = os.getenv("FRONTEND_ORIGIN", "").strip()
    host, origin = _validate_origin(classic, production=_environment() == "production")
    if config.host_origins.get(host) != origin:
        raise BrowserSecurityConfigError("FRONTEND_ORIGIN must match a declared host-origin binding")
    return origin


def set_refresh_cookie(response: Response, value: str, *, max_age: int) -> None:
    response.set_cookie(key="refresh_token", value=value, httponly=True, secure=refresh_cookie_secure(),
                        max_age=max_age, samesite="lax", path="/")


def delete_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key="refresh_token", httponly=True, secure=refresh_cookie_secure(),
                           samesite="lax", path="/")


def _request_host(request: Request) -> str:
    return request.url.hostname or ""


class BrowserOriginGuard(BaseHTTPMiddleware):
    _SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

    def __init__(self, app, *, host_origins: dict[str, str]):
        super().__init__(app)
        self._host_origins = dict(host_origins)

    async def dispatch(self, request: Request, call_next):
        if request.method in self._SAFE_METHODS:
            return await call_next(request)
        origin = request.headers.get("origin")
        expected = self._host_origins.get(_request_host(request))
        if origin is not None and origin != expected:
            return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
        if request.cookies.get("refresh_token") is not None and origin is None:
            return JSONResponse({"detail": "origin_required"}, status_code=403)
        return await call_next(request)


class HostBoundCORSMiddleware(BaseHTTPMiddleware):
    """Credentialed CORS only for the Origin belonging to the request Host."""

    _METHODS = "GET, POST, PUT, DELETE"
    _HEADERS = frozenset({"authorization", "content-type", "idempotency-key"})

    def __init__(self, app, *, host_origins: dict[str, str]):
        super().__init__(app)
        self._host_origins = dict(host_origins)

    def _matches(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        return origin is not None and origin == self._host_origins.get(_request_host(request))

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
            if not self._matches(request):
                return PlainTextResponse("Disallowed CORS origin", status_code=403)
            requested = {name.strip().lower() for name in request.headers.get("access-control-request-headers", "").split(",") if name.strip()}
            if not requested.issubset(self._HEADERS):
                return PlainTextResponse("Disallowed CORS headers", status_code=400)
            origin = request.headers["origin"]
            return Response(status_code=200, headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": self._METHODS,
                "Access-Control-Allow-Headers": ", ".join(sorted(self._HEADERS)),
                "Vary": "Origin",
            })
        response = await call_next(request)
        if self._matches(request):
            response.headers["Access-Control-Allow-Origin"] = request.headers["origin"]
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers.append("Vary", "Origin")
        return response
