"""Office-only directory and global-role administration.

This surface deliberately operates only on an existing permanent Office
profile.  Creating an identity or binding an unprofiled canonical user has a
separate own-credential proof contract and remains in the invitation/bootstrap
slice; a directory route must never become an account-enablement shortcut.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.audit import record_authorization_event
from virtual_office.authority import authorize_office_in_cursor
from virtual_office.capabilities import OfficeOperation
from virtual_office.mfa import load_mfa_config_from_environment


router = APIRouter(prefix="/api/virtual-office/people")
_ROLES = Literal["COLLABORATOR", "GENERAL_ADMIN", "OFFICE_SUPERUSER"]


class GlobalRoleUpdate(BaseModel):
    global_role: _ROLES
    expected_version: int = Field(ge=1)


class DelegationUpdate(BaseModel):
    expected_version: int = Field(ge=0)


def _actor_id(user: AuthUser) -> int:
    try:
        return int(user.user_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="office_people_access_denied") from error


async def _current_admin(cur, *, actor_user_id: int, proof_event_id: int | None):
    decision = await authorize_office_in_cursor(
        cur, actor_user_id, OfficeOperation.ADMINISTER_OFFICE,
        require_recent_mfa=True, mfa_proof_event_id=proof_event_id,
        mfa_config=load_mfa_config_from_environment(),
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail="office_people_access_denied")
    return decision


async def _lock_actor_target_tenants(cur, *, actor_user_id: int, target_user_id: int) -> int:
    """Take the shared tenant mutexes before actor/profile then target rows."""
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (actor_user_id,))
    actor = await cur.fetchone()
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (target_user_id,))
    target = await cur.fetchone()
    if actor is None or target is None:
        raise HTTPException(status_code=404, detail="office_delegation_target_not_found")
    target_tenant_id = int(target[0])
    for tenant_id in sorted({int(actor[0]), target_tenant_id}):
        await cur.execute("SELECT tenant_id FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (tenant_id,))
        if await cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="office_delegation_target_not_found")
    return target_tenant_id


@router.get("")
async def list_people(
    tenant_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
    after_user_id: int | None = Query(default=None, gt=0),
    user: AuthUser = Depends(get_current_user),
):
    """Return directory metadata, never credentials, tokens, or audit detail."""
    actor_user_id = _actor_id(user)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        # A delegated general admin remains tenant scoped.  Root can cross
        # Office tenants, but only has access to active canonical profiles.
        if decision.global_role != "OFFICE_SUPERUSER":
            if tenant_id is not None and tenant_id != decision.tenant_id:
                raise HTTPException(status_code=403, detail="office_people_access_denied")
            target_tenant_id = decision.tenant_id
        else:
            target_tenant_id = tenant_id
        sql = (
            "SELECT u.user_id,u.tenant_id,u.email,u.account_class,p.global_role,p.status,p.version "
            "FROM office_account_profile p JOIN jax_users u ON u.user_id=p.user_id "
            "JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
            "WHERE u.status='active' AND t.status='active'"
        )
        params: list[object] = []
        if target_tenant_id is not None:
            sql += " AND u.tenant_id=%s"
            params.append(target_tenant_id)
        if after_user_id is not None:
            sql += " AND u.user_id>%s"
            params.append(after_user_id)
        sql += " ORDER BY u.user_id LIMIT %s"
        params.append(limit + 1)
        await cur.execute(sql, tuple(params))
        rows = await cur.fetchall()
    output = [
        {"user_id": int(row[0]), "tenant_id": int(row[1]), "email": row[2],
         "account_class": row[3], "global_role": row[4],
         "status": row[5], "version": int(row[6])}
        for row in rows[:limit]
    ]
    return {"people": output, "next_after_user_id": output[-1]["user_id"] if len(rows) > limit else None}


@router.put("/{target_user_id}/global-role")
async def update_global_role(target_user_id: int, payload: GlobalRoleUpdate,
                             user: AuthUser = Depends(get_current_user)):
    """Root-only mutation of an existing Office profile.

    The profile is permanent; this endpoint changes its role/version but does
    not create, delete, reactivate, or convert a canonical identity.
    """
    actor_user_id = _actor_id(user)
    if target_user_id < 1:
        raise HTTPException(status_code=404, detail="office_person_not_found")
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        # Discover tenant ids only as lock-order hints, then lock every row
        # and re-read the target below.  This matches the 3C shared order.
        await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (actor_user_id,))
        actor_hint = await cur.fetchone()
        await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (target_user_id,))
        target_hint = await cur.fetchone()
        if actor_hint is None or target_hint is None:
            raise HTTPException(status_code=404, detail="office_person_not_found")
        for tenant_id in sorted({int(actor_hint[0]), int(target_hint[0])}):
            await cur.execute("SELECT tenant_id FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (tenant_id,))
            if await cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="office_person_not_found")
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_global_role_denied")
        await cur.execute(
            "SELECT u.tenant_id,u.status,t.status,p.global_role,p.status,p.version "
            "FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
            "LEFT JOIN office_account_profile p ON p.user_id=u.user_id "
            "WHERE u.user_id=%s FOR UPDATE",
            (target_user_id,),
        )
        target = await cur.fetchone()
        if (target is None or target[1] != "active" or target[2] != "active"
                or target[3] is None or target[4] != "ACTIVE"):
            raise HTTPException(status_code=404, detail="office_person_not_found")
        if int(target[5]) != payload.expected_version:
            raise HTTPException(status_code=409, detail="office_profile_version_conflict")
        # Removing an Office root is the separate recovery/continuity
        # ceremony, not an ordinary profile edit.  In particular, two roots
        # must never be able to concurrently demote each other and leave no
        # authority capable of performing the recovery contract.
        if target[3] == "OFFICE_SUPERUSER" and payload.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=409, detail="office_root_demotion_requires_recovery")
        # Reread current authority after the target profile lock.  A revoked
        # root can never use a decision taken before this write point.
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_global_role_denied")
        await cur.execute(
            "UPDATE office_account_profile SET global_role=%s,version=version+1,updated_at=UTC_TIMESTAMP(6) "
            "WHERE user_id=%s AND version=%s AND status='ACTIVE'",
            (payload.global_role, target_user_id, payload.expected_version),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="office_profile_version_conflict")
        await record_authorization_event(cur, decision)
    return {"user_id": target_user_id, "global_role": payload.global_role,
            "version": payload.expected_version + 1}


@router.put("/{target_user_id}/delegation")
async def grant_tenant_administration(target_user_id: int, payload: DelegationUpdate,
                                      user: AuthUser = Depends(get_current_user)):
    """Grant tenant-scoped general administration to an existing GENERAL_ADMIN."""
    actor_user_id = _actor_id(user)
    config = load_mfa_config_from_environment()
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        target_tenant_hint = await _lock_actor_target_tenants(
            cur, actor_user_id=actor_user_id, target_user_id=target_user_id)
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_delegation_denied")
        await cur.execute(
            "SELECT u.tenant_id,u.status,t.status,p.global_role,p.status FROM jax_users u "
            "JOIN jax_tenants t ON t.tenant_id=u.tenant_id JOIN office_account_profile p ON p.user_id=u.user_id "
            "WHERE u.user_id=%s FOR UPDATE", (target_user_id,))
        target = await cur.fetchone()
        if target is None or target[1] != "active" or target[2] != "active" or target[3] != "GENERAL_ADMIN" or target[4] != "ACTIVE":
            raise HTTPException(status_code=404, detail="office_delegation_target_not_found")
        tenant_id = int(target[0])
        if tenant_id != target_tenant_hint:
            raise HTTPException(status_code=409, detail="office_delegation_target_drift")
        await cur.execute("SELECT delegation_id,status,version FROM office_admin_delegation WHERE tenant_id=%s AND user_id=%s AND capability='ADMINISTER_OFFICE' FOR UPDATE", (tenant_id, target_user_id))
        existing = await cur.fetchone()
        if existing is None:
            if payload.expected_version != 0:
                raise HTTPException(status_code=409, detail="office_delegation_version_conflict")
            await cur.execute("INSERT INTO office_admin_delegation (tenant_id,user_id,capability,status,granted_by) VALUES (%s,%s,'ADMINISTER_OFFICE','ACTIVE',%s)", (tenant_id, target_user_id, actor_user_id))
            version, replayed = 1, False
        else:
            status, version = existing[1], int(existing[2])
            if status == "ACTIVE":
                if payload.expected_version not in {0, version}:
                    raise HTTPException(status_code=409, detail="office_delegation_version_conflict")
                replayed = True
            else:
                if payload.expected_version != version:
                    raise HTTPException(status_code=409, detail="office_delegation_version_conflict")
                await cur.execute("UPDATE office_admin_delegation SET status='ACTIVE',granted_by=%s,version=version+1 WHERE tenant_id=%s AND user_id=%s AND capability='ADMINISTER_OFFICE' AND version=%s", (actor_user_id, tenant_id, target_user_id, version))
                version, replayed = version + 1, False
        decision = await _current_admin(cur, actor_user_id=actor_user_id, proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_delegation_denied")
        await record_authorization_event(cur, decision)
    return {"user_id": target_user_id, "tenant_id": tenant_id, "capability": "ADMINISTER_OFFICE", "version": version, "replayed": replayed}


@router.delete("/{target_user_id}/delegation")
async def revoke_tenant_administration(target_user_id: int, expected_version: int = Query(ge=1),
                                       user: AuthUser = Depends(get_current_user)):
    actor_user_id = _actor_id(user)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        target_tenant_hint = await _lock_actor_target_tenants(
            cur, actor_user_id=actor_user_id, target_user_id=target_user_id)
        decision = await _current_admin(cur, actor_user_id=actor_user_id,
                                        proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_delegation_denied")
        await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s FOR UPDATE", (target_user_id,))
        target = await cur.fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="office_delegation_target_not_found")
        tenant_id = int(target[0])
        if tenant_id != target_tenant_hint:
            raise HTTPException(status_code=409, detail="office_delegation_target_drift")
        await cur.execute("SELECT status,version FROM office_admin_delegation WHERE tenant_id=%s AND user_id=%s AND capability='ADMINISTER_OFFICE' FOR UPDATE", (tenant_id, target_user_id))
        delegation = await cur.fetchone()
        if delegation is None:
            raise HTTPException(status_code=404, detail="office_delegation_not_found")
        if int(delegation[1]) != expected_version:
            raise HTTPException(status_code=409, detail="office_delegation_version_conflict")
        if delegation[0] == "REVOKED":
            return {"user_id": target_user_id, "tenant_id": tenant_id, "capability": "ADMINISTER_OFFICE", "version": expected_version, "replayed": True}
        await cur.execute("UPDATE office_admin_delegation SET status='REVOKED',version=version+1 WHERE tenant_id=%s AND user_id=%s AND capability='ADMINISTER_OFFICE' AND version=%s", (tenant_id, target_user_id, expected_version))
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="office_delegation_version_conflict")
        decision = await _current_admin(cur, actor_user_id=actor_user_id, proof_event_id=user.mfa_proof_event_id)
        if decision.global_role != "OFFICE_SUPERUSER":
            raise HTTPException(status_code=403, detail="office_delegation_denied")
        await record_authorization_event(cur, decision)
    return {"user_id": target_user_id, "tenant_id": tenant_id, "capability": "ADMINISTER_OFFICE", "version": expected_version + 1, "replayed": False}
