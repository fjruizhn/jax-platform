"""Office invitation ingress.

The bearer is accepted only as a JSON POST field.  It is never a route
parameter, query parameter, response field, or logging value.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.invitation_config import load_invitation_config_from_environment
from virtual_office.invitations import (
    InvitationError, accept_invitation_in_cursor, issue_invitation_in_cursor,
    revoke_invitation_in_cursor,
)
from virtual_office.mfa import load_mfa_config_from_environment


router = APIRouter(prefix="/api/virtual-office")


class InvitationIssueRequest(BaseModel):
    target_user_id: int = Field(gt=0)
    email: str = Field(min_length=3, max_length=320)
    target_account_class: str
    recipient_group_id: int | None = Field(default=None, gt=0)


class InvitationAcceptRequest(BaseModel):
    bearer_token: str = Field(min_length=43, max_length=256)


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
                cur, actor_user_id=actor, project_id=project_id, target_user_id=payload.target_user_id,
                email=payload.email, target_account_class=payload.target_account_class,
                recipient_group_id=payload.recipient_group_id, proof_event_id=user.mfa_proof_event_id,
                mfa_config=mfa_config, invitation_config=invitation_config,
            )
    except InvitationError as error:
        _raise(error)
    return {"invitation_id": invitation.invitation_id, "expires_at": invitation.expires_at.isoformat()}


@router.delete("/invitations/{invitation_id}")
async def revoke_invitation(invitation_id: str, user: AuthUser = Depends(get_current_user)):
    actor = _actor_id(user)
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            replayed = await revoke_invitation_in_cursor(
                cur, actor_user_id=actor, invitation_id=invitation_id,
                proof_event_id=user.mfa_proof_event_id,
                mfa_config=load_mfa_config_from_environment(),
            )
    except InvitationError as error:
        _raise(error)
    return {"invitation_id": invitation_id, "status": "REVOKED", "replayed": replayed}


@router.post("/invitations/accept")
async def accept_invitation(payload: InvitationAcceptRequest,
                            idempotency_key: str = Header(alias="Idempotency-Key"),
                            user: AuthUser = Depends(get_current_user)):
    actor = _actor_id(user)
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            replayed = await accept_invitation_in_cursor(
                cur, accepting_user_id=actor, accepting_email=user.email,
                bearer_token=payload.bearer_token, idempotency_key=idempotency_key,
            )
    except InvitationError as error:
        _raise(error)
    return {"status": "ACCEPTED", "replayed": replayed}
