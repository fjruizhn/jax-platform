"""Office invitation ingress.

The bearer is accepted only as a JSON POST field.  It is never a route
parameter, query parameter, response field, or logging value.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.jwt import decode_token
from auth.middleware import get_current_user, verificar_sesion
from auth.models import AuthUser
from auth.password_rules import problema_de_password
from db.seed import _hash
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.invitation_config import load_invitation_config_from_environment
from virtual_office.invitations import (
    InvitationError, accept_invitation_in_cursor, issue_invitation_in_cursor,
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


async def _optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
) -> AuthUser | None:
    if credentials is None:
        return None
    return await verificar_sesion(decode_token(credentials.credentials), "access")


@router.post("/projects/{project_id}/invitations")
async def issue_invitation(project_id: int, payload: InvitationIssueRequest,
                           user: AuthUser = Depends(get_current_user)):
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
            )
    except InvitationError as error:
        _raise(error)
    return {"invitation_id": invitation.invitation_id, "expires_at": invitation.expires_at.isoformat()}


@router.delete("/projects/{project_id}/invitations/{invitation_id}")
async def revoke_invitation(project_id: int, invitation_id: str, expected_version: int = Query(ge=1),
                            user: AuthUser = Depends(get_current_user)):
    actor = _actor_id(user)
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            replayed = await revoke_invitation_in_cursor(
                cur, actor_user_id=actor, project_id=project_id, invitation_id=invitation_id,
                expected_version=expected_version,
                proof_event_id=user.mfa_proof_event_id,
                mfa_config=load_mfa_config_from_environment(),
            )
    except InvitationError as error:
        _raise(error)
    return {"invitation_id": invitation_id, "status": "REVOKED", "replayed": replayed}


@router.post("/invitations/accept")
async def accept_invitation(payload: InvitationAcceptRequest,
                            user: AuthUser | None = Depends(_optional_current_user)):
    password_hash: str | None = None
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
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            replayed = await accept_invitation_in_cursor(
                cur, accepting_user_id=None if user is None else _actor_id(user),
                accepting_email=None if user is None else user.email,
                new_password_hash=password_hash, bearer_token=payload.token,
            )
    except InvitationError as error:
        _raise(error)
    if replayed is None:
        raise HTTPException(status_code=410, detail="office_invitation_expired")
    return {"status": "ACCEPTED", "replayed": replayed}
