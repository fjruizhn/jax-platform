"""The canonical Office reads.  Request values are always bound parameters."""
from __future__ import annotations

from .capabilities import OfficeOperation


async def fetch_actor(cur, actor_user_id: int, *, for_update: bool):
    suffix = " FOR UPDATE" if for_update else ""
    await cur.execute(
        "SELECT u.tenant_id,u.status,u.account_class,t.status,p.global_role,p.status "
        "FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
        "LEFT JOIN office_account_profile p ON p.user_id=u.user_id "
        "WHERE u.user_id=%s" + suffix,
        (actor_user_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return dict(zip(("tenant_id", "user_status", "account_class", "tenant_status", "global_role", "profile_status"), row))


async def fetch_project_scope(cur, project_id: int, *, for_update: bool):
    suffix = " FOR UPDATE" if for_update else ""
    await cur.execute(
        "SELECT tenant_id,status,authorization_namespace FROM jax_project_scope "
        "WHERE project_id=%s" + suffix,
        (project_id,),
    )
    row = await cur.fetchone()
    return None if row is None else dict(zip(("tenant_id", "status", "namespace"), row))


async def fetch_internal_grant(cur, project_id: int, actor_user_id: int, *, for_update: bool):
    suffix = " FOR UPDATE" if for_update else ""
    await cur.execute(
        "SELECT g.grant_role,g.audience,g.status,m.status "
        "FROM office_project_grant g JOIN jax_project_membership m "
        "ON m.project_id=g.project_id AND m.user_id=g.user_id "
        "WHERE g.project_id=%s AND g.user_id=%s" + suffix,
        (project_id, actor_user_id),
    )
    row = await cur.fetchone()
    return None if row is None else dict(zip(("grant_role", "audience", "grant_status", "membership_status"), row))


async def fetch_external_grant(cur, project_id: int, actor_user_id: int, recipient_group_id: int | None, *, for_update: bool):
    suffix = " FOR UPDATE" if for_update else ""
    query = (
        "SELECT g.grant_role,g.audience,g.status,r.status,r.recipient_group_id,rg.status "
        "FROM office_project_grant g JOIN office_external_relation r "
        "ON r.project_id=g.project_id AND r.user_id=g.user_id "
        "JOIN office_recipient_group rg ON rg.recipient_group_id=r.recipient_group_id AND rg.project_id=r.project_id "
        "WHERE g.project_id=%s AND g.user_id=%s"
    )
    params: tuple[object, ...] = (project_id, actor_user_id)
    if recipient_group_id is not None:
        query += " AND r.recipient_group_id=%s"
        params += (recipient_group_id,)
    await cur.execute(query + suffix, params)
    row = await cur.fetchone()
    return None if row is None else dict(zip(("grant_role", "audience", "grant_status", "relation_status", "recipient_group_id", "group_status"), row))


async def fetch_delegation(cur, actor_user_id: int, operation: OfficeOperation, *, for_update: bool):
    suffix = " FOR UPDATE" if for_update else ""
    await cur.execute(
        "SELECT status FROM office_admin_delegation WHERE user_id=%s AND capability=%s" + suffix,
        (actor_user_id, operation.value),
    )
    row = await cur.fetchone()
    return None if row is None else {"status": row[0]}


async def fetch_professional_grant(cur, project_id: int, actor_user_id: int, operation: OfficeOperation, *, for_update: bool):
    suffix = " FOR UPDATE" if for_update else ""
    await cur.execute(
        "SELECT status FROM office_professional_grant WHERE project_id=%s AND user_id=%s AND capability=%s" + suffix,
        (project_id, actor_user_id, operation.value),
    )
    row = await cur.fetchone()
    return None if row is None else {"status": row[0]}
