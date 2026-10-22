from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.connection import get_pool
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.authority import authorize_office_in_cursor
from virtual_office.capabilities import OfficeOperation
from virtual_office.mfa import MFADenied, load_mfa_config_from_environment, reload_current_self_mfa_verified_in_cursor
from virtual_office.namespace_adapter import (
    OfficeNamespaceAuthorizationDenied,
    OfficeNamespaceAuthorizer,
)

router = APIRouter(prefix="/api/virtual-office/projects")

class ProjectCreate(BaseModel):
    tenant_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class _PhaseBRetryable(RuntimeError):
    """The JAX project exists but its Office recipient group is not ready."""


async def _initialize_client_group(*, actor_user_id: int, target_tenant_id: int,
                                   project_id: int, idempotency_key: str,
                                   proof_event_id: int, mfa_config) -> None:
    """Finish the Office-owned phase after JAX committed the canonical project.

    This intentionally has its own transaction.  A failed recipient-group
    initialization leaves the confidential JAX scope inaccessible and is
    repaired only by an authorized replay of the original key.
    """
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            # Discovery is merely a lock-order hint.  All authority rows are
            # read again after their tenant locks have been acquired.
            await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (actor_user_id,))
            actor_row = await cur.fetchone()
            if actor_row is None:
                raise _PhaseBRetryable("office actor disappeared")
            actor_tenant_id = int(actor_row[0])
            for tenant_id in sorted({actor_tenant_id, target_tenant_id}):
                await cur.execute("SELECT tenant_id FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (tenant_id,))
                if await cur.fetchone() is None:
                    raise _PhaseBRetryable("office tenant disappeared")
            await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s FOR UPDATE", (actor_user_id,))
            locked_actor = await cur.fetchone()
            if locked_actor is None or int(locked_actor[0]) != actor_tenant_id:
                raise _PhaseBRetryable("office actor tenant drift")
            await cur.execute("SELECT user_id FROM office_account_profile WHERE user_id=%s FOR UPDATE", (actor_user_id,))
            await cur.fetchone()

            decision = await authorize_office_in_cursor(
                cur, actor_user_id, OfficeOperation.CREATE_OFFICE_PROJECT,
                require_recent_mfa=True, mfa_proof_event_id=proof_event_id,
                mfa_config=mfa_config,
            )
            if not decision.allowed or decision.global_role != "OFFICE_SUPERUSER":
                raise OfficeNamespaceAuthorizationDenied("Office project creation requires an active Office superuser")

            await cur.execute(
                "SELECT tenant_id,authorization_namespace,status FROM jax_project_scope "
                "WHERE project_id=%s FOR UPDATE", (project_id,),
            )
            scope = await cur.fetchone()
            if scope is None or (int(scope[0]), scope[1], scope[2]) != (
                target_tenant_id, "OFFICE_CONFIDENTIAL", "ACTIVE"
            ):
                raise _PhaseBRetryable("office project scope is unavailable")
            await cur.execute(
                "SELECT actor_user_id,target_tenant_id,project_id FROM jax_namespace_project_creation_request "
                "WHERE namespace='OFFICE_CONFIDENTIAL' AND idempotency_key=%s FOR UPDATE",
                (idempotency_key,),
            )
            request = await cur.fetchone()
            if request is None or tuple(map(int, request)) != (actor_user_id, target_tenant_id, project_id):
                raise _PhaseBRetryable("office project creation request is unavailable")

            await cur.execute(
                "SELECT recipient_group_id,tenant_id,status FROM office_recipient_group "
                "WHERE project_id=%s AND group_kind='CLIENT' FOR UPDATE", (project_id,),
            )
            group = await cur.fetchone()
            if group is None:
                await cur.execute(
                    "INSERT INTO office_recipient_group "
                    "(project_id,tenant_id,group_kind,status,created_by) VALUES (%s,%s,'CLIENT','ACTIVE',%s)",
                    (project_id, target_tenant_id, actor_user_id),
                )
            elif int(group[1]) != target_tenant_id:
                raise _PhaseBRetryable("office client group tenant mismatch")
            elif group[2] != "ACTIVE":
                # A revoked group is a durable security state.  A replay is
                # never permission to silently restore it.
                raise HTTPException(status_code=409, detail="office_client_group_revoked")
    except HTTPException:
        raise
    except OfficeNamespaceAuthorizationDenied:
        raise
    except Exception as error:
        raise _PhaseBRetryable("office project initialization failed") from error


async def _active_profile_in_cursor(cur, actor_user_id: int):
    """Establish that this is an active Office identity in the same read.

    Project visibility itself is always decided by the canonical resolver
    below.  This small precondition distinguishes an unprovisioned/revoked
    Office identity (403) from a valid identity with no visible projects ([]).
    """
    await cur.execute(
        "SELECT u.status,t.status,p.status,u.account_class,p.global_role,u.tenant_id FROM jax_users u "
        "JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
        "JOIN office_account_profile p ON p.user_id=u.user_id "
        "WHERE u.user_id=%s",
        (actor_user_id,),
    )
    row = await cur.fetchone()
    if row is None or tuple(row[:3]) != ("active", "active", "ACTIVE"):
        return None
    return {"account_class": row[3], "global_role": row[4], "tenant_id": int(row[5])}


@router.get("")
async def list_projects(
    limit: int = Query(default=50, ge=1, le=100),
    after_id: int | None = Query(default=None, gt=0),
    user: AuthUser = Depends(get_current_user),
):
    """List only scopes approved by the same Office resolver used elsewhere.

    Candidate discovery deliberately knows only the Office namespace.  Every
    candidate is then resolved in one READ COMMITTED transaction with the
    actor/profile, current tenant, membership/grant or external relation, and
    MFA proof reread from the database.  This prevents list-only shortcuts
    from becoming a confidential-project access path.
    """
    try:
        actor_user_id = int(user.user_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="office_access_denied") from error
    config = load_mfa_config_from_environment()
    proof_event_id = user.mfa_proof_event_id
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        profile = await _active_profile_in_cursor(cur, actor_user_id)
        if profile is None:
            raise HTTPException(status_code=403, detail="office_access_denied")
        proof_valid = False
        if type(proof_event_id) is int and proof_event_id > 0:
            proof_valid = not isinstance(
                await reload_current_self_mfa_verified_in_cursor(
                    cur, actor_user_id=actor_user_id, proof_event_id=proof_event_id,
                ),
                MFADenied,
            )
        predicate = " AND p.id>%s" if after_id is not None else ""
        cursor_params: tuple[object, ...] = () if after_id is None else (after_id,)
        if profile["global_role"] == "OFFICE_SUPERUSER":
            if not proof_valid:
                return {"projects": [], "next_after_id": None}
            query = (
                "SELECT p.id,p.name,p.description,s.tenant_id,NULL FROM projects p "
                "JOIN jax_project_scope s ON s.project_id=p.id "
                "WHERE s.authorization_namespace='OFFICE_CONFIDENTIAL' AND s.status='ACTIVE'"
            )
            params = cursor_params + (limit + 1,)
        elif profile["account_class"] in {"INTERNAL", "OFFICE_INTERNAL"}:
            query = (
                "SELECT p.id,p.name,p.description,s.tenant_id,NULL FROM office_project_grant g "
                "STRAIGHT_JOIN projects p ON p.id=g.project_id "
                "STRAIGHT_JOIN jax_project_scope s ON s.project_id=p.id "
                "STRAIGHT_JOIN jax_project_membership m ON m.project_id=p.id AND m.user_id=%s "
                "WHERE s.authorization_namespace='OFFICE_CONFIDENTIAL' AND s.status='ACTIVE' "
                "AND g.user_id=%s AND s.tenant_id=%s AND g.status='ACTIVE' AND g.audience='INTERNAL' "
                "AND g.grant_role IN ('COLLABORATOR','PROJECT_ADMIN') AND m.status='ACTIVE'"
            )
            if not proof_valid:
                query += (
                    " AND g.grant_role='COLLABORATOR' AND NOT EXISTS (SELECT 1 FROM office_professional_grant pg "
                    "WHERE pg.project_id=p.id AND pg.user_id=%s AND pg.capability='PROFESSIONAL_ACTION' "
                    "AND pg.status='ACTIVE')"
                )
            params = (actor_user_id, actor_user_id, profile["tenant_id"], *( () if proof_valid else (actor_user_id,) ), *cursor_params, limit + 1)
        else:
            query = (
                "SELECT p.id,p.name,p.description,s.tenant_id,r.recipient_group_id FROM office_external_relation r FORCE INDEX (idx_office_external_relation_list) "
                "STRAIGHT_JOIN projects p ON p.id=r.project_id "
                "STRAIGHT_JOIN jax_project_scope s ON s.project_id=p.id "
                "STRAIGHT_JOIN office_project_grant g ON g.project_id=p.id AND g.user_id=%s "
                "STRAIGHT_JOIN office_recipient_group rg ON rg.recipient_group_id=r.recipient_group_id AND rg.project_id=p.id "
                "WHERE s.authorization_namespace='OFFICE_CONFIDENTIAL' AND s.status='ACTIVE' "
                "AND g.status='ACTIVE' AND g.audience='CLIENT_PUBLISHED' AND g.grant_role='COLLABORATOR' "
                "AND r.user_id=%s AND r.status='ACTIVE' AND rg.status='ACTIVE'"
            )
            if not proof_valid:
                query += (
                    " AND NOT EXISTS (SELECT 1 FROM office_professional_grant pg "
                    "WHERE pg.project_id=p.id AND pg.user_id=%s AND pg.capability='PROFESSIONAL_ACTION' "
                    "AND pg.status='ACTIVE')"
                )
            params = (actor_user_id, actor_user_id, *( () if proof_valid else (actor_user_id,) ), *cursor_params, limit + 1)
        await cur.execute(query + predicate + " ORDER BY p.id LIMIT %s", params)
        candidates = await cur.fetchall()
        visible = []
        for project_id, name, description, tenant_id, recipient_group_id in candidates:
            project_id = int(project_id)
            decision = await authorize_office_in_cursor(
                cur, actor_user_id, OfficeOperation.VIEW_PROJECT, project_id,
                recipient_group_id=None if recipient_group_id is None else int(recipient_group_id),
                mfa_proof_event_id=proof_event_id, mfa_config=config,
            )
            if not decision.allowed:
                # Candidate SQL and the central resolver must agree while the
                # actor/proof lock is held.  A mismatch is a race or schema
                # invariant break, never a partially disclosed page.
                raise HTTPException(status_code=503, detail="office_project_list_retryable")
            visible.append({
                "id": project_id, "name": name,
                "description": None if decision.audience == "CLIENT_PUBLISHED" else description,
                "tenant_id": int(tenant_id),
            })
        # For non-roots the candidate predicates are exactly the resolver's
        # active-view prerequisites (the resolver remains the authoritative
        # final decision).  Thus a bounded candidate page cannot be consumed
        # by hidden rows before a permitted one.  The sole root exception is
        # a stale/missing MFA proof, where returning no continuation is the
        # intended fail-closed result.
        # The cursor contains only a project already returned to this caller.
        # It never advances over a hidden lookahead candidate, so it cannot be
        # used to enumerate confidential IDs.  A final full page may yield an
        # empty next page; that is a bounded, harmless continuation.
        visible = visible[:limit]
        next_after_id = visible[-1]["id"] if len(visible) == limit else None
    return {"projects": visible, "next_after_id": next_after_id}


@router.post("")
async def create_project(payload: ProjectCreate, idempotency_key: str = Header(alias="Idempotency-Key"),
                         user: AuthUser = Depends(get_current_user)):
    config = load_mfa_config_from_environment()
    if config is None or user.mfa_proof_event_id is None:
        raise HTTPException(status_code=403, detail="office_mfa_required")
    from jax.memory.b9_mariadb import MariaDBB9Store
    from jax.memory.project_authority import (
        AuthorizationDenied,
        IdempotencyKeyConflict,
        ProjectAuthorityAdmin,
        ProjectAuthorityError,
        ProjectNotVisible,
        ProjectRoleInsufficient,
        ProjectAuthorityRetryable,
        ProjectNamespace,
        ScopeDenied,
    )
    try:
        result = await ProjectAuthorityAdmin(MariaDBB9Store(await get_pool())).create_namespaced_project(
            actor_user_id=int(user.user_id), target_tenant_id=payload.tenant_id,
            namespace=ProjectNamespace.OFFICE_CONFIDENTIAL, name=payload.name,
            description=payload.description, idempotency_key=idempotency_key,
            authorizer=OfficeNamespaceAuthorizer(mfa_proof_event_id=user.mfa_proof_event_id, mfa_config=config),
        )
    except ProjectAuthorityRetryable as error:
        raise HTTPException(status_code=503, detail="office_project_create_retryable") from error
    except IdempotencyKeyConflict as error:
        raise HTTPException(status_code=409, detail="office_project_idempotency_conflict") from error
    except (OfficeNamespaceAuthorizationDenied, AuthorizationDenied, ScopeDenied,
            ProjectNotVisible, ProjectRoleInsufficient) as error:
        raise HTTPException(status_code=403, detail="office_project_create_denied") from error
    except ProjectAuthorityError as error:
        raise HTTPException(status_code=503, detail="office_project_create_retryable") from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="office_project_create_retryable") from error
    try:
        await _initialize_client_group(
            actor_user_id=int(user.user_id), target_tenant_id=payload.tenant_id,
            project_id=result.project_id, idempotency_key=idempotency_key,
            proof_event_id=user.mfa_proof_event_id, mfa_config=config,
        )
    except _PhaseBRetryable as error:
        raise HTTPException(status_code=503, detail="office_project_initialization_retryable") from error
    except OfficeNamespaceAuthorizationDenied as error:
        raise HTTPException(status_code=403, detail="office_project_create_denied") from error
    return {"id": result.project_id, "created": result.created}
