from __future__ import annotations

import json
import uuid
import time

from auth.jwt import create_access_token
from tests.identidades import crear_usuario, sql
from virtual_office.mfa import MFAConfig


def _headers(user_id: int, tenant_id: int = 1):
    return {"Authorization": "Bearer " + create_access_token(str(user_id), str(tenant_id), "operator")}


_MFA_CONFIG = MFAConfig(120, 3, 300, 300, 3)


async def _office_root_with_current_proof(*, role: str = "OFFICE_SUPERUSER", proof_time_sql: str = "UTC_TIMESTAMP(6)"):
    user_id, _ = await crear_usuario()
    await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,%s,'ACTIVE')", (user_id, role))
    versions = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True))[0]
    event_id = await sql(
        "INSERT INTO office_mfa_security_event "
        "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
        "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL," + proof_time_sql + ")",
        (user_id, user_id, int(versions[0]), int(versions[1]), uuid.uuid4().bytes),
    )
    return user_id, int(versions[0]), int(versions[1]), event_id


async def _corrupt_client_group_tenant(project_id: int, tenant_id: int):
    """Exercise Phase-B's defensive reread against a broken legacy row.

    The composite FK normally prevents this state; this fixture deliberately
    bypasses it on one disposable connection so the route proves it does not
    trust an existing group by project id alone.
    """
    from db.connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SET FOREIGN_KEY_CHECKS=0")
            try:
                await cur.execute("UPDATE office_recipient_group SET tenant_id=%s WHERE project_id=%s", (tenant_id, project_id))
            finally:
                await cur.execute("SET FOREIGN_KEY_CHECKS=1")


def _proof_headers(user_id: int, token_version: int, mfa_version: int, proof_id: int, key: str):
    token = create_access_token(str(user_id), "1", "operator", token_version,
                                mfa_proof_event_id=proof_id, mfa_version=mfa_version,
                                mfa_at=int(time.time()))
    return {"Authorization": "Bearer " + token, "Idempotency-Key": key}


def _project_payload():
    return {"tenant_id": 1, "name": "office-project-" + uuid.uuid4().hex[:12], "description": "confidential"}


def _configure_mfa(monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120", "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300", "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)


def test_office_projects_list_enforces_namespace_profile_and_external_publication(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, root_tv, root_mv, root_proof = client.portal.call(_office_root_with_current_proof)
    async def scenario():
        member, _ = await crear_usuario()
        external, _ = await crear_usuario()
        await sql("UPDATE jax_users SET account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (external,))
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE'),(%s,'COLLABORATOR','ACTIVE')", (member, external))
        office = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("office-api-" + uuid.uuid4().hex,))
        classic = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("classic-api-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6)),(%s,1,'CLASSIC','ACTIVE',NOW(6),'test',NOW(6))", (office, classic))
        await sql("INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))", (str(uuid.uuid4()), office, member))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s),(%s,%s,'COLLABORATOR','CLIENT_PUBLISHED','ACTIVE',%s)", (office, member, root, office, external, root))
        group = await sql("INSERT INTO office_recipient_group (project_id,tenant_id,group_kind,status,created_by) VALUES (%s,1,'CLIENT','ACTIVE',%s)", (office, root))
        await sql("INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) VALUES (%s,%s,%s,'ACTIVE')", (office, external, group))
        return member, external, office, classic
    member, external, office, classic = client.portal.call(scenario)
    page = f"/api/virtual-office/projects?after_id={office - 1}"
    root_ids = [row["id"] for row in client.get(
        page,
        headers=_proof_headers(root, root_tv, root_mv, root_proof, str(uuid.uuid4())),
    ).json()["projects"]]
    assert office in root_ids
    assert classic not in root_ids
    assert [row["id"] for row in client.get(page, headers=_headers(member)).json()["projects"]] == [office]
    # The list route delegates the same internal predicates as a project
    # read: client publication and revoked membership never become an
    # internal shortcut.
    client.portal.call(sql, "UPDATE office_project_grant SET audience='CLIENT_PUBLISHED' WHERE project_id=%s AND user_id=%s", (office, member))
    assert client.get(page, headers=_headers(member)).json()["projects"] == []
    client.portal.call(sql, "UPDATE office_project_grant SET audience='INTERNAL' WHERE project_id=%s AND user_id=%s", (office, member))
    client.portal.call(sql, "UPDATE jax_project_membership SET status='REVOKED' WHERE project_id=%s AND user_id=%s", (office, member))
    assert client.get(page, headers=_headers(member)).json()["projects"] == []
    client.portal.call(sql, "UPDATE jax_project_membership SET status='ACTIVE' WHERE project_id=%s AND user_id=%s", (office, member))
    # Root is global only after the current verified MFA proof is reread.
    assert client.get(page, headers=_headers(root)).json()["projects"] == []
    external_projects = client.get(page, headers=_headers(external)).json()["projects"]
    assert [row["id"] for row in external_projects] == [office]
    assert external_projects[0]["description"] is None
    no_profile, _ = client.portal.call(crear_usuario)
    denied = client.get(page, headers=_headers(no_profile))
    assert denied.status_code == 403
    assert denied.headers["Cache-Control"] == "no-store"
    client.portal.call(sql, "UPDATE office_external_relation SET status='REVOKED' WHERE user_id=%s", (external,))
    assert client.get(page, headers=_headers(external)).json()["projects"] == []


def test_virtual_office_route_sets_no_store_for_success_and_validation_errors(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    success = client.get("/api/virtual-office/projects", headers=_proof_headers(root, tv, mv, proof, str(uuid.uuid4())))
    assert success.status_code == 200
    assert success.headers["Cache-Control"] == "no-store"
    validation = client.get("/api/virtual-office/projects?limit=101", headers=_headers(root))
    assert validation.status_code == 422
    assert validation.headers["Cache-Control"] == "no-store"


def test_project_list_cursor_advances_over_a_bounded_stable_scope_scan(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        ids = []
        for suffix in ("one", "two"):
            project_id = await sql(
                "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                ("office-cursor-" + suffix + "-" + uuid.uuid4().hex,),
            )
            await sql(
                "INSERT INTO jax_project_scope "
                "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
                "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))",
                (project_id,),
            )
            ids.append(project_id)
        return ids

    first_id, second_id = client.portal.call(scenario)
    headers = _proof_headers(root, tv, mv, proof, str(uuid.uuid4()))
    first = client.get(f"/api/virtual-office/projects?after_id={first_id - 1}&limit=1", headers=headers).json()
    assert first["projects"] == [{"id": first_id, "name": first["projects"][0]["name"], "description": None, "tenant_id": 1}]
    assert first["next_after_id"] == first_id
    second = client.get(f"/api/virtual-office/projects?after_id={first['next_after_id']}&limit=1", headers=headers).json()
    assert second["projects"][0]["id"] == second_id


def test_project_list_never_uses_hidden_lookahead_as_a_cursor(client):
    async def scenario():
        member, _ = await crear_usuario()
        no_grant, _ = await crear_usuario()
        await sql(
            "INSERT INTO office_account_profile (user_id,global_role,status) VALUES "
            "(%s,'COLLABORATOR','ACTIVE'),(%s,'COLLABORATOR','ACTIVE')",
            (member, no_grant),
        )
        project_ids = []
        for suffix in ("hidden", "visible"):
            project_id = await sql(
                "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                ("office-hidden-cursor-" + suffix + "-" + uuid.uuid4().hex,),
            )
            await sql(
                "INSERT INTO jax_project_scope "
                "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
                "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))",
                (project_id,),
            )
            project_ids.append(project_id)
        visible = project_ids[1]
        await sql(
            "INSERT INTO jax_project_membership "
            "(membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) "
            "VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))",
            (str(uuid.uuid4()), visible, member),
        )
        await sql(
            "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
            "VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s)",
            (visible, member, member),
        )
        return member, no_grant, project_ids

    member, no_grant, (hidden, visible) = client.portal.call(scenario)
    response = client.get("/api/virtual-office/projects?limit=1", headers=_headers(member)).json()
    assert [project["id"] for project in response["projects"]] == [visible]
    assert response["next_after_id"] == visible
    assert hidden not in [project["id"] for project in response["projects"]]
    # A user with no grants receives neither an ID nor a progress cursor over
    # confidential scopes owned by other people.
    assert client.get("/api/virtual-office/projects?limit=1", headers=_headers(no_grant)).json() == {
        "projects": [], "next_after_id": None,
    }


def test_list_prefilter_excludes_privileged_view_rows_without_mfa_once(client):
    async def scenario():
        user_id, _ = await crear_usuario()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (user_id,))
        projects = []
        for role in ("PROJECT_ADMIN", "PROJECT_ADMIN", "COLLABORATOR"):
            project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("office-mfa-page-" + uuid.uuid4().hex,))
            await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project_id,))
            await sql("INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))", (str(uuid.uuid4()), project_id, user_id))
            await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,%s,'INTERNAL','ACTIVE',%s)", (project_id, user_id, role, user_id))
            projects.append(project_id)
        return user_id, projects

    user_id, (admin_a, admin_b, collaborator_c) = client.portal.call(scenario)
    page = client.get("/api/virtual-office/projects?limit=3", headers=_headers(user_id)).json()
    assert [row["id"] for row in page["projects"] if row["id"] in {admin_a, admin_b, collaborator_c}] == [collaborator_c]
    assert page["next_after_id"] is None



def test_list_fails_closed_for_global_admin_or_missing_mfa_runtime_config(client, monkeypatch):
    """No eligible proof/config means no privileged candidate discovery or 503."""
    import api.virtual_office.projects as projects_api

    async def scenario():
        collaborator, tv, mv, proof = await _office_root_with_current_proof(role="COLLABORATOR")
        general_admin, _, _, admin_proof = await _office_root_with_current_proof(role="GENERAL_ADMIN")
        projects = []
        for role, professional in (("PROJECT_ADMIN", False), ("COLLABORATOR", True), ("COLLABORATOR", False)):
            project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("office-list-config-" + uuid.uuid4().hex,))
            await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project_id,))
            for user_id in (collaborator, general_admin):
                await sql("INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))", (str(uuid.uuid4()), project_id, user_id))
                await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,%s,'INTERNAL','ACTIVE',%s)", (project_id, user_id, role, user_id))
            if professional:
                await sql("INSERT INTO office_professional_grant (project_id,user_id,capability,basis,evidence_ref,status,granted_by) VALUES (%s,%s,'PROFESSIONAL_ACTION','test','test','ACTIVE',%s)", (project_id, collaborator, collaborator))
            projects.append(project_id)
        return collaborator, tv, mv, proof, general_admin, admin_proof, projects

    collaborator, tv, mv, proof, general_admin, admin_proof, projects = client.portal.call(scenario)
    monkeypatch.setattr(projects_api, "load_mfa_config_from_environment", lambda: None)
    # The durable proof alone is not candidate eligibility if the canonical
    # resolver cannot receive its runtime configuration.  A/B stay hidden;
    # ordinary C remains available without a resolver mismatch.
    collaborator_response = client.get(
        "/api/virtual-office/projects?limit=3",
        headers=_proof_headers(collaborator, tv, mv, proof, str(uuid.uuid4())),
    )
    assert collaborator_response.status_code == 200
    assert [row["id"] for row in collaborator_response.json()["projects"] if row["id"] in projects] == [projects[2]]
    # GENERAL_ADMIN is privileged for all views.  With no proof it returns the
    # same opaque empty page regardless of a cursor at a real scope boundary.
    for headers in (_headers(general_admin), _proof_headers(general_admin, 0, 0, admin_proof, str(uuid.uuid4()))):
        assert client.get("/api/virtual-office/projects", headers=headers).json() == {"projects": [], "next_after_id": None}
        assert client.get(f"/api/virtual-office/projects?after_id={projects[1]}", headers=headers).json() == {"projects": [], "next_after_id": None}

def test_list_aborts_whole_page_when_central_resolver_detects_a_revocation_race(client, monkeypatch):
    import api.virtual_office.projects as projects_api
    from virtual_office.models import OfficeDecision

    async def scenario():
        user_id, _ = await crear_usuario()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (user_id,))
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("office-race-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project_id,))
        await sql("INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))", (str(uuid.uuid4()), project_id, user_id))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s)", (project_id, user_id, user_id))
        return user_id, project_id

    user_id, project_id = client.portal.call(scenario)

    async def revoked(*_args, **_kwargs):
        return OfficeDecision(False, "office_grant_missing", user_id, "VIEW_PROJECT", project_id, project_id)

    monkeypatch.setattr(projects_api, "authorize_office_in_cursor", revoked)
    response = client.get("/api/virtual-office/projects?limit=1", headers=_headers(user_id))
    assert response.status_code == 503
    assert response.json() == {"detail": "office_project_list_retryable"}
    assert response.headers["Cache-Control"] == "no-store"


def test_list_queries_use_measured_user_leading_indexes_under_skew(client):
    """Measure the exact bounded candidate SQL under a 1,200-to-3 ownership skew.

    The user-leading keys are useful only if both the normal and no-proof
    anti-join branches examine the caller's small range, rather than the
    unrelated Office population.  ANALYZE FORMAT=JSON executes these SELECTs
    in the private test schema and exposes actual `r_rows` per table.
    """
    async def scenario():
        from db.connection import get_pool

        internal, _ = await crear_usuario()
        external, _ = await crear_usuario()
        unrelated, _ = await crear_usuario()
        await sql("UPDATE jax_users SET account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (external,))
        await sql(
            "INSERT INTO office_account_profile (user_id,global_role,status) VALUES "
            "(%s,'COLLABORATOR','ACTIVE'),(%s,'COLLABORATOR','ACTIVE')",
            (internal, external),
        )
        pool = await get_pool()
        prefix = "office-list-skew-" + uuid.uuid4().hex
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Population which belongs to another user.  It is deliberately
                # much larger than the subject's three confidential projects.
                await cur.executemany(
                    "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                    [(f"{prefix}-{ordinal}",) for ordinal in range(1200)],
                )
                await cur.execute("SELECT id FROM projects WHERE name LIKE %s ORDER BY id", (prefix + "-%",))
                unrelated_projects = [int(row[0]) for row in await cur.fetchall()]
                assert len(unrelated_projects) == 1200
                await cur.executemany(
                    "INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
                    "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))",
                    [(project_id,) for project_id in unrelated_projects],
                )
                await cur.executemany(
                    "INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) "
                    "VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))",
                    [(str(uuid.uuid4()), project_id, unrelated) for project_id in unrelated_projects],
                )
                await cur.executemany(
                    "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
                    "VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s)",
                    [(project_id, unrelated, unrelated) for project_id in unrelated_projects],
                )
                await cur.executemany(
                    "INSERT INTO office_recipient_group (project_id,tenant_id,group_kind,status,created_by) "
                    "VALUES (%s,1,'CLIENT','ACTIVE',%s)",
                    [(project_id, unrelated) for project_id in unrelated_projects],
                )
                await cur.execute(
                    "SELECT recipient_group_id,project_id FROM office_recipient_group WHERE project_id IN ("
                    + ",".join(["%s"] * len(unrelated_projects)) + ")",
                    unrelated_projects,
                )
                unrelated_groups = {int(project_id): int(group_id) for group_id, project_id in await cur.fetchall()}
                await cur.executemany(
                    "INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) VALUES (%s,%s,%s,'ACTIVE')",
                    [(project_id, unrelated, unrelated_groups[project_id]) for project_id in unrelated_projects],
                )

                owned = []
                for role, professional in (("PROJECT_ADMIN", False), ("COLLABORATOR", True), ("COLLABORATOR", False)):
                    await cur.execute("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", (prefix + "-owned-" + uuid.uuid4().hex,))
                    project_id = int(cur.lastrowid)
                    await cur.execute("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project_id,))
                    await cur.execute("INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))", (str(uuid.uuid4()), project_id, internal))
                    await cur.execute("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,%s,'INTERNAL','ACTIVE',%s),(%s,%s,'COLLABORATOR','CLIENT_PUBLISHED','ACTIVE',%s)", (project_id, internal, role, internal, project_id, external, internal))
                    await cur.execute("INSERT INTO office_recipient_group (project_id,tenant_id,group_kind,status,created_by) VALUES (%s,1,'CLIENT','ACTIVE',%s)", (project_id, internal))
                    group_id = int(cur.lastrowid)
                    await cur.execute("INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) VALUES (%s,%s,%s,'ACTIVE')", (project_id, external, group_id))
                    if professional:
                        await cur.execute("INSERT INTO office_professional_grant (project_id,user_id,capability,basis,evidence_ref,status,granted_by) VALUES (%s,%s,'PROFESSIONAL_ACTION','test','test','ACTIVE',%s)", (project_id, internal, internal))
                    owned.append(project_id)

                internal_sql = (
                    "SELECT p.id,p.name,p.description,s.tenant_id,NULL FROM office_project_grant g "
                    "STRAIGHT_JOIN projects p ON p.id=g.project_id "
                    "STRAIGHT_JOIN jax_project_scope s ON s.project_id=p.id "
                    "STRAIGHT_JOIN jax_project_membership m ON m.project_id=p.id AND m.user_id=%s "
                    "WHERE s.authorization_namespace='OFFICE_CONFIDENTIAL' AND s.status='ACTIVE' "
                    "AND g.user_id=%s AND s.tenant_id=%s AND g.status='ACTIVE' AND g.audience='INTERNAL' "
                    "AND g.grant_role IN ('COLLABORATOR','PROJECT_ADMIN') AND m.status='ACTIVE' "
                )
                external_sql = (
                    "SELECT p.id,p.name,p.description,s.tenant_id,r.recipient_group_id FROM office_external_relation r FORCE INDEX (idx_office_external_relation_list) "
                    "STRAIGHT_JOIN projects p ON p.id=r.project_id "
                    "STRAIGHT_JOIN jax_project_scope s ON s.project_id=p.id "
                    "STRAIGHT_JOIN office_project_grant g ON g.project_id=p.id AND g.user_id=%s "
                    "STRAIGHT_JOIN office_recipient_group rg ON rg.recipient_group_id=r.recipient_group_id AND rg.project_id=p.id "
                    "WHERE s.authorization_namespace='OFFICE_CONFIDENTIAL' AND s.status='ACTIVE' "
                    "AND g.status='ACTIVE' AND g.audience='CLIENT_PUBLISHED' AND g.grant_role='COLLABORATOR' "
                    "AND r.user_id=%s AND r.status='ACTIVE' AND rg.status='ACTIVE' "
                )

                async def measure(name, statement, params, index):
                    await cur.execute(statement, params)
                    result = await cur.fetchall()
                    await cur.execute("EXPLAIN " + statement, params)
                    plan = await cur.fetchall()
                    assert any(row[5] == index for row in plan), (name, [(row[2], row[3], row[5], row[8], row[9]) for row in plan])
                    assert all(row[3] != "ALL" and "filesort" not in (row[9] or "").lower() and "temporary" not in (row[9] or "").lower() for row in plan)
                    await cur.execute("ANALYZE FORMAT=JSON " + statement, params)
                    analyzed = json.loads((await cur.fetchone())[0])
                    return plan, analyzed, result

                measurements = {}
                for branch, base, params, proof_actor, index in (
                    ("internal", internal_sql, (internal, internal, 1), internal, "idx_office_project_grant_list"),
                    ("external", external_sql, (external, external), external, "idx_office_external_relation_list"),
                ):
                    for proof in ("valid", "missing"):
                        predicate = ""
                        if proof == "missing":
                            predicate = "AND g.grant_role='COLLABORATOR' AND NOT EXISTS (SELECT 1 FROM office_professional_grant pg WHERE pg.project_id=p.id AND pg.user_id=%s AND pg.capability='PROFESSIONAL_ACTION' AND pg.status='ACTIVE') "
                        for cursor_name, after in (("first", None), ("after", owned[0])):
                            cursor_predicate = "" if after is None else "AND p.id>%s "
                            statement = base + predicate + cursor_predicate + "ORDER BY p.id LIMIT %s"
                            query_params = (*params, *((proof_actor,) if proof == "missing" else ()), *((after,) if after is not None else ()), 51)
                            measurements[(branch, proof, cursor_name)] = await measure(
                                branch + ":" + proof + ":" + cursor_name, statement, query_params, index
                            )
        return owned, measurements

    owned, measurements = client.portal.call(scenario)
    # ANALYZE's driving table reports actual examined index records.  In both
    # cursor positions and proof modes it stays within the three owned rows,
    # despite 1,200 unrelated confidential projects.
    for name, (plan, analyzed, result) in measurements.items():
        first_table = analyzed["query_block"]["nested_loop"][0]["table"]
        assert first_table["r_rows"] <= 3, (name, first_table)
        expected = owned if name[1] == "valid" or name[0] == "external" else [owned[2]]
        if name[2] == "after":
            expected = [project_id for project_id in expected if project_id > owned[0]]
        assert [int(row[0]) for row in result] == expected, (name, result)
    assert owned == sorted(owned)


def test_create_project_jax_first_then_exact_replay_and_one_client_group(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    key = str(uuid.uuid4())
    headers = _proof_headers(root, tv, mv, proof, key)
    payload = _project_payload()
    created = client.post("/api/virtual-office/projects", json=payload, headers=headers)
    assert created.status_code == 200
    assert created.json()["created"] is True
    project_id = created.json()["id"]
    replay = client.post("/api/virtual-office/projects", json=payload, headers=headers)
    assert replay.status_code == 200
    assert replay.json() == {"id": project_id, "created": False}
    assert client.portal.call(sql, "SELECT authorization_namespace,tenant_id FROM jax_project_scope WHERE project_id=%s", (project_id,), True) == (("OFFICE_CONFIDENTIAL", 1),)
    assert client.portal.call(sql, "SELECT COUNT(*) FROM office_recipient_group WHERE project_id=%s AND group_kind='CLIENT' AND status='ACTIVE'", (project_id,), True) == ((1,),)


def test_create_project_denies_nonroot_and_expired_proof_before_jax_write(client, monkeypatch):
    _configure_mfa(monkeypatch)
    ordinary, tv, mv, proof = client.portal.call(lambda: _office_root_with_current_proof(role="COLLABORATOR"))
    assert client.post("/api/virtual-office/projects", json=_project_payload(), headers=_proof_headers(ordinary, tv, mv, proof, str(uuid.uuid4()))).status_code == 403
    root, tv, mv, old_proof = client.portal.call(
        lambda: _office_root_with_current_proof(proof_time_sql="DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 301 SECOND)")
    )
    assert client.post("/api/virtual-office/projects", json=_project_payload(), headers=_proof_headers(root, tv, mv, old_proof, str(uuid.uuid4()))).status_code == 403


def test_create_project_group_fault_is_partial_and_same_key_repairs(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    key = str(uuid.uuid4())
    payload = _project_payload()
    trigger = "office_group_fault_" + uuid.uuid4().hex[:10]
    client.portal.call(sql, "CREATE TRIGGER " + trigger + " BEFORE INSERT ON office_recipient_group FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'fault'")
    try:
        first = client.post("/api/virtual-office/projects", json=payload, headers=_proof_headers(root, tv, mv, proof, key))
        assert first.status_code == 503
        project_id = client.portal.call(sql, "SELECT project_id FROM jax_namespace_project_creation_request WHERE namespace='OFFICE_CONFIDENTIAL' AND idempotency_key=%s", (key,), True)[0][0]
        assert client.portal.call(sql, "SELECT COUNT(*) FROM office_recipient_group WHERE project_id=%s", (project_id,), True) == ((0,),)
    finally:
        client.portal.call(sql, "DROP TRIGGER " + trigger)
    repaired = client.post("/api/virtual-office/projects", json=payload, headers=_proof_headers(root, tv, mv, proof, key))
    assert repaired.status_code == 200
    assert repaired.json()["created"] is False
    assert client.portal.call(sql, "SELECT COUNT(*) FROM office_recipient_group WHERE project_id=%s AND status='ACTIVE'", (project_id,), True) == ((1,),)


def test_create_project_revoked_actor_cannot_repair_or_reactivate_group(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    key = str(uuid.uuid4())
    payload = _project_payload()
    trigger = "office_group_fault_" + uuid.uuid4().hex[:10]
    client.portal.call(sql, "CREATE TRIGGER " + trigger + " BEFORE INSERT ON office_recipient_group FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'fault'")
    try:
        assert client.post("/api/virtual-office/projects", json=payload, headers=_proof_headers(root, tv, mv, proof, key)).status_code == 503
    finally:
        client.portal.call(sql, "DROP TRIGGER " + trigger)
    client.portal.call(sql, "UPDATE office_account_profile SET status='REVOKED' WHERE user_id=%s", (root,))
    assert client.post("/api/virtual-office/projects", json=payload, headers=_proof_headers(root, tv, mv, proof, key)).status_code == 403

    active_root, active_tv, active_mv, active_proof = client.portal.call(_office_root_with_current_proof)
    active_key = str(uuid.uuid4())
    active_payload = _project_payload()
    created = client.post("/api/virtual-office/projects", json=active_payload,
                          headers=_proof_headers(active_root, active_tv, active_mv, active_proof, active_key))
    assert created.status_code == 200
    project_id = created.json()["id"]
    client.portal.call(sql, "UPDATE office_recipient_group SET status='REVOKED' WHERE project_id=%s", (project_id,))
    conflict = client.post("/api/virtual-office/projects", json=active_payload,
                           headers=_proof_headers(active_root, active_tv, active_mv, active_proof, active_key))
    assert conflict.status_code == 409
    assert client.portal.call(sql, "SELECT status FROM office_recipient_group WHERE project_id=%s", (project_id,), True) == (("REVOKED",),)


def test_create_project_maps_core_idempotency_and_typed_contention(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    key = str(uuid.uuid4())
    headers = _proof_headers(root, tv, mv, proof, key)
    payload = _project_payload()
    assert client.post("/api/virtual-office/projects", json=payload, headers=headers).status_code == 200
    changed = dict(payload, name=payload["name"] + "-changed")
    assert client.post("/api/virtual-office/projects", json=changed, headers=headers).status_code == 409

    from jax.memory.project_authority import ProjectAuthorityAdmin, ProjectAuthorityRetryable

    async def contention(*_args, **_kwargs):
        raise ProjectAuthorityRetryable("retry")

    monkeypatch.setattr(ProjectAuthorityAdmin, "create_namespaced_project", contention)
    assert client.post("/api/virtual-office/projects", json=_project_payload(),
                       headers=_proof_headers(root, tv, mv, proof, str(uuid.uuid4()))).status_code == 503


def test_create_project_existing_client_group_must_match_target_tenant(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    key = str(uuid.uuid4())
    payload = _project_payload()
    created = client.post("/api/virtual-office/projects", json=payload,
                          headers=_proof_headers(root, tv, mv, proof, key))
    assert created.status_code == 200
    project_id = created.json()["id"]
    tenant_name = "office-wrong-group-" + uuid.uuid4().hex[:8]
    client.portal.call(sql, "INSERT INTO jax_tenants (name,plan,status) VALUES (%s,'test','active')", (tenant_name,))
    other_tenant = client.portal.call(sql, "SELECT tenant_id FROM jax_tenants WHERE name=%s", (tenant_name,), True)[0][0]
    client.portal.call(_corrupt_client_group_tenant, project_id, other_tenant)
    replay = client.post("/api/virtual-office/projects", json=payload,
                         headers=_proof_headers(root, tv, mv, proof, key))
    assert replay.status_code == 503
    assert replay.json() == {"detail": "office_project_initialization_retryable"}


def test_create_project_unexpected_core_failure_is_opaque_retryable(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    from jax.memory.project_authority import ProjectAuthorityAdmin

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("database connection password=not-for-response")

    monkeypatch.setattr(ProjectAuthorityAdmin, "create_namespaced_project", unavailable)
    response = client.post("/api/virtual-office/projects", json=_project_payload(),
                           headers=_proof_headers(root, tv, mv, proof, str(uuid.uuid4())))
    assert response.status_code == 503
    assert response.json() == {"detail": "office_project_create_retryable"}


def test_project_detail_hides_internal_metadata_from_external_client(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, root_tv, root_mv, proof = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        external, _ = await crear_usuario()
        await sql("UPDATE jax_users SET account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (external,))
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (external,))
        project = await sql("INSERT INTO projects (project_uuid,name,description,status) VALUES (UUID(),%s,%s,'active')",
                            ("detail-" + uuid.uuid4().hex, "internal-only metadata"))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','CLIENT_PUBLISHED','ACTIVE',%s)", (project, external, root))
        group = await sql("INSERT INTO office_recipient_group (project_id,tenant_id,group_kind,status,created_by) VALUES (%s,1,'CLIENT','ACTIVE',%s)", (project, root))
        await sql("INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) VALUES (%s,%s,%s,'ACTIVE')", (project, external, group))
        return external, project

    external, project = client.portal.call(scenario)
    external_response = client.get(f"/api/virtual-office/projects/{project}", headers=_headers(external))
    assert external_response.status_code == 200
    assert external_response.json()["description"] is None
    root_response = client.get(f"/api/virtual-office/projects/{project}", headers=_proof_headers(root, root_tv, root_mv, proof, str(uuid.uuid4())))
    assert root_response.status_code == 200
    assert root_response.json()["description"] == "internal-only metadata"


def test_root_updates_office_metadata_through_typed_jax_hook(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project = await sql("INSERT INTO projects (project_uuid,name,description,status) VALUES (UUID(),%s,%s,'active')",
                            ("metadata-before-" + uuid.uuid4().hex, "before"))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project,))
        return project

    project = client.portal.call(scenario)
    updated = client.put(
        f"/api/virtual-office/projects/{project}",
        json={"name": "  metadata after  ", "description": "after"},
        headers=_proof_headers(root, tv, mv, proof, str(uuid.uuid4())),
    )
    assert updated.status_code == 200
    assert updated.json() == {"id": project, "name": "metadata after", "description": "after", "changed": True}
    assert client.portal.call(sql, "SELECT name,description FROM projects WHERE id=%s", (project,), True) == (("metadata after", "after"),)


def test_create_project_rejects_whitespace_name_and_invalid_idempotency_before_write(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    before = client.portal.call(sql, "SELECT COUNT(*) FROM jax_project_scope", (), True)[0][0]
    headers = _proof_headers(root, tv, mv, proof, str(uuid.uuid4()))
    whitespace = client.post("/api/virtual-office/projects", json={"tenant_id": 1, "name": "   "}, headers=headers)
    assert whitespace.status_code == 422
    invalid_headers = dict(headers)
    invalid_headers["Idempotency-Key"] = "not-a-canonical-uuid"
    invalid = client.post("/api/virtual-office/projects", json={"tenant_id": 1, "name": "valid"}, headers=invalid_headers)
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "office_project_idempotency_invalid"}
    assert client.portal.call(sql, "SELECT COUNT(*) FROM jax_project_scope", (), True)[0][0] == before
