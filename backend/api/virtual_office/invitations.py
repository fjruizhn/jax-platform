"""Office invitation ingress.

The bearer is accepted only as a JSON POST field.  It is never a route
parameter, query parameter, response field, or logging value.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
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
    revoke_invitation_in_cursor,
)
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


def _raise(error: InvitationError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.code) from error


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

    The login limiter needs a stable per-bearer bucket in addition to the IP
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
    mfa_config = load_mfa_config_from_environment()
    if invitation_config is None or mfa_config is None:
        raise HTTPException(status_code=503, detail="office_invitation_unavailable")
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=actor, project_id=project_id, email=payload.email,
                audience=payload.audience, proof_event_id=user.mfa_proof_event_id,
                mfa_config=mfa_config, invitation_config=invitation_config,
                idempotency_key=idempotency_key,
            )
    except InvitationError as error:
        _raise(error)
    return {"id": invitation.invitation_id, "invitation_id": invitation.invitation_id,
            "version": invitation.version, "expires_at": invitation.expires_at.isoformat(),
            "replayed": invitation.replayed}


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
                proof_event_id=user.mfa_proof_event_id,
                mfa_config=load_mfa_config_from_environment(),
                idempotency_key=idempotency_key,
            )
    except InvitationError as error:
        _raise(error)
    return {"id": invitation_id, "invitation_id": invitation_id, "status": "REVOKED",
            "version": version, "replayed": replayed}


@router.post("/invitations/accept")
async def accept_invitation(payload: InvitationAcceptRequest,
                            request: Request, user: AuthUser | None = Depends(_optional_current_user)):
    _require_ascii_invitation_bearer(payload.token)
    password_hash: str | None = None
    if user is None:
        # Bound CPU before the untrusted password reaches bcrypt.  The bearer
        # itself is opaque and is only represented by a domain-separated
        # digest in the limiter, never returned or retained as a raw key.
        rate_limit.check_login_rate(request, _invitation_rate_key(payload.token))
        if not await invitation_bearer_preflight(payload.token):
            raise HTTPException(status_code=403, detail="office_invitation_invalid")
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
            replayed = await accept_invitation_in_cursor(
                cur, accepting_user_id=None if user is None else _actor_id(user),
                accepting_email=None if user is None else user.email,
                new_password_hash=password_hash, bearer_token=payload.token,
            )
    except InvitationError as error:
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
    if replayed is None:
        raise HTTPException(status_code=410, detail="office_invitation_expired")
    return {"status": "ACCEPTED", "replayed": replayed}
