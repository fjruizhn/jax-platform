"""Ingress for invitations that create Office people, never project access."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.account_invitations import (AccountInvitationError,
    issue_account_invitation_in_cursor, revoke_account_invitation_in_cursor,
    list_account_invitations_in_cursor)
from virtual_office.invitation_config import load_invitation_config_from_environment

router = APIRouter(prefix="/api/virtual-office/account-invitations")


class AccountInvitationIssueRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


def _actor(user: AuthUser) -> int:
    try:
        value = int(user.user_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="office_account_invitation_denied") from error
    if value < 1:
        raise HTTPException(status_code=403, detail="office_account_invitation_denied")
    return value


def _raise(error: AccountInvitationError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.code) from error


@router.get("")
async def list_account_invitations(response: Response,
                                   limit: int = Query(default=25, ge=1, le=100),
                                   after_id: str | None = Query(default=None, max_length=36),
                                   user: AuthUser = Depends(get_current_user)):
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            rows, next_after_id = await list_account_invitations_in_cursor(
                cur, actor_user_id=_actor(user), limit=limit, after_id=after_id)
    except AccountInvitationError as error:
        _raise(error)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"
    return {"invitations": [{**row, "expires_at": row["expires_at"].isoformat()} for row in rows],
            "next_after_id": next_after_id}


@router.post("")
async def issue_account_invitation(payload: AccountInvitationIssueRequest,
                                   idempotency_key: str = Header(alias="Idempotency-Key"),
                                   user: AuthUser = Depends(get_current_user)):
    config = load_invitation_config_from_environment()
    if config is None:
        raise HTTPException(status_code=503, detail="office_account_invitation_unconfigured")
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            issued = await issue_account_invitation_in_cursor(
                cur, actor_user_id=_actor(user), email=payload.email,
                idempotency_key=idempotency_key, config=config)
    except AccountInvitationError as error:
        _raise(error)
    return {"invitation_id": issued.invitation_id, "status": "ISSUED",
            "version": issued.version, "expires_at": issued.expires_at.isoformat(), "replayed": issued.replayed}


@router.delete("/{invitation_id}")
async def revoke_account_invitation(invitation_id: str, expected_version: int,
                                    idempotency_key: str = Header(alias="Idempotency-Key"),
                                    user: AuthUser = Depends(get_current_user)):
    if expected_version < 1:
        raise HTTPException(status_code=422, detail="office_account_invitation_version_invalid")
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            version, replayed = await revoke_account_invitation_in_cursor(
                cur, actor_user_id=_actor(user), invitation_id=invitation_id,
                expected_version=expected_version, idempotency_key=idempotency_key)
    except AccountInvitationError as error:
        _raise(error)
    return {"invitation_id": invitation_id, "status": "REVOKED", "version": version, "replayed": replayed}
