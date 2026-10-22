from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.connection import get_pool
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.authority import authorize_office_in_cursor
from virtual_office.capabilities import OfficeOperation
from virtual_office.mfa import load_mfa_config_from_environment
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


async def _profile(user: AuthUser):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT p.global_role,p.status,u.account_class FROM office_account_profile p JOIN jax_users u ON u.user_id=p.user_id WHERE p.user_id=%s", (int(user.user_id),))
            return await cur.fetchone()


@router.get("")
async def list_projects(user: AuthUser = Depends(get_current_user)):
    profile = await _profile(user)
    if profile is None or profile[1] != "ACTIVE":
        raise HTTPException(status_code=403, detail="office_access_denied")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            if profile[2] == "EXTERNAL_CLIENT":
                await cur.execute("SELECT DISTINCT p.id,p.name,NULL,s.tenant_id FROM projects p JOIN jax_project_scope s ON s.project_id=p.id JOIN office_project_grant g ON g.project_id=p.id JOIN office_external_relation r ON r.project_id=g.project_id AND r.user_id=g.user_id JOIN office_recipient_group rg ON rg.recipient_group_id=r.recipient_group_id AND rg.project_id=r.project_id WHERE s.authorization_namespace='OFFICE_CONFIDENTIAL' AND s.status='ACTIVE' AND g.user_id=%s AND g.status='ACTIVE' AND g.audience='CLIENT_PUBLISHED' AND g.grant_role='COLLABORATOR' AND r.status='ACTIVE' AND rg.status='ACTIVE' ORDER BY p.id", (int(user.user_id),))
            elif profile[0] == "OFFICE_SUPERUSER":
                await cur.execute("SELECT p.id,p.name,p.description,s.tenant_id FROM projects p JOIN jax_project_scope s ON s.project_id=p.id WHERE s.authorization_namespace='OFFICE_CONFIDENTIAL' AND s.status='ACTIVE' ORDER BY p.id")
            else:
                await cur.execute("SELECT p.id,p.name,p.description,s.tenant_id FROM projects p JOIN jax_project_scope s ON s.project_id=p.id JOIN office_project_grant g ON g.project_id=p.id WHERE s.authorization_namespace='OFFICE_CONFIDENTIAL' AND s.status='ACTIVE' AND g.user_id=%s AND g.status='ACTIVE' ORDER BY p.id", (int(user.user_id),))
            rows = await cur.fetchall()
    return {"projects": [{"id": int(row[0]), "name": row[1], "description": row[2], "tenant_id": int(row[3])} for row in rows]}


@router.post("")
async def create_project(payload: ProjectCreate, idempotency_key: str = Header(alias="Idempotency-Key"),
                         user: AuthUser = Depends(get_current_user)):
    config = load_mfa_config_from_environment()
    if config is None or user.mfa_proof_event_id is None:
        raise HTTPException(status_code=403, detail="office_mfa_required")
    profile = await _profile(user)
    if profile is None or profile[0] != "OFFICE_SUPERUSER" or profile[1] != "ACTIVE" or profile[2] not in {"INTERNAL", "OFFICE_INTERNAL"}:
        raise HTTPException(status_code=403, detail="office_access_denied")
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
