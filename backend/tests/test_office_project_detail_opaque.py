"""Opaque project-detail responses for confidential Office scopes."""
from __future__ import annotations

import uuid

from tests.identidades import crear_usuario, sql
from tests.test_office_projects_api import (
    _configure_mfa, _headers, _office_root_with_current_proof, _proof_headers,
)


def test_project_detail_hides_cross_tenant_scope_like_a_nonexistent_id(client, monkeypatch):
    _configure_mfa(monkeypatch)

    async def scenario():
        actor, _ = await crear_usuario()
        await sql(
            "INSERT INTO office_account_profile (user_id,global_role,status) "
            "VALUES (%s,'COLLABORATOR','ACTIVE')", (actor,),
        )
        tenant = await sql(
            "INSERT INTO jax_tenants (name,plan,status) VALUES (%s,'test','active')",
            ("opaque-detail-tenant-" + uuid.uuid4().hex,),
        )
        project = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("opaque-detail-project-" + uuid.uuid4().hex,),
        )
        await sql(
            "INSERT INTO jax_project_scope "
            "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,%s,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
            (project, tenant),
        )
        classic = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("opaque-detail-classic-" + uuid.uuid4().hex,),
        )
        archived = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("opaque-detail-archived-" + uuid.uuid4().hex,),
        )
        await sql(
            "INSERT INTO jax_project_scope "
            "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,1,'CLASSIC','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6)),"
            "(%s,1,'OFFICE_CONFIDENTIAL','ARCHIVED',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
            (classic, archived),
        )
        return actor, project, classic, archived

    actor, foreign_project, classic_project, archived_project = client.portal.call(scenario)
    hidden = client.get(f"/api/virtual-office/projects/{foreign_project}", headers=_headers(actor))
    nonexistent = client.get(
        f"/api/virtual-office/projects/{foreign_project + 1000000}", headers=_headers(actor),
    )
    classic = client.get(f"/api/virtual-office/projects/{classic_project}", headers=_headers(actor))
    archived = client.get(f"/api/virtual-office/projects/{archived_project}", headers=_headers(actor))
    responses = (hidden, nonexistent, classic, archived)
    assert all(response.status_code == 404 for response in responses)
    assert all(response.json() == {"detail": "office_project_not_found"} for response in responses)
    assert all(response.headers["Cache-Control"] == "no-store" for response in responses)


def test_project_detail_returns_allowed_office_scope_after_canonical_authorization(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, token_version, mfa_version, proof = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project = await sql(
            "INSERT INTO projects (project_uuid,name,description,status) VALUES (UUID(),%s,%s,'active')",
            ("allowed-detail-" + uuid.uuid4().hex, "confidential detail"),
        )
        await sql(
            "INSERT INTO jax_project_scope "
            "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
            (project,),
        )
        return project

    project = client.portal.call(scenario)
    response = client.get(
        f"/api/virtual-office/projects/{project}",
        headers=_proof_headers(root, token_version, mfa_version, proof, str(uuid.uuid4())),
    )
    assert response.status_code == 200
    assert response.json()["id"] == project
    assert response.json()["description"] == "confidential detail"
    assert response.json()["capabilities"]["manage_invitations"] is False
