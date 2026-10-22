from __future__ import annotations

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


def test_office_projects_list_enforces_namespace_profile_and_external_publication(client):
    async def scenario():
        root, _ = await crear_usuario()
        member, _ = await crear_usuario()
        external, _ = await crear_usuario()
        await sql("UPDATE jax_users SET account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (external,))
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'OFFICE_SUPERUSER','ACTIVE'),(%s,'COLLABORATOR','ACTIVE'),(%s,'COLLABORATOR','ACTIVE')", (root, member, external))
        office = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("office-api-" + uuid.uuid4().hex,))
        classic = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("classic-api-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6)),(%s,1,'CLASSIC','ACTIVE',NOW(6),'test',NOW(6))", (office, classic))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s),(%s,%s,'COLLABORATOR','CLIENT_PUBLISHED','ACTIVE',%s)", (office, member, root, office, external, root))
        group = await sql("INSERT INTO office_recipient_group (project_id,tenant_id,group_kind,status,created_by) VALUES (%s,1,'CLIENT','ACTIVE',%s)", (office, root))
        await sql("INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) VALUES (%s,%s,%s,'ACTIVE')", (office, external, group))
        return root, member, external, office, classic
    root, member, external, office, classic = client.portal.call(scenario)
    root_ids = [row["id"] for row in client.get("/api/virtual-office/projects", headers=_headers(root)).json()["projects"]]
    assert office in root_ids
    assert classic not in root_ids
    assert [row["id"] for row in client.get("/api/virtual-office/projects", headers=_headers(member)).json()["projects"]] == [office]
    external_projects = client.get("/api/virtual-office/projects", headers=_headers(external)).json()["projects"]
    assert [row["id"] for row in external_projects] == [office]
    assert external_projects[0]["description"] is None
    no_profile, _ = client.portal.call(crear_usuario)
    assert client.get("/api/virtual-office/projects", headers=_headers(no_profile)).status_code == 403
    client.portal.call(sql, "UPDATE office_external_relation SET status='REVOKED' WHERE user_id=%s", (external,))
    assert client.get("/api/virtual-office/projects", headers=_headers(external)).json()["projects"] == []


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
