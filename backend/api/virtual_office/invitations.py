"""Office invitation ingress.

The bearer is accepted only as a JSON POST field.  It is never a route
parameter, query parameter, response field, or logging value.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from pymysql.err import OperationalError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.jwt import decode_token
from auth.middleware import get_current_user, verificar_sesion
from auth.models import AuthUser
from auth.password_rules import problema_de_password
from auth import rate_limit
from db.seed import _hash
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.invitation_config import load_invitation_config_from_environment
from virtual_office.invitations import (
    InvitationError, accept_invitation_in_cursor, invitation_bearer_preflight, issue_invitation_in_cursor,
    list_active_invitations_in_cursor, revoke_invitation_in_cursor,
)
from virtual_office.account_invitations import (
    AccountInvitationError, accept_account_invitation_in_cursor,
    account_invitation_bearer_preflight,
)
# Retained for compatible import surfaces while invitation issuance uses the
# normal authenticated project-admin session instead of an MFA prerequisite.
from virtual_office.mfa import load_mfa_config_from_environment


router = APIRouter(prefix="/api/virtual-office")
optional_bearer = HTTPBearer(auto_error=False)


class InvitationIssueRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    audience: str


class InvitationAcceptRequest(BaseModel):
    token: str = Field(min_length=43, max_length=256)
    password: str | None = Field(default=None, max_length=256)


def _actor_id(user: AuthUser) -> int:
    try:
        actor = int(user.user_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="office_invitation_denied") from error
    if actor < 1:
        raise HTTPException(status_code=403, detail="office_invitation_denied")
    return actor


def _raise(error: InvitationError | AccountInvitationError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.code) from error


def _raise_if_retryable_database_error(error: OperationalError) -> None:
    """Translate only MariaDB's documented transaction-contention errors."""
    if error.args and error.args[0] in {1205, 1213}:
        raise HTTPException(status_code=503, detail="office_invitation_retryable") from error


def _require_durable_idempotency(idempotency_key: str) -> str:
    """Validate the global UUID that the transactional receipt owns."""
    try:
        parsed = uuid.UUID(idempotency_key)
    except (AttributeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="office_invitation_idempotency_invalid") from error
    if str(parsed) != idempotency_key.lower():
        raise HTTPException(status_code=422, detail="office_invitation_idempotency_invalid")
    return str(parsed)


def _invitation_rate_key(bearer_token: str) -> str:
    """Return a domain-separated limiter key without retaining the bearer.

    The invitation limiter needs a stable per-bearer bucket in addition to the IP
    bucket, but the invitation capability itself must never become a raw
    in-memory key or an accidental diagnostic value.
    """
    _require_ascii_invitation_bearer(bearer_token)
    return hashlib.sha256(
        b"office-invitation-bearer-rate/v1\x00" + bearer_token.encode("ascii")
    ).hexdigest()


def _require_ascii_invitation_bearer(bearer_token: str) -> None:
    """Reject malformed capabilities before they reach the limiter or bcrypt.

    ``InvitationAcceptRequest`` bounds Unicode code points, whereas the
    bearer contract is an ASCII capability.  Keeping this check at ingress
    prevents an encoding failure from escaping as a 500 and keeps malformed
    tokens indistinguishable from other invalid invitation bearers.
    """
    if (not isinstance(bearer_token, str) or not 43 <= len(bearer_token) <= 256
            or not bearer_token.isascii()):
        raise HTTPException(status_code=403, detail="office_invitation_invalid")


async def _optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
) -> AuthUser | None:
    if credentials is None:
        return None
    return await verificar_sesion(decode_token(credentials.credentials), "access")


@router.post("/projects/{project_id}/invitations")
async def issue_invitation(project_id: int, payload: InvitationIssueRequest,
                           idempotency_key: str = Header(alias="Idempotency-Key"),
                           user: AuthUser = Depends(get_current_user)):
    idempotency_key = _require_durable_idempotency(idempotency_key)
    actor = _actor_id(user)
    invitation_config = load_invitation_config_from_environment()
    if invitation_config is None:
        raise HTTPException(status_code=503, detail="office_invitation_unavailable")
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=actor, project_id=project_id, email=payload.email,
                audience=payload.audience, proof_event_id=None,
                mfa_config=None, invitation_config=invitation_config,
                idempotency_key=idempotency_key,
            )
    except InvitationError as error:
        _raise(error)
    except OperationalError as error:
        _raise_if_retryable_database_error(error)
        raise
    return {"id": invitation.invitation_id, "invitation_id": invitation.invitation_id,
            "version": invitation.version, "expires_at": invitation.expires_at.isoformat(),
            "replayed": invitation.replayed}


@router.get("/projects/{project_id}/invitations")
async def list_invitations(project_id: int, response: Response,
                           limit: int = Query(default=25, ge=1, le=100),
                           cursor: str | None = Query(default=None, max_length=512),
                           user: AuthUser = Depends(get_current_user)):
    """Return only unexpired issued invitations after the issuer gate."""
    actor = _actor_id(user)
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitations, next_cursor = await list_active_invitations_in_cursor(
                cur, actor_user_id=actor, project_id=project_id,
                proof_event_id=None, mfa_config=None,
                limit=limit, cursor=cursor,
            )
    except InvitationError as error:
        _raise(error)
    except OperationalError as error:
        # Listing is unavailable when durable authority or its confidential
        # rows cannot be read.  Do not turn a database failure into ``[]``.
        raise HTTPException(status_code=503, detail="office_invitation_unavailable") from error
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"
    return {
        "invitations": [
            {"id": invitation.invitation_id, "email": invitation.email,
             "audience": invitation.audience, "status": invitation.status,
             "version": invitation.version, "expires_at": invitation.expires_at.isoformat(),
             "delivery_state": invitation.delivery_state,
             "delivery_result_code": invitation.delivery_result_code}
            for invitation in invitations
        ],
        "next_cursor": next_cursor,
    }


@router.delete("/projects/{project_id}/invitations/{invitation_id}")
async def revoke_invitation(project_id: int, invitation_id: str, expected_version: int = Query(ge=1),
                            idempotency_key: str = Header(alias="Idempotency-Key"),
                            user: AuthUser = Depends(get_current_user)):
    idempotency_key = _require_durable_idempotency(idempotency_key)
    actor = _actor_id(user)
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            version, replayed = await revoke_invitation_in_cursor(
                cur, actor_user_id=actor, project_id=project_id, invitation_id=invitation_id,
                expected_version=expected_version,
                proof_event_id=None, mfa_config=None,
                idempotency_key=idempotency_key,
            )
    except InvitationError as error:
        _raise(error)
    except OperationalError as error:
        _raise_if_retryable_database_error(error)
        raise
    return {"id": invitation_id, "invitation_id": invitation_id, "status": "REVOKED",
            "version": version, "replayed": replayed}


@router.post("/invitations/accept")
async def accept_invitation(payload: InvitationAcceptRequest,
                            request: Request, user: AuthUser | None = Depends(_optional_current_user)):
    _require_ascii_invitation_bearer(payload.token)
    password_hash: str | None = None
    if user is None:
        # Bound unauthenticated database lookups as well as bcrypt.  The
        # limiter receives a digest, never the bearer itself.
        rate_limit.check_invitation_accept_rate(request, _invitation_rate_key(payload.token))
    project_bearer = await invitation_bearer_preflight(payload.token)
    account_bearer = await account_invitation_bearer_preflight(payload.token)
    # A valid bearer belongs to exactly one lifecycle.  Treat a collision as
    # an integrity outage rather than guessing which authority it grants.
    if project_bearer and account_bearer:
        raise HTTPException(status_code=503, detail="office_invitation_integrity")
    if not project_bearer and not account_bearer:
        raise HTTPException(status_code=403, detail="office_invitation_invalid")
    if user is None:
        if payload.password is None:
            raise HTTPException(status_code=422, detail="office_invitation_password_required")
        problem = problema_de_password(payload.password)
        if problem is not None:
            raise HTTPException(status_code=422, detail=f"office_invitation_password_{problem}")
        # bcrypt is deliberately outside the transaction and event loop.
        password_hash = await asyncio.to_thread(_hash, payload.password)
    elif payload.password is not None:
        raise HTTPException(status_code=409, detail="office_invitation_existing_session_required")
    from jax.memory.project_authority import (
        AuthorizationDenied,
        ProjectAuthorityRetryable,
        ProjectStateConflict,
    )
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            if account_bearer:
                replayed = await accept_account_invitation_in_cursor(
                    cur, accepting_user_id=None if user is None else _actor_id(user),
                    accepting_email=None if user is None else user.email,
                    new_password_hash=password_hash, bearer_token=payload.token)
            else:
                replayed = await accept_invitation_in_cursor(
                    cur, accepting_user_id=None if user is None else _actor_id(user),
                    accepting_email=None if user is None else user.email,
                    new_password_hash=password_hash, bearer_token=payload.token,
                )
    except (InvitationError, AccountInvitationError) as error:
        _raise(error)
    except ProjectAuthorityRetryable as error:
        # JAX reserves this class for transient MariaDB contention.  Its
        # stable contract is that retrying the same request is safe.
        raise HTTPException(status_code=503, detail="office_invitation_retryable") from error
    except (AuthorizationDenied, ProjectStateConflict) as error:
        # The lifecycle normally translates these races to InvitationError.
        # Keep the HTTP boundary fail-closed if JAX raises before that adapter
        # can do so: a revoked MFA/grant or closed project never becomes 500.
        raise HTTPException(status_code=403, detail="office_invitation_issuer_denied") from error
    except OperationalError as error:
        _raise_if_retryable_database_error(error)
        raise
    if replayed is None:
        raise HTTPException(status_code=410, detail="office_invitation_expired")
    return {"status": "ACCEPTED", "replayed": replayed}
