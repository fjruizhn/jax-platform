"""Dedicated Office HTTP process backed by its own MariaDB connection.

The deployment supplies JAX_DB_* with Office-only values while this process
reuses the proven authentication and Office modules.  Classic JAX routers,
background workers, and their startup side effects are never registered here.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from auth.browser_security import BrowserOriginGuard, HostBoundCORSMiddleware, load_browser_security_config
from db.connection import close_pool, get_pool
from db.runtime_contract import assert_runtime_db_contract
from http_client import close_http_client, get_http_client
import limite_json
from virtual_office.db_identity import require_dedicated_db

from api.auth import router as auth_router
from api.apariencia import router as appearance_router
from api.admin.smtp import router as smtp_router
from api.virtual_office.projects import router as projects_router
from api.virtual_office.people import router as people_router
from api.virtual_office.grants import router as grants_router
from api.virtual_office.invitations import router as invitations_router
from api.virtual_office.account_invitations import router as account_invitations_router
from api.virtual_office.session import router as session_router
from api.virtual_office.agents import router as agents_router


_AUTH_ROUTES = frozenset({
    ("POST", "/api/auth/login"), ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/logout"), ("GET", "/api/auth/me"),
    ("POST", "/api/auth/me/password"),
    ("POST", "/api/auth/forgot-password"), ("POST", "/api/auth/reset-password"),
    ("GET", "/api/auth/mfa/status"), ("POST", "/api/auth/mfa/login/verify"),
    ("POST", "/api/auth/mfa/enrollment/start"),
    ("POST", "/api/auth/mfa/enrollment/confirm"),
    ("POST", "/api/auth/mfa/reenrollment/start"),
    ("POST", "/api/auth/mfa/step-up"), ("POST", "/api/auth/mfa/step-up/verify"),
    ("POST", "/api/auth/mfa/recover"),
    ("POST", "/api/auth/mfa/recovery/enrollment/start"),
    ("POST", "/api/auth/mfa/recovery-codes/regenerate"),
    ("POST", "/api/auth/mfa/disable"),
})
_SMTP_ROUTES = frozenset({
    ("GET", "/api/admin/smtp"), ("PUT", "/api/admin/smtp"),
    ("POST", "/api/admin/smtp/test-connection"),
    ("POST", "/api/admin/smtp/test"),
})


class OfficeRouteBoundary:
    """The private listener exposes only Office's browser API contract."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, method = scope["path"], scope["method"]
        allowed = ((method, path) in _AUTH_ROUTES
                   or (method, path) in _SMTP_ROUTES
                   or method == "GET" and path == "/api/apariencia"
                   or path == "/api/virtual-office"
                   or path.startswith("/api/virtual-office/"))
        if not allowed:
            await JSONResponse({"detail": "not_found"}, status_code=404)(scope, receive, send)
            return

        async def no_store(message):
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", ())
                           if key.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, no_store)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    require_dedicated_db()
    pool = await get_pool()
    await assert_runtime_db_contract(pool)
    await get_http_client()
    try:
        yield
    finally:
        await close_http_client()
        await close_pool()


app = FastAPI(title="Virtual Office", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, _error: RequestValidationError):
    code = "mfa_invalid_request" if request.url.path.startswith("/api/auth/mfa/") else "office_invalid_request"
    return JSONResponse({"detail": code}, status_code=422, headers={"Cache-Control": "no-store"})


for router in (auth_router, appearance_router, smtp_router, projects_router, people_router,
               grants_router, invitations_router, account_invitations_router,
               session_router, agents_router):
    app.include_router(router)

app.add_middleware(limite_json.LimiteDeProfundidadJSON,
                   limite=limite_json.limite_configurado())
app.add_middleware(OfficeRouteBoundary)
browser_security = load_browser_security_config()
app.add_middleware(HostBoundCORSMiddleware, host_origins=browser_security.host_origins)
app.add_middleware(BrowserOriginGuard, host_origins=browser_security.host_origins)
if browser_security.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(browser_security.allowed_hosts))
