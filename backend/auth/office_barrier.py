"""Fail-closed classic-surface barrier for Office-only account classes.

The account class is fetched by ``verificar_sesion`` on every request.  This
module deliberately has no prefix-only allow rule: a future ``/api/auth/*``
endpoint cannot accidentally become reachable by an Office-only identity.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .jwt import decode_token
from .middleware import verificar_sesion
from .models import AuthUser

_RESTRICTED = frozenset({"OFFICE_INTERNAL", "EXTERNAL_CLIENT"})
_AUTH_ALLOWLIST = frozenset({
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/me/password"),
    ("POST", "/api/auth/forgot-password"),
    ("POST", "/api/auth/reset-password"),
    ("POST", "/api/auth/mfa/login/verify"),
    ("GET", "/api/auth/mfa/status"),
    ("POST", "/api/auth/mfa/enrollment/start"),
    ("POST", "/api/auth/mfa/enrollment/confirm"),
    ("POST", "/api/auth/mfa/reenrollment/start"),
    ("POST", "/api/auth/mfa/step-up"),
    ("POST", "/api/auth/mfa/step-up/verify"),
    ("POST", "/api/auth/mfa/recover"),
    ("POST", "/api/auth/mfa/recovery/enrollment/start"),
    ("POST", "/api/auth/mfa/recovery-codes/regenerate"),
    ("POST", "/api/auth/mfa/disable"),
    ("POST", "/api/auth/mfa/root-reset"),
})


def is_office_surface(method: str, path: str) -> bool:
    """Only the dedicated Office API namespace is forward-compatible."""
    return path == "/api/virtual-office" or path.startswith("/api/virtual-office/")


def restricted_route_allowed(method: str, path: str) -> bool:
    return (method, path) in _AUTH_ALLOWLIST or is_office_surface(method, path)


def require_classic_stream_user(user: AuthUser) -> None:
    """Streams are classic-only even when called outside HTTP middleware."""
    if getattr(user, "account_class", None) != "INTERNAL":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="office_surface_required")


class OfficeSurfaceBarrierMiddleware(BaseHTTPMiddleware):
    """Reject an authenticated Office-only principal before any classic route.

    An absent or invalid bearer is left to the route's normal authentication
    dependency, preserving existing 401 semantics.  A valid restricted bearer
    is resolved directly from MariaDB and denied before route/body/provider
    code can run.
    """
    async def dispatch(self, request: Request, call_next):
        authorization = request.headers.get("authorization")
        if not authorization:
            return await call_next(request)
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return await call_next(request)
        # These endpoints authenticate themselves (and are the finite
        # subject-proof/session surface explicitly granted to restricted
        # accounts).  Skipping the preflight here preserves the one canonical
        # account lookup performed by their dependency/handler.
        if restricted_route_allowed(request.method, request.url.path):
            return await call_next(request)
        try:
            # Resolve the canonical class even for a password-change-pending
            # restricted user.  The barrier must run before body, limiter,
            # quota or route work; normal INTERNAL pending-password semantics
            # still occur in the endpoint dependency below.
            user = await verificar_sesion(decode_token(token), "access", admite_cambio_pendiente=True)
        except HTTPException:
            return await call_next(request)
        except Exception:
            # An unexpected classification failure must never turn into a
            # classic access grant for a bearer that reached this boundary.
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "sesion_invalida"})
        if user.account_class in _RESTRICTED and not restricted_route_allowed(request.method, request.url.path):
            return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": "office_surface_required"})
        return await call_next(request)
