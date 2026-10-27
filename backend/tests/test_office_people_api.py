from __future__ import annotations

import time
import uuid

from auth.jwt import create_access_token
from tests.identidades import crear_usuario, sql


def _configure_mfa(monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120", "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300", "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)


async def _root_with_proof():
    user_id, _ = await crear_usuario()
    await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'OFFICE_SUPERUSER','ACTIVE')", (user_id,))
    token_version, mfa_version = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True))[0]
    proof = await sql(
        "INSERT INTO office_mfa_security_event "
        "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
        "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,UTC_TIMESTAMP(6))",
        (user_id, user_id, int(token_version), int(mfa_version), uuid.uuid4().bytes),
    )
    return user_id, int(token_version), int(mfa_version), int(proof)


async def _general_admin_with_proof():
    user_id, _ = await crear_usuario()
    await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'GENERAL_ADMIN','ACTIVE')", (user_id,))
    token_version, mfa_version = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True))[0]
    proof = await sql(
        "INSERT INTO office_mfa_security_event "
        "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
        "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,UTC_TIMESTAMP(6))",
        (user_id, user_id, int(token_version), int(mfa_version), uuid.uuid4().bytes),
    )
    return user_id, int(token_version), int(mfa_version), int(proof)


def _headers(user_id: int, token_version: int, mfa_version: int, proof: int, *, tenant_id: int = 1):
    return {"Authorization": "Bearer " + create_access_token(
        str(user_id), str(tenant_id), "operator", token_version,
        mfa_proof_event_id=proof, mfa_version=mfa_version, mfa_at=int(time.time()),
    )}


def _headers_without_mfa(user_id: int, token_version: int, *, tenant_id: int = 1):
    return {"Authorization": "Bearer " + create_access_token(
        str(user_id), str(tenant_id), "operator", token_version,
    )}


def test_office_directory_lists_human_profiles_without_mfa_but_keeps_mutations_protected(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, _mv, _proof = client.portal.call(_root_with_proof)

    async def scenario():
        peer, _ = await crear_usuario()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (peer,))
        peer_tv = (await sql("SELECT token_version FROM jax_users WHERE user_id=%s", (peer,), True))[0][0]
        return peer, int(peer_tv)

    peer, peer_tv = client.portal.call(scenario)
    root_headers = _headers_without_mfa(root, tv)
    listed = client.get(f"/api/virtual-office/people?after_user_id={min(root, peer) - 1}", headers=root_headers)
    assert listed.status_code == 200
    assert {root, peer}.issubset({item["user_id"] for item in listed.json()["people"]})
    assert listed.headers["cache-control"] == "no-store"
    assert "Authorization" in {part.strip() for part in listed.headers["vary"].split(",")}
    assert client.get("/api/virtual-office/people", headers=_headers_without_mfa(peer, peer_tv)).status_code == 403
    projects = client.get(f"/api/virtual-office/people/{peer}/projects", headers=root_headers)
    assert projects.status_code == 200
    assert projects.json() == {"user_id": peer, "projects": [], "next_after_project_id": None}
    assert projects.headers["cache-control"] == "no-store"
    assert client.get(f"/api/virtual-office/people/{peer}/projects", headers=_headers_without_mfa(peer, peer_tv)).status_code == 403

    async def add_project():
        project_id = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("office-person-" + uuid.uuid4().hex,),
        )
        await sql(
            "INSERT INTO jax_project_scope "
            "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
            (project_id,),
        )
        await sql(
            "INSERT INTO jax_project_membership "
            "(membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) "
            "VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
            (str(uuid.uuid4()), project_id, peer),
        )
        await sql(
            "INSERT INTO office_project_grant "
            "(project_id,user_id,grant_role,audience,status,granted_by) "
            "VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s)",
            (project_id, peer, root),
        )
        return project_id

    project_id = client.portal.call(add_project)
    visible = client.get(f"/api/virtual-office/people/{peer}/projects", headers=root_headers)
    assert visible.status_code == 200
    assert [item["project_id"] for item in visible.json()["projects"]] == [project_id]

    async def delegated_admin():
        admin, _ = await crear_usuario()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'GENERAL_ADMIN','ACTIVE')", (admin,))
        await sql(
            "INSERT INTO office_admin_delegation (tenant_id,user_id,capability,status,granted_by) "
            "VALUES (1,%s,'ADMINISTER_OFFICE','ACTIVE',%s)", (admin, root),
        )
        version = (await sql("SELECT token_version FROM jax_users WHERE user_id=%s", (admin,), True))[0][0]
        return admin, int(version)

    admin, admin_tv = client.portal.call(delegated_admin)
    assignable = client.get(f"/api/virtual-office/people/assignable-projects?after_project_id={project_id - 1}",
                            headers=_headers_without_mfa(admin, admin_tv))
    assert assignable.status_code == 200
    assert project_id in [item["id"] for item in assignable.json()["projects"]]
    assert assignable.headers["cache-control"] == "no-store"
    client.portal.call(sql, "UPDATE jax_project_membership SET status='REVOKED' WHERE project_id=%s AND user_id=%s", (project_id, peer))
    assert client.get(f"/api/virtual-office/people/{peer}/projects", headers=root_headers).json()["projects"] == []
    assert client.put(
        f"/api/virtual-office/people/{peer}/global-role",
        json={"global_role": "GENERAL_ADMIN", "expected_version": 1}, headers=root_headers,
    ).status_code == 403
    monkeypatch.setenv("OFFICE_DEDICATED_DB", "1")
    monkeypatch.setenv("OFFICE_MFA_POLICY", "optional")
    optional = client.put(
        f"/api/virtual-office/people/{peer}/global-role",
        json={"global_role": "GENERAL_ADMIN", "expected_version": 1}, headers=root_headers,
    )
    assert optional.status_code == 200
    assert optional.json()["global_role"] == "GENERAL_ADMIN"


def test_people_directory_is_delegated_tenant_metadata_not_content(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)

    async def scenario():
        peer, _ = await crear_usuario()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (peer,))
        return peer

    peer = client.portal.call(scenario)
    response = client.get(f"/api/virtual-office/people?after_user_id={peer - 1}", headers=_headers(root, tv, mv, proof))
    assert response.status_code == 200
    row = next(item for item in response.json()["people"] if item["user_id"] == peer)
    assert set(row) == {"user_id", "tenant_id", "email", "account_class", "global_role", "status", "version", "delegation_status", "delegation_version"}
    assert "password" not in response.text.lower()


def test_root_can_promote_existing_profile_with_version_and_no_identity_conversion(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)

    async def scenario():
        target, _ = await crear_usuario()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status,version) VALUES (%s,'COLLABORATOR','ACTIVE',1)", (target,))
        before = (await sql("SELECT account_class FROM jax_users WHERE user_id=%s", (target,), True))[0][0]
        return target, before

    target, account_class = client.portal.call(scenario)
    headers = _headers(root, tv, mv, proof)
    promoted = client.put(f"/api/virtual-office/people/{target}/global-role", json={"global_role": "OFFICE_SUPERUSER", "expected_version": 1}, headers=headers)
    assert promoted.status_code == 200
    assert promoted.json() == {"user_id": target, "global_role": "OFFICE_SUPERUSER", "version": 2}
    assert client.portal.call(
        sql, "SELECT COUNT(*) FROM office_account_profile WHERE global_role='OFFICE_SUPERUSER' AND status='ACTIVE' "
             "AND user_id IN (%s,%s)", (root, target), True,
    ) == ((2,),)
    stale = client.put(f"/api/virtual-office/people/{target}/global-role", json={"global_role": "GENERAL_ADMIN", "expected_version": 1}, headers=headers)
    assert stale.status_code == 409
    assert client.portal.call(sql, "SELECT account_class,global_role,version FROM jax_users u JOIN office_account_profile p ON p.user_id=u.user_id WHERE u.user_id=%s", (target,), True) == ((account_class, "OFFICE_SUPERUSER", 2),)


def test_global_role_mutation_never_creates_unprofiled_office_binding(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    target, _ = client.portal.call(crear_usuario)
    response = client.put(f"/api/virtual-office/people/{target}/global-role", json={"global_role": "GENERAL_ADMIN", "expected_version": 1}, headers=_headers(root, tv, mv, proof))
    assert response.status_code == 404
    assert client.portal.call(sql, "SELECT user_id FROM office_account_profile WHERE user_id=%s", (target,), True) == ()


def test_non_office_actor_cannot_probe_people_targets_through_root_only_routes(client, monkeypatch):
    _configure_mfa(monkeypatch)

    async def scenario():
        actor, _ = await crear_usuario()
        target, _ = await crear_usuario()
        versions = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (actor,), True))[0]
        return actor, target, int(versions[0]), int(versions[1])

    actor, target, tv, mv = client.portal.call(scenario)
    headers = _headers(actor, tv, mv, 1)
    cases = (
        ("put", f"/api/virtual-office/people/{target}/profile", {"expected_version": 0}),
        ("delete", f"/api/virtual-office/people/{target}/profile?expected_version=1", None),
        ("put", f"/api/virtual-office/people/{target}/global-role", {"global_role": "GENERAL_ADMIN", "expected_version": 1}),
        ("put", f"/api/virtual-office/people/{target}/delegation", {"expected_version": 0}),
        ("delete", f"/api/virtual-office/people/{target}/delegation?expected_version=1", None),
    )
    for method, existing_path, body in cases:
        missing_path = existing_path.replace(f"/{target}", f"/{target + 1000000}", 1)
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        existing = getattr(client, method)(existing_path, **kwargs)
        missing = getattr(client, method)(missing_path, **kwargs)
        assert (existing.status_code, existing.json()) == (403, {"detail": "office_people_access_denied"})
        assert (missing.status_code, missing.json()) == (403, {"detail": "office_people_access_denied"})


def test_external_client_cannot_receive_administrative_global_role_or_appear_after_revocation(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)

    async def scenario():
        target, _ = await crear_usuario()
        await sql("UPDATE jax_users SET account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (target,))
        await sql("INSERT INTO office_account_profile (user_id,global_role,status,version) VALUES (%s,'COLLABORATOR','ACTIVE',1)", (target,))
        return target

    target = client.portal.call(scenario)
    headers = _headers(root, tv, mv, proof)
    denied = client.put(
        f"/api/virtual-office/people/{target}/global-role",
        json={"global_role": "GENERAL_ADMIN", "expected_version": 1}, headers=headers,
    )
    assert (denied.status_code, denied.json()) == (409, {"detail": "office_external_role_incompatible"})
    assert client.portal.call(
        sql, "SELECT global_role,version FROM office_account_profile WHERE user_id=%s", (target,), True,
    ) == (("COLLABORATOR", 1),)
    assert client.delete(
        f"/api/virtual-office/people/{target}/profile?expected_version=1", headers=headers,
    ).status_code == 200
    listed = client.get("/api/virtual-office/people", headers=headers)
    assert listed.status_code == 200
    assert target not in {row["user_id"] for row in listed.json()["people"]}


def test_root_enables_only_self_controlled_existing_profile_and_never_reactivates_revoked(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)

    async def scenario():
        target, _ = await crear_usuario()
        # A root/admin-originated completion is not a subject-controlled credential.
        await sql("INSERT INTO user_admin_audit (actor_user_id,target_user_id,action,detail,ts) VALUES (%s,%s,'password_reset_completed',JSON_OBJECT('source','test'),UTC_TIMESTAMP(6))", (root, target))
        return target

    target = client.portal.call(scenario)
    headers = _headers(root, tv, mv, proof)
    denied = client.put(f"/api/virtual-office/people/{target}/profile", json={"expected_version": 0}, headers=headers)
    assert denied.status_code == 403
    assert client.portal.call(sql, "SELECT user_id FROM office_account_profile WHERE user_id=%s", (target,), True) == ()

    async def own_control():
        return await sql("INSERT INTO user_admin_audit (actor_user_id,target_user_id,action,detail,ts) VALUES (%s,%s,'password_reset_completed',JSON_OBJECT('source','test'),UTC_TIMESTAMP(6))", (target, target))

    audit_id = client.portal.call(own_control)
    async def current_control():
        from db.transaccion import transaccion, AISLAMIENTO_ADMIN
        from virtual_office.bootstrap import credential_self_control_in_cursor
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            return await credential_self_control_in_cursor(cur, user_id=target)
    assert client.portal.call(current_control) == audit_id
    enabled = client.put(f"/api/virtual-office/people/{target}/profile", json={"expected_version": 0}, headers=headers)
    assert enabled.status_code == 200
    assert client.portal.call(
        sql, "SELECT status,bound_by_user_id,credential_audit_id FROM office_account_profile WHERE user_id=%s", (target,), True,
    ) == (("ACTIVE", root, audit_id),)
    revoked = client.delete(f"/api/virtual-office/people/{target}/profile?expected_version=1", headers=headers)
    assert revoked.status_code == 200
    assert client.portal.call(
        sql,
        "SELECT actor_user_id,target_user_id,action,version_before,version_after,reason_code "
        "FROM office_profile_revocation_event WHERE target_user_id=%s", (target,), True,
    ) == ((root, target, "REVOKE_PROFILE", 1, 2, "office_profile_revoke"),)
    assert revoked.status_code == 200
    retry = client.put(f"/api/virtual-office/people/{target}/profile", json={"expected_version": 2}, headers=headers)
    assert retry.status_code == 409
    assert client.portal.call(sql, "SELECT status FROM office_account_profile WHERE user_id=%s", (target,), True) == (("REVOKED",),)


def test_global_role_route_does_not_demote_a_root_without_recovery_ceremony(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    response = client.put(f"/api/virtual-office/people/{root}/global-role", json={"global_role": "COLLABORATOR", "expected_version": 1}, headers=_headers(root, tv, mv, proof))
    assert response.status_code == 409
    assert response.json() == {"detail": "office_root_demotion_requires_recovery"}


def test_root_grants_and_revokes_tenant_bound_administration_with_cas(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, root_tv, root_mv, root_proof = client.portal.call(_root_with_proof)
    manager, manager_tv, manager_mv, manager_proof = client.portal.call(_general_admin_with_proof)
    root_headers = _headers(root, root_tv, root_mv, root_proof)

    granted = client.put(
        f"/api/virtual-office/people/{manager}/delegation",
        json={"expected_version": 0}, headers=root_headers,
    )
    assert granted.status_code == 200
    assert granted.json() == {
        "user_id": manager, "tenant_id": 1, "capability": "ADMINISTER_OFFICE",
        "version": 1, "replayed": False,
    }
    replay = client.put(
        f"/api/virtual-office/people/{manager}/delegation",
        json={"expected_version": 1}, headers=root_headers,
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert client.get("/api/virtual-office/people", headers=_headers(manager, manager_tv, manager_mv, manager_proof)).status_code == 200

    revoked = client.delete(
        f"/api/virtual-office/people/{manager}/delegation?expected_version=1", headers=root_headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["version"] == 2
    assert client.get("/api/virtual-office/people", headers=_headers(manager, manager_tv, manager_mv, manager_proof)).status_code == 403
    revoke_replay = client.delete(
        f"/api/virtual-office/people/{manager}/delegation?expected_version=2", headers=root_headers,
    )
    assert revoke_replay.status_code == 200
    assert revoke_replay.json()["replayed"] is True


def test_delegation_uses_the_target_canonical_tenant_not_request_tenant(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, root_tv, root_mv, root_proof = client.portal.call(_root_with_proof)

    async def other_tenant_manager():
        suffix = uuid.uuid4().hex[:12]
        tenant_id = await sql(
            "INSERT INTO jax_tenants (name,plan,status) VALUES (%s,'test','active')",
            (f"office-delegation-{suffix}",),
        )
        user_id = await sql(
            "INSERT INTO jax_users (tenant_id,email,password_hash,role,status) VALUES (%s,%s,'x','operator','active')",
            (tenant_id, f"office-delegation-{suffix}@example.invalid"),
        )
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'GENERAL_ADMIN','ACTIVE')", (user_id,))
        token_version, mfa_version = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True))[0]
        proof = await sql(
            "INSERT INTO office_mfa_security_event "
            "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
            "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,UTC_TIMESTAMP(6))",
            (user_id, user_id, int(token_version), int(mfa_version), uuid.uuid4().bytes),
        )
        return user_id, int(tenant_id), int(token_version), int(mfa_version), int(proof)

    manager, tenant_id, manager_tv, manager_mv, manager_proof = client.portal.call(other_tenant_manager)
    response = client.put(
        f"/api/virtual-office/people/{manager}/delegation", json={"expected_version": 0},
        headers=_headers(root, root_tv, root_mv, root_proof),
    )
    assert response.status_code == 200
    assert response.json()["tenant_id"] == tenant_id
    assert client.get(
        "/api/virtual-office/people",
        headers=_headers(manager, manager_tv, manager_mv, manager_proof, tenant_id=tenant_id),
    ).status_code == 200
    assert client.portal.call(
        sql,
        "SELECT tenant_id FROM office_admin_delegation WHERE user_id=%s AND capability='ADMINISTER_OFFICE'",
        (manager,), True,
    ) == ((tenant_id,),)
