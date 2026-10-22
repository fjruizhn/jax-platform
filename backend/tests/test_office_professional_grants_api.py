from __future__ import annotations

import uuid

from tests.identidades import crear_usuario, sql
from tests.test_office_people_api import _configure_mfa, _headers, _root_with_proof


async def _project_member(root: int):
    target, _ = await crear_usuario()
    await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (target,))
    project = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("professional-" + uuid.uuid4().hex,))
    await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project,))
    await sql("INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))", (str(uuid.uuid4()), project, target))
    await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s)", (project, target, root))
    return project, target


def test_root_grants_professional_capability_with_versioned_replay(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    project, target = client.portal.call(_project_member, root)
    headers = _headers(root, tv, mv, proof)
    payload = {"basis": "human-review", "evidence_ref": "case:42", "expected_version": 0}
    granted = client.put(f"/api/virtual-office/projects/{project}/professionals/{target}", json=payload, headers=headers)
    assert granted.status_code == 200
    assert granted.json()["version"] == 1
    replay = client.put(f"/api/virtual-office/projects/{project}/professionals/{target}", json=payload, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    changed = client.put(f"/api/virtual-office/projects/{project}/professionals/{target}", json={"basis": "updated-review", "evidence_ref": "case:43", "expected_version": 1}, headers=headers)
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    revoked = client.delete(f"/api/virtual-office/projects/{project}/professionals/{target}?expected_version=2", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["version"] == 3
    replay_revoke = client.delete(f"/api/virtual-office/projects/{project}/professionals/{target}?expected_version=3", headers=headers)
    assert replay_revoke.status_code == 200
    assert replay_revoke.json()["replayed"] is True


def test_project_admin_cannot_mint_professional_capability(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, _, _, _ = client.portal.call(_root_with_proof)
    project, target = client.portal.call(_project_member, root)

    async def project_admin():
        user, _ = await crear_usuario()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (user,))
        versions = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user,), True))[0]
        proof = await sql("INSERT INTO office_mfa_security_event (event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,UTC_TIMESTAMP(6))", (user, user, versions[0], versions[1], uuid.uuid4().bytes))
        await sql("INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))", (str(uuid.uuid4()), project, user))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project, user, root))
        return user, int(versions[0]), int(versions[1]), int(proof)

    admin, tv, mv, proof = client.portal.call(project_admin)
    denied = client.put(f"/api/virtual-office/projects/{project}/professionals/{target}", json={"basis": "no", "evidence_ref": "no", "expected_version": 0}, headers=_headers(admin, tv, mv, proof))
    assert denied.status_code == 403
    assert client.portal.call(sql, "SELECT professional_grant_id FROM office_professional_grant WHERE project_id=%s AND user_id=%s", (project, target), True) == ()
