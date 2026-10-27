from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from pymysql.err import OperationalError

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.connection import get_pool
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.audit import record_authorization_event
from virtual_office.authority import authorize_office_in_cursor
from virtual_office.capabilities import OfficeOperation
from virtual_office.invitations import (
    InvitationError, can_manage_invitations_in_cursor,
    lock_office_actor_project_tenants_in_cursor,
)
from virtual_office.mfa import MFADenied, load_mfa_config_from_environment, reload_current_self_mfa_verified_in_cursor
from virtual_office.namespace_adapter import (
    OfficeNamespaceAuthorizationDenied,
    OfficeNamespaceAuthorizer,
)
from virtual_office.verticales import vertical_activa, vertical_registrada

router = APIRouter(prefix="/api/virtual-office/projects")

class ProjectCreate(BaseModel):
    tenant_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=255, pattern=r".*\S.*")
    description: str | None = Field(default=None, max_length=2000)
    vertical: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,32}$")


class ProjectMetadataUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255, pattern=r".*\S.*")
    description: str | None = Field(default=None, max_length=2000)


class _OfficeProjectMetadataAuthorizer:
    def __init__(self, *, proof_event_id: int | None, mfa_config):
        self._proof_event_id = proof_event_id
        self._mfa_config = mfa_config

    async def authorize_in_transaction(self, cursor, *, actor_user_id: int,
                                       target_tenant_id: int, project_id: int,
                                       namespace, operation: str) -> None:
        from jax.memory.project_authority import AuthorizationDenied, ProjectNamespace
        if namespace is not ProjectNamespace.OFFICE_CONFIDENTIAL or operation != "UPDATE_OFFICE_PROJECT_METADATA":
            raise AuthorizationDenied("Office metadata authority context is invalid")
        decision = await authorize_office_in_cursor(
            cursor, actor_user_id, OfficeOperation.MANAGE_PROJECT, project_id,
        )
        if not decision.allowed or decision.tenant_id != target_tenant_id:
            raise AuthorizationDenied("Office project metadata authority is required")


class _PhaseBRetryable(RuntimeError):
    """The JAX project exists but its Office recipient group is not ready."""


async def _initialize_client_group(*, actor_user_id: int, target_tenant_id: int,
                                   project_id: int, idempotency_key: str,
                                   vertical: str | None = None) -> None:
    """Finish the Office-owned phase after JAX committed the canonical project.

    This intentionally has its own transaction.  A failed companion phase
    leaves the confidential JAX scope inaccessible and is repaired only by an
    authorized replay of the original key.  The creator's canonical JAX
    membership, Office grant, receipt, and client group commit together.
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
            )
            if (not decision.allowed or decision.global_role != "OFFICE_SUPERUSER"
                    or decision.tenant_id != target_tenant_id):
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

            # This is the one narrowly-defined self-grant: it binds the
            # creator to the scope that the same canonical CREATE request has
            # just created.  JAX validates that exception under these locks;
            # JXP supplies the current Office-root authority and records the
            # Office companion in this same Phase-B transaction.
            from api.virtual_office.grants import (
                _OfficeMembershipAuthorizer,
                _assert_receipt_companion,
                _locked_membership_receipt,
                _membership_receipt_digest,
                _write_membership_receipt,
            )
            from jax.memory.b9_mariadb import MariaDBB9Store
            from jax.memory.project_authority import (
                NamespacedMemberKind,
                ProjectAuthorityAdmin,
                ProjectNamespace,
            )

            result = await ProjectAuthorityAdmin(MariaDBB9Store(await get_pool())).grant_namespaced_member_in_transaction(
                cur, actor_user_id=actor_user_id, target_tenant_id=target_tenant_id,
                project_id=project_id, target_user_id=actor_user_id,
                namespace=ProjectNamespace.OFFICE_CONFIDENTIAL,
                member_kind=NamespacedMemberKind.INTERNAL,
                idempotency_key=idempotency_key,
                execution=None,
                authorizer=_OfficeMembershipAuthorizer(
                    proof_event_id=None, mfa_config=None, grant_role="PROJECT_ADMIN"),
            )
            digest = _membership_receipt_digest(
                operation="GRANT_OFFICE_MEMBER", actor_user_id=actor_user_id,
                target_tenant_id=target_tenant_id, project_id=project_id,
                target_user_id=actor_user_id, expected_version=0,
                grant_role="PROJECT_ADMIN", audience="INTERNAL",
            )
            receipt = await _locked_membership_receipt(cur, idempotency_key=idempotency_key)
            if receipt is not None:
                if (not result.replayed or receipt[0] != digest
                        or str(receipt[1]) != result.membership_id
                        or str(receipt[2]) != result.event_id):
                    raise _PhaseBRetryable("office creator grant replay is unavailable")
                await _assert_receipt_companion(
                    cur, project_id=project_id, target_user_id=actor_user_id,
                    receipt=receipt)
            else:
                if result.replayed:
                    # A canonical replay without JXP's terminal receipt is a
                    # torn or incompatible companion, never a license to
                    # manufacture missing authorization evidence.
                    raise _PhaseBRetryable("office creator grant receipt is unavailable")
                await cur.execute(
                    "SELECT grant_role,audience,status,version FROM office_project_grant "
                    "WHERE project_id=%s AND user_id=%s FOR UPDATE",
                    (project_id, actor_user_id),
                )
                if await cur.fetchone() is not None:
                    raise _PhaseBRetryable("office creator grant is unavailable")
                await cur.execute(
                    "INSERT INTO office_project_grant "
                    "(project_id,user_id,grant_role,audience,status,granted_by) "
                    "VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
                    (project_id, actor_user_id, actor_user_id),
                )
                authorization_event_id = await record_authorization_event(cur, decision)
                await _write_membership_receipt(
                    cur, idempotency_key=idempotency_key, digest=digest,
                    operation="GRANT_OFFICE_MEMBER", actor_user_id=actor_user_id,
                    target_tenant_id=target_tenant_id, project_id=project_id,
                    target_user_id=actor_user_id, expected_version=0,
                    grant_role="PROJECT_ADMIN", audience="INTERNAL", result=result,
                    office_grant_version=1, office_grant_status="ACTIVE",
                    authorization_event_id=authorization_event_id,
                )

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
            if vertical is not None:
                # Same Phase-B transaction as the grant and client group: the
                # companion commits with them or is repaired by the same
                # authorized key replay.  A divergent existing row is a torn
                # or incompatible companion, never silently rewritten.
                await cur.execute(
                    "SELECT vertical FROM office_project_vertical "
                    "WHERE project_id=%s FOR UPDATE", (project_id,),
                )
                vertical_row = await cur.fetchone()
                if vertical_row is None:
                    await cur.execute(
                        "INSERT INTO office_project_vertical "
                        "(project_id,vertical,created_by) VALUES (%s,%s,%s)",
                        (project_id, vertical, actor_user_id),
                    )
                elif vertical_row[0] != vertical:
                    raise _PhaseBRetryable("office project vertical drift")
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
    vertical: str | None = Query(default=None, max_length=32, pattern=r"^[a-z0-9_-]{1,32}$"),
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
        if vertical is not None and not vertical_registrada(vertical):
            # The room catalogue is server-owned; an unknown id is a client
            # bug, not an empty page.
            raise HTTPException(status_code=422, detail="office_vertical_invalid")
        # The candidate filter may treat a row as MFA-eligible only when the
        # resolver can apply the same verified-proof contract.  A durable
        # proof without its runtime configuration must fail closed here,
        # rather than producing a permanent resolver mismatch/503.
        proof_valid = config is not None and type(proof_event_id) is int and proof_event_id > 0
        if proof_valid:
            proof_valid = not isinstance(
                await reload_current_self_mfa_verified_in_cursor(
                    cur, actor_user_id=actor_user_id, proof_event_id=proof_event_id,
                ),
                MFADenied,
            )
        predicate = ""
        predicate_params: tuple[object, ...] = ()
        if after_id is not None:
            predicate += " AND p.id>%s"
            predicate_params += (after_id,)
        if vertical is not None:
            predicate += " AND v.vertical=%s"
            predicate_params += (vertical,)
        if profile["global_role"] == "GENERAL_ADMIN":
            # General administration remains MFA-bound.  An Office root can
            # bootstrap and manage its Office projects after normal sign-in.
            if not proof_valid:
                return {"projects": [], "next_after_id": None}
        if profile["global_role"] == "OFFICE_SUPERUSER":
            query = (
                "SELECT p.id,p.name,p.description,s.tenant_id,NULL,v.vertical FROM projects p "
                "JOIN jax_project_scope s ON s.project_id=p.id "
                "LEFT JOIN office_project_vertical v ON v.project_id=p.id "
                "WHERE s.authorization_namespace='OFFICE_CONFIDENTIAL' AND s.status='ACTIVE'"
            )
            params = predicate_params + (limit + 1,)
        elif profile["account_class"] in {"INTERNAL", "OFFICE_INTERNAL"}:
            query = (
                "SELECT p.id,p.name,p.description,s.tenant_id,NULL,v.vertical FROM office_project_grant g "
                "STRAIGHT_JOIN projects p ON p.id=g.project_id "
                "STRAIGHT_JOIN jax_project_scope s ON s.project_id=p.id "
                "STRAIGHT_JOIN jax_project_membership m ON m.project_id=p.id AND m.user_id=%s "
                "LEFT JOIN office_project_vertical v ON v.project_id=p.id "
                "WHERE s.authorization_namespace='OFFICE_CONFIDENTIAL' AND s.status='ACTIVE' "
                "AND g.user_id=%s AND s.tenant_id=%s AND g.status='ACTIVE' AND g.audience='INTERNAL' "
                "AND g.grant_role IN ('COLLABORATOR','PROJECT_ADMIN') AND m.status='ACTIVE'"
            )
            if not proof_valid:
                query += (
                    " AND NOT EXISTS (SELECT 1 FROM office_professional_grant pg "
                    "WHERE pg.project_id=p.id AND pg.user_id=%s AND pg.capability='PROFESSIONAL_ACTION' "
                    "AND pg.status='ACTIVE')"
                )
            params = (actor_user_id, actor_user_id, profile["tenant_id"], *( () if proof_valid else (actor_user_id,) ), *predicate_params, limit + 1)
        else:
            query = (
                "SELECT p.id,p.name,p.description,s.tenant_id,r.recipient_group_id,v.vertical FROM office_external_relation r FORCE INDEX (idx_office_external_relation_list) "
                "STRAIGHT_JOIN projects p ON p.id=r.project_id "
                "STRAIGHT_JOIN jax_project_scope s ON s.project_id=p.id "
                "STRAIGHT_JOIN office_project_grant g ON g.project_id=p.id AND g.user_id=%s "
                "STRAIGHT_JOIN office_recipient_group rg ON rg.recipient_group_id=r.recipient_group_id AND rg.project_id=p.id "
                "LEFT JOIN office_project_vertical v ON v.project_id=p.id "
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
            params = (actor_user_id, actor_user_id, *( () if proof_valid else (actor_user_id,) ), *predicate_params, limit + 1)
        await cur.execute(query + predicate + " ORDER BY p.id LIMIT %s", params)
        candidates = await cur.fetchall()
        visible = []
        for project_id, name, description, tenant_id, recipient_group_id, project_vertical in candidates:
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
                "vertical": None if project_vertical is None else str(project_vertical),
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


async def _project_detail_in_cursor(cur, *, actor_user_id: int, project_id: int,
                                    proof_event_id: int | None, mfa_config) -> dict:
    """Return a project only after the canonical Office resolver approves it.

    The row is locked for mutation callers.  Reads use the same resolver and
    intentionally omit confidential metadata for a published-client grant.
    """
    # Share the tenant → actor → scope prefix with invitation issuance and
    # listing before either resolver can lock actor/scope rows.  Both later
    # authorizations re-read all authority facts in this same transaction.
    await lock_office_actor_project_tenants_in_cursor(
        cur, actor_user_id=actor_user_id, project_id=project_id,
    )
    await cur.execute(
        "SELECT p.id,p.name,p.description,s.tenant_id,s.version,v.vertical FROM projects p "
        "JOIN jax_project_scope s ON s.project_id=p.id "
        "LEFT JOIN office_project_vertical v ON v.project_id=p.id WHERE p.id=%s",
        (project_id,),
    )
    row = await cur.fetchone()
    # The recipient group is relevant only for external callers.  Resolve it
    # from the server-owned relationship; callers never submit it as a query
    # parameter capable of selecting another client's publication.
    await cur.execute(
        "SELECT recipient_group_id FROM office_external_relation "
        "WHERE project_id=%s AND user_id=%s AND status='ACTIVE'",
        (project_id, actor_user_id),
    )
    relation = await cur.fetchone()
    decision = await authorize_office_in_cursor(
        cur, actor_user_id, OfficeOperation.VIEW_PROJECT, project_id,
        recipient_group_id=None if relation is None else int(relation[0]),
        mfa_proof_event_id=proof_event_id, mfa_config=mfa_config,
    )
    if row is None or not decision.allowed:
        # Keep absent, inactive, non-Office and inaccessible scopes
        # indistinguishable.  It runs even for an absent scope, preserving
        # the canonical authority/audit path for every request.
        raise HTTPException(status_code=404, detail="office_project_not_found")
    try:
        manage_invitations = await can_manage_invitations_in_cursor(
            cur, actor_user_id=actor_user_id, project_id=project_id,
            proof_event_id=proof_event_id, mfa_config=mfa_config,
        )
    except InvitationError as error:
        # A normal authorization/MFA denial has already become ``False`` in
        # the helper.  Any remaining domain error is incomplete operational
        # state, never a capability that the browser may safely infer.
        raise HTTPException(status_code=503, detail="office_invitation_unavailable") from error
    return {
        "id": int(row[0]), "name": row[1],
        "description": None if decision.audience == "CLIENT_PUBLISHED" else row[2],
        "tenant_id": int(row[3]), "scope_version": int(row[4]),
        "vertical": None if row[5] is None else str(row[5]),
        "audience": decision.audience or "INTERNAL",
        "capabilities": {"manage_invitations": manage_invitations},
    }


@router.get("/{project_id}")
async def get_project(project_id: int, user: AuthUser = Depends(get_current_user)):
    try:
        actor_user_id = int(user.user_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="office_project_access_denied") from error
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            return await _project_detail_in_cursor(
                cur, actor_user_id=actor_user_id, project_id=project_id,
                proof_event_id=user.mfa_proof_event_id,
                mfa_config=load_mfa_config_from_environment(),
            )
    except OperationalError as error:
        raise HTTPException(status_code=503, detail="office_invitation_unavailable") from error


@router.put("/{project_id}")
async def update_project_metadata(project_id: int, payload: ProjectMetadataUpdate,
                                  user: AuthUser = Depends(get_current_user)):
    """Use JAX's canonical Office metadata hook; JXP never updates projects directly."""
    try:
        actor_user_id = int(user.user_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="office_project_update_denied") from error
    if project_id < 1:
        raise HTTPException(status_code=404, detail="office_project_update_not_found")
    config = load_mfa_config_from_environment()
    from jax.memory.b9_mariadb import MariaDBB9Store
    from jax.memory.project_authority import (
        AuthorizationDenied, ProjectAuthorityAdmin, ProjectAuthorityError,
        ProjectAuthorityRetryable, ProjectNamespace, ProjectNotVisible,
    )
    try:
        # Resolve the Office operation first.  Reading the scope for a tenant
        # hint before this gate would disclose whether a guessed id is absent
        # or merely belongs to Classic.  JAX still owns the mutation's
        # canonical locks and repeats this authorization in its callback.
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            decision = await authorize_office_in_cursor(
                cur, actor_user_id, OfficeOperation.MANAGE_PROJECT, project_id,
            )
            if not decision.allowed or decision.tenant_id is None:
                raise HTTPException(status_code=403, detail="office_project_update_denied")
            target_tenant_id = int(decision.tenant_id)
        result = await ProjectAuthorityAdmin(MariaDBB9Store(await get_pool())).update_namespaced_project_metadata(
            actor_user_id=actor_user_id, target_tenant_id=target_tenant_id, project_id=project_id,
            namespace=ProjectNamespace.OFFICE_CONFIDENTIAL, name=payload.name,
            description=payload.description,
            authorizer=_OfficeProjectMetadataAuthorizer(
                proof_event_id=user.mfa_proof_event_id, mfa_config=config),
        )
    except HTTPException:
        raise
    except (AuthorizationDenied, ProjectNotVisible) as error:
        raise HTTPException(status_code=403, detail="office_project_update_denied") from error
    except ProjectAuthorityRetryable as error:
        raise HTTPException(status_code=503, detail="office_project_update_retryable") from error
    except ProjectAuthorityError as error:
        raise HTTPException(status_code=503, detail="office_project_update_retryable") from error
    return {"id": result.project_id, "name": result.name,
            "description": result.description, "changed": result.changed}


@router.post("")
async def create_project(payload: ProjectCreate, idempotency_key: str = Header(alias="Idempotency-Key"),
                         user: AuthUser = Depends(get_current_user)):
    from jax.memory.b9_mariadb import MariaDBB9Store
    from jax.memory.project_authority import (
        AuthorizationDenied,
        InvalidIdempotencyKey,
        IdempotencyKeyConflict,
        ProjectAuthorityAdmin,
        ProjectAuthorityError,
        ProjectNotVisible,
        ProjectRoleInsufficient,
        ProjectAuthorityRetryable,
        ProjectNamespace,
        ScopeDenied,
    )
    if payload.vertical is not None and not vertical_activa(payload.vertical):
        # Only an active room accepts projects; the registry state is a
        # server decision, not a client choice.
        raise HTTPException(status_code=422, detail="office_project_vertical_invalid")
    try:
        result = await ProjectAuthorityAdmin(MariaDBB9Store(await get_pool())).create_namespaced_project(
            actor_user_id=int(user.user_id), target_tenant_id=payload.tenant_id,
            namespace=ProjectNamespace.OFFICE_CONFIDENTIAL, name=payload.name,
            description=payload.description, idempotency_key=idempotency_key,
            authorizer=OfficeNamespaceAuthorizer(),
        )
    except ProjectAuthorityRetryable as error:
        raise HTTPException(status_code=503, detail="office_project_create_retryable") from error
    except IdempotencyKeyConflict as error:
        raise HTTPException(status_code=409, detail="office_project_idempotency_conflict") from error
    except InvalidIdempotencyKey as error:
        raise HTTPException(status_code=422, detail="office_project_idempotency_invalid") from error
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
            vertical=payload.vertical,
        )
    except _PhaseBRetryable as error:
        raise HTTPException(status_code=503, detail="office_project_initialization_retryable") from error
    except OfficeNamespaceAuthorizationDenied as error:
        raise HTTPException(status_code=403, detail="office_project_create_denied") from error
    return {"id": result.project_id, "created": result.created}
