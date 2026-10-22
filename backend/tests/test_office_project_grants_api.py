from __future__ import annotations

import uuid

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

    replay = client.put(
        f"/api/virtual-office/projects/{project}/members/{target}",
        json={**body, "expected_version": 1}, headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    revoke_headers = {**_headers(root, tv, mv, proof), "Idempotency-Key": str(uuid.uuid4())}
    revoked = client.delete(
        f"/api/virtual-office/projects/{project}/members/{target}?expected_version=1", headers=revoke_headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["version"] == 2
    assert client.portal.call(
        sql,
        "SELECT m.status,g.status FROM jax_project_membership m JOIN office_project_grant g "
        "ON g.project_id=m.project_id AND g.user_id=m.user_id WHERE m.project_id=%s AND m.user_id=%s",
        (project, target), True,
    ) == (("REVOKED", "REVOKED"),)
