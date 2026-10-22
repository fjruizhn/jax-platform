"""Root-controlled professional capability grants for existing project members."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.audit import record_authorization_event
from virtual_office.authority import authorize_office_in_cursor
from virtual_office.capabilities import OfficeOperation
from virtual_office.mfa import load_mfa_config_from_environment


router = APIRouter(prefix="/api/virtual-office/projects")


class ProfessionalGrantUpdate(BaseModel):
    basis: str = Field(min_length=1, max_length=128, pattern=r".*\S.*")
    evidence_ref: str = Field(min_length=1, max_length=255, pattern=r".*\S.*")
    expected_version: int = Field(ge=0)


def _actor_id(user: AuthUser) -> int:
    try:
        return int(user.user_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="office_professional_grant_denied") from error


@router.put("/{project_id}/professionals/{target_user_id}")
async def grant_professional_capability(project_id: int, target_user_id: int,
                                        payload: ProfessionalGrantUpdate,
                                        user: AuthUser = Depends(get_current_user)):
    """Grant the sole V1 professional capability to an existing staff member.

    Project administrators may manage collaborators but cannot mint approval
    authority.  The root check is separate from the project membership check
    and is repeated after the target/grant locks are acquired.
    """
    actor_user_id = _actor_id(user)
    if project_id < 1 or target_user_id < 1:
        raise HTTPException(status_code=404, detail="office_professional_target_not_found")
    config = load_mfa_config_from_environment()
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        decision = await authorize_office_in_cursor(
            cur, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
            require_recent_mfa=True, mfa_proof_event_id=user.mfa_proof_event_id, mfa_config=config,
        )
        if not decision.allowed or decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_professional_grant_denied")
        await cur.execute(
            "SELECT u.tenant_id,u.status,u.account_class,t.status,p.status "
            "FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
            "JOIN office_account_profile p ON p.user_id=u.user_id WHERE u.user_id=%s FOR UPDATE",
            (target_user_id,),
        )
        target = await cur.fetchone()
        if (target is None or target[1] != "active" or target[3] != "active" or target[4] != "ACTIVE"
                or target[2] not in {"INTERNAL", "OFFICE_INTERNAL"}):
            raise HTTPException(status_code=404, detail="office_professional_target_not_found")
        await cur.execute(
            "SELECT g.status,m.status FROM office_project_grant g JOIN jax_project_membership m "
            "ON m.project_id=g.project_id AND m.user_id=g.user_id "
            "WHERE g.project_id=%s AND g.user_id=%s AND g.audience='INTERNAL' FOR UPDATE",
            (project_id, target_user_id),
        )
        membership = await cur.fetchone()
        if membership is None or membership[0] != "ACTIVE" or membership[1] != "ACTIVE":
            raise HTTPException(status_code=409, detail="office_professional_member_required")
        await cur.execute(
            "SELECT professional_grant_id,status,version,basis,evidence_ref FROM office_professional_grant "
            "WHERE project_id=%s AND user_id=%s AND capability='PROFESSIONAL_ACTION' FOR UPDATE",
            (project_id, target_user_id),
        )
        existing = await cur.fetchone()
        if existing is None:
            if payload.expected_version != 0:
                raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
            await cur.execute(
                "INSERT INTO office_professional_grant "
                "(project_id,user_id,capability,basis,evidence_ref,status,granted_by) "
                "VALUES (%s,%s,'PROFESSIONAL_ACTION',%s,%s,'ACTIVE',%s)",
                (project_id, target_user_id, payload.basis, payload.evidence_ref, actor_user_id),
            )
            version, replayed = 1, False
        else:
            _grant_id, status, version, basis, evidence_ref = existing
            version = int(version)
            if (status == "ACTIVE" and basis == payload.basis and evidence_ref == payload.evidence_ref):
                if payload.expected_version not in {0, version}:
                    raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
                replayed = True
            else:
                if payload.expected_version != version:
                    raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
                await cur.execute(
                    "UPDATE office_professional_grant SET basis=%s,evidence_ref=%s,status='ACTIVE',"
                    "granted_by=%s,version=version+1 WHERE project_id=%s AND user_id=%s "
                    "AND capability='PROFESSIONAL_ACTION' AND version=%s",
                    (payload.basis, payload.evidence_ref, actor_user_id, project_id, target_user_id, version),
                )
                if cur.rowcount != 1:
                    raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
                version, replayed = version + 1, False
        decision = await authorize_office_in_cursor(
            cur, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
            require_recent_mfa=True, mfa_proof_event_id=user.mfa_proof_event_id, mfa_config=config,
        )
        if not decision.allowed or decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_professional_grant_denied")
        await record_authorization_event(cur, decision)
    return {"project_id": project_id, "user_id": target_user_id,
            "capability": "PROFESSIONAL_ACTION", "version": version, "replayed": replayed}


@router.delete("/{project_id}/professionals/{target_user_id}")
async def revoke_professional_capability(project_id: int, target_user_id: int,
                                         expected_version: int = Query(ge=1),
                                         user: AuthUser = Depends(get_current_user)):
    """Revoke only the Office professional capability; membership survives."""
    actor_user_id = _actor_id(user)
    if project_id < 1 or target_user_id < 1:
        raise HTTPException(status_code=404, detail="office_professional_grant_not_found")
    config = load_mfa_config_from_environment()
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        decision = await authorize_office_in_cursor(
            cur, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
            require_recent_mfa=True, mfa_proof_event_id=user.mfa_proof_event_id, mfa_config=config,
        )
        if not decision.allowed or decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_professional_grant_denied")
        await cur.execute(
            "SELECT status,version FROM office_professional_grant WHERE project_id=%s AND user_id=%s "
            "AND capability='PROFESSIONAL_ACTION' FOR UPDATE",
            (project_id, target_user_id),
        )
        grant = await cur.fetchone()
        if grant is None:
            raise HTTPException(status_code=404, detail="office_professional_grant_not_found")
        if int(grant[1]) != expected_version:
            raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
        if grant[0] == "REVOKED":
            return {"project_id": project_id, "user_id": target_user_id,
                    "capability": "PROFESSIONAL_ACTION", "version": expected_version, "replayed": True}
        await cur.execute(
            "UPDATE office_professional_grant SET status='REVOKED',version=version+1 "
            "WHERE project_id=%s AND user_id=%s AND capability='PROFESSIONAL_ACTION' AND version=%s",
            (project_id, target_user_id, expected_version),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="office_professional_grant_version_conflict")
        decision = await authorize_office_in_cursor(
            cur, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
            require_recent_mfa=True, mfa_proof_event_id=user.mfa_proof_event_id, mfa_config=config,
        )
        if not decision.allowed or decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_professional_grant_denied")
        await record_authorization_event(cur, decision)
    return {"project_id": project_id, "user_id": target_user_id,
            "capability": "PROFESSIONAL_ACTION", "version": expected_version + 1, "replayed": False}
