from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

from tests.identidades import crear_usuario, sql
from tests.test_office_people_api import _configure_mfa, _headers, _root_with_proof


async def _office_project_and_unassigned_member():
    target, _ = await crear_usuario()
    await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (target,))
    project = await sql(
        "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
        ("office-member-" + uuid.uuid4().hex,),
    )
    await sql(
        "INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
        "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project,),
    )
    return project, target


def test_root_grant_and_revoke_member_are_atomic_with_jax_membership(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    project, target = client.portal.call(_office_project_and_unassigned_member)
    headers = {**_headers(root, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())}
    body = {"grant_role": "COLLABORATOR", "audience": "INTERNAL", "expected_version": 0}

    granted = client.put(f"/api/virtual-office/projects/{project}/members/{target}", json=body, headers=headers)
    assert granted.status_code == 200
    assert granted.json()["version"] == 1
    assert client.portal.call(
        sql,
        "SELECT m.status,g.status,g.grant_role,g.audience FROM jax_project_membership m "
        "JOIN office_project_grant g ON g.project_id=m.project_id AND g.user_id=m.user_id "
        "WHERE m.project_id=%s AND m.user_id=%s", (project, target), True,
    ) == (("ACTIVE", "ACTIVE", "COLLABORATOR", "INTERNAL"),)
    plan = client.portal.call(
        sql,
        "EXPLAIN SELECT grant_role,audience,status,version FROM office_project_grant "
        "WHERE project_id=%s AND user_id=%s FOR UPDATE", (project, target), True,
    )
    assert plan and plan[0][5] == "uq_office_project_grant_subject"
    assert "Using filesort" not in str(plan[0][-1] or "")
    assert "Using temporary" not in str(plan[0][-1] or "")

    replay = client.put(
        f"/api/virtual-office/projects/{project}/members/{target}",
        json=body, headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    # The Office receipt binds the optimistic version as well as JAX's
    # namespace request.  Reusing a UUID with another Office transition is
    # not a lost-response retry.
    conflict = client.put(
        f"/api/virtual-office/projects/{project}/members/{target}",
        json={**body, "expected_version": 1}, headers=headers,
    )
    assert (conflict.status_code, conflict.json()["detail"]) == (409, "office_project_grant_idempotency_conflict")
    assert client.portal.call(
        sql,
        "SELECT operation,expected_version,office_grant_status FROM office_membership_mutation_receipt "
        "WHERE namespace='OFFICE_CONFIDENTIAL' AND idempotency_key=%s", (headers["Idempotency-Key"],), True,
    ) == (("GRANT_OFFICE_MEMBER", 0, "ACTIVE"),)
    revoke_headers = {**_headers(root, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())}
    revoked = client.delete(
        f"/api/virtual-office/projects/{project}/members/{target}?expected_version=1", headers=revoke_headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["version"] == 2
    revoke_replay = client.delete(
        f"/api/virtual-office/projects/{project}/members/{target}?expected_version=1", headers=revoke_headers,
    )
    assert revoke_replay.status_code == 200
    assert revoke_replay.json()["replayed"] is True
    assert client.portal.call(
        sql,
        "SELECT operation,expected_version,office_grant_version,office_grant_status FROM office_membership_mutation_receipt "
        "WHERE namespace='OFFICE_CONFIDENTIAL' AND idempotency_key=%s", (revoke_headers["Idempotency-Key"],), True,
    ) == (("REVOKE_OFFICE_MEMBER", 1, 2, "REVOKED"),)
    assert client.portal.call(
        sql,
        "SELECT m.status,g.status FROM jax_project_membership m JOIN office_project_grant g "
        "ON g.project_id=m.project_id AND g.user_id=m.user_id WHERE m.project_id=%s AND m.user_id=%s",
        (project, target), True,
    ) == (("REVOKED", "REVOKED"),)


def test_project_admin_client_published_shape_is_rejected_before_membership(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    project, target = client.portal.call(_office_project_and_unassigned_member)
    response = client.put(f"/api/virtual-office/projects/{project}/members/{target}",
                          json={"grant_role": "PROJECT_ADMIN", "audience": "CLIENT_PUBLISHED", "expected_version": 0},
                          headers={**_headers(root, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 422
    assert client.portal.call(sql, "SELECT user_id FROM jax_project_membership WHERE project_id=%s AND user_id=%s", (project, target), True) == ()


def test_client_published_grant_requires_active_external_profile_and_tracks_relation(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    project, _ = client.portal.call(_office_project_and_unassigned_member)

    async def external_target():
        target, _ = await crear_usuario()
        await sql("UPDATE jax_users SET account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (target,))
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (target,))
        await sql("INSERT INTO office_recipient_group (project_id,tenant_id,group_kind,status,created_by) VALUES (%s,1,'CLIENT','ACTIVE',%s)", (project, root))
        return target

    target = client.portal.call(external_target)
    headers = {**_headers(root, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())}
    response = client.put(
        f"/api/virtual-office/projects/{project}/members/{target}",
        json={"grant_role": "COLLABORATOR", "audience": "CLIENT_PUBLISHED", "expected_version": 0}, headers=headers)
    assert response.status_code == 200
    assert client.portal.call(
        sql, "SELECT g.status,r.status,rg.status FROM office_project_grant g JOIN office_external_relation r "
        "ON r.project_id=g.project_id AND r.user_id=g.user_id JOIN office_recipient_group rg "
        "ON rg.recipient_group_id=r.recipient_group_id WHERE g.project_id=%s AND g.user_id=%s", (project, target), True,
    ) == (("ACTIVE", "ACTIVE", "ACTIVE"),)
    revoked = client.delete(
        f"/api/virtual-office/projects/{project}/members/{target}?expected_version=1",
        headers={**_headers(root, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())})
    assert revoked.status_code == 200
    assert client.portal.call(
        sql, "SELECT g.status,r.status FROM office_project_grant g JOIN office_external_relation r "
        "ON r.project_id=g.project_id AND r.user_id=g.user_id WHERE g.project_id=%s AND g.user_id=%s", (project, target), True,
    ) == (("REVOKED", "REVOKED"),)


def test_unprofiled_or_revoked_target_never_receives_jax_membership(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    project, target = client.portal.call(_office_project_and_unassigned_member)
    awaitable = lambda: sql("UPDATE office_account_profile SET status='REVOKED' WHERE user_id=%s", (target,))
    client.portal.call(awaitable)
    response = client.put(
        f"/api/virtual-office/projects/{project}/members/{target}",
        json={"grant_role": "COLLABORATOR", "audience": "INTERNAL", "expected_version": 0},
        headers={**_headers(root, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 409
    assert client.portal.call(sql, "SELECT user_id FROM jax_project_membership WHERE project_id=%s AND user_id=%s", (project, target), True) == ()


def test_project_admin_cannot_mint_project_admin_or_leave_partial_member(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, _, _, _ = client.portal.call(_root_with_proof)
    project, target = client.portal.call(_office_project_and_unassigned_member)

    async def project_admin():
        admin, _ = await crear_usuario()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (admin,))
        versions = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (admin,), True))[0]
        proof = await sql(
            "INSERT INTO office_mfa_security_event "
            "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
            "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,UTC_TIMESTAMP(6))",
            (admin, admin, versions[0], versions[1], uuid.uuid4().bytes),
        )
        await sql("INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,grant_origin,created_at,created_by,updated_at) VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE','OFFICE',NOW(6),'test',NOW(6))", (str(uuid.uuid4()), project, admin))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project, admin, root))
        return admin, int(versions[0]), int(versions[1]), int(proof)

    admin, tv, mv, proof = client.portal.call(project_admin)
    denied = client.put(
        f"/api/virtual-office/projects/{project}/members/{target}",
        json={"grant_role": "PROJECT_ADMIN", "audience": "INTERNAL", "expected_version": 0},
        headers={**_headers(admin, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert denied.status_code == 403
    assert client.portal.call(
        sql, "SELECT user_id FROM jax_project_membership WHERE project_id=%s AND user_id=%s", (project, target), True,
    ) == ()
    assert client.portal.call(
        sql, "SELECT user_id FROM office_project_grant WHERE project_id=%s AND user_id=%s", (project, target), True,
    ) == ()


def test_office_grant_write_failure_rolls_back_jax_membership(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    project, target = client.portal.call(_office_project_and_unassigned_member)

    async def install_trigger():
        await sql("DROP TRIGGER IF EXISTS test_office_project_grant_rollback")
        await sql(
            "CREATE TRIGGER test_office_project_grant_rollback BEFORE INSERT ON office_project_grant "
            "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test rollback'"
        )

    client.portal.call(install_trigger)
    try:
        response = client.put(
            f"/api/virtual-office/projects/{project}/members/{target}",
            json={"grant_role": "COLLABORATOR", "audience": "INTERNAL", "expected_version": 0},
            headers={**_headers(root, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 503
    finally:
        client.portal.call(sql, "DROP TRIGGER IF EXISTS test_office_project_grant_rollback")
    assert client.portal.call(
        sql, "SELECT user_id FROM jax_project_membership WHERE project_id=%s AND user_id=%s", (project, target), True,
    ) == ()


def test_terminal_receipt_failure_rolls_back_every_companion(client, monkeypatch):
    """The receipt is terminal evidence, never a best-effort post-commit log."""
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    project, target = client.portal.call(_office_project_and_unassigned_member)

    async def install_trigger():
        await sql("DROP TRIGGER IF EXISTS test_office_receipt_rollback")
        await sql(
            "CREATE TRIGGER test_office_receipt_rollback BEFORE INSERT ON office_membership_mutation_receipt "
            "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test receipt rollback'"
        )

    client.portal.call(install_trigger)
    try:
        response = client.put(
            f"/api/virtual-office/projects/{project}/members/{target}",
            json={"grant_role": "COLLABORATOR", "audience": "INTERNAL", "expected_version": 0},
            headers={**_headers(root, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 503
    finally:
        client.portal.call(sql, "DROP TRIGGER IF EXISTS test_office_receipt_rollback")
    assert client.portal.call(
        sql,
        "SELECT (SELECT COUNT(*) FROM jax_project_membership WHERE project_id=%s AND user_id=%s),"
        "(SELECT COUNT(*) FROM office_project_grant WHERE project_id=%s AND user_id=%s),"
        "(SELECT COUNT(*) FROM office_membership_mutation_receipt WHERE project_id=%s AND target_user_id=%s)",
        (project, target, project, target, project, target), True,
    ) == ((0, 0, 0),)


def test_replay_refuses_drifted_office_companion(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    project, target = client.portal.call(_office_project_and_unassigned_member)
    headers = {**_headers(root, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())}
    body = {"grant_role": "COLLABORATOR", "audience": "INTERNAL", "expected_version": 0}
    assert client.put(f"/api/virtual-office/projects/{project}/members/{target}", json=body, headers=headers).status_code == 200
    client.portal.call(
        sql,
        "UPDATE office_project_grant SET version=version+1 WHERE project_id=%s AND user_id=%s",
        (project, target),
    )
    replay = client.put(f"/api/virtual-office/projects/{project}/members/{target}", json=body, headers=headers)
    assert (replay.status_code, replay.json()["detail"]) == (503, "office_project_grant_retryable")


def test_concurrent_same_membership_request_has_one_receipt(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    project, target = client.portal.call(_office_project_and_unassigned_member)
    headers = {**_headers(root, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())}
    body = {"grant_role": "COLLABORATOR", "audience": "INTERNAL", "expected_version": 0}

    def request():
        return client.put(f"/api/virtual-office/projects/{project}/members/{target}", json=body, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _unused: request(), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 200]
    assert sum(response.json()["replayed"] for response in responses) == 1
    assert client.portal.call(
        sql,
        "SELECT (SELECT COUNT(*) FROM jax_namespace_membership_mutation_request WHERE namespace='OFFICE_CONFIDENTIAL' AND idempotency_key=%s),"
        "(SELECT COUNT(*) FROM office_membership_mutation_receipt WHERE namespace='OFFICE_CONFIDENTIAL' AND idempotency_key=%s)",
        (headers["Idempotency-Key"], headers["Idempotency-Key"]), True,
    ) == ((1, 1),)
