"""Measured EXPLAIN contracts for the 3C1 Office administration paths.

These are the exact bound statements used by the routes/repository.  The
fixture deliberately adds a directory skew before asking MariaDB for plans:
an index that looks optional with two rows must still remain a viable access
path once the Office directory has grown.
"""
from __future__ import annotations

import uuid

from tests.identidades import crear_usuario, sql
from tests.test_office_people_api import _configure_mfa, _headers, _root_with_proof
from tests.test_office_project_grants_api import _office_project_and_unassigned_member


async def _assert_hot_plan(statement: str, params: tuple[object, ...]):
    """Return a real MariaDB plan while rejecting full/sort/temp hot paths."""
    rows = await sql("EXPLAIN " + statement, params, True)
    assert rows, statement
    assert all(str(row[3]).upper() != "ALL" for row in rows), (statement, rows)
    extras = " ".join(str(row[-1] or "") for row in rows).lower()
    assert "filesort" not in extras, (statement, rows)
    assert "temporary" not in extras, (statement, rows)
    assert all(row[5] is not None for row in rows), (statement, rows)
    return rows


def test_3c1_hot_read_plans_cover_directory_receipt_identity_and_authority(client, monkeypatch):
    """Guard every SQL lookup that 3C1 adds or makes load-bearing."""
    _configure_mfa(monkeypatch)
    root, token_version, mfa_version, proof = client.portal.call(_root_with_proof)

    async def seed_and_measure():
        # Directory skew: profiles are permanent Office identities and the
        # list route is cursor ordered by the canonical user id.
        for number in range(160):
            user_id, _ = await crear_usuario()
            await sql(
                "INSERT INTO office_account_profile (user_id,global_role,status) "
                "VALUES (%s,'COLLABORATOR','ACTIVE')", (user_id,)
            )

        project, target = await _office_project_and_unassigned_member()
        # Load-bearing authority rows for the repository's exact lookups.
        await sql(
            "INSERT INTO office_admin_delegation "
            "(tenant_id,user_id,capability,status,granted_by) "
            "VALUES (1,%s,'ADMINISTER_OFFICE','ACTIVE',%s)", (root, root),
        )
        await sql(
            "INSERT INTO office_professional_grant "
            "(project_id,user_id,capability,basis,evidence_ref,status,granted_by) "
            "VALUES (%s,%s,'PROFESSIONAL_ACTION','test','test','ACTIVE',%s)",
            (project, root, root),
        )

        people = (
            "SELECT u.user_id,u.tenant_id,u.email,u.account_class,p.global_role,p.status,p.version "
            "FROM office_account_profile p JOIN jax_users u ON u.user_id=p.user_id "
            "JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
            "WHERE u.status='active' AND t.status='active' AND p.status='ACTIVE' "
            "AND u.tenant_id=%s AND u.user_id>%s ORDER BY u.user_id LIMIT %s"
        )
        profile = (
            "SELECT u.status,u.account_class,t.status,p.status,p.version "
            "FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
            "LEFT JOIN office_account_profile p ON p.user_id=u.user_id "
            "WHERE u.user_id=%s FOR UPDATE"
        )
        actor_target = (
            "SELECT u.tenant_id,u.status,u.account_class,t.status,p.status,p.global_role "
            "FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
            "LEFT JOIN office_account_profile p ON p.user_id=u.user_id "
            "WHERE u.user_id=%s FOR UPDATE"
        )
        delegation = (
            "SELECT status FROM office_admin_delegation "
            "WHERE tenant_id=%s AND user_id=%s AND capability=%s FOR UPDATE"
        )
        professional = (
            "SELECT status FROM office_professional_grant "
            "WHERE project_id=%s AND user_id=%s AND capability=%s FOR UPDATE"
        )
        plans = {
            "people": await _assert_hot_plan(people, (1, 0, 51)),
            "profile": await _assert_hot_plan(profile, (target,)),
            "actor_target": await _assert_hot_plan(actor_target, (root,)),
            "delegation": await _assert_hot_plan(delegation, (1, root, "ADMINISTER_OFFICE")),
            "professional": await _assert_hot_plan(professional, (project, root, "PROFESSIONAL_ACTION")),
        }
        return plans

    plans = client.portal.call(seed_and_measure)
    # The list route's driving key must be deterministic; all remaining
    # lookups are exact primary/unique probes and the common helper already
    # rejects plan regressions such as ALL/filesort/temporary.
    assert plans["people"][0][5] == "PRIMARY"

    project, target = client.portal.call(_office_project_and_unassigned_member)
    key = str(uuid.uuid4())
    response = client.put(
        f"/api/virtual-office/projects/{project}/members/{target}",
        json={"grant_role": "COLLABORATOR", "audience": "INTERNAL", "expected_version": 0},
        headers={**_headers(root, token_version, mfa_version, proof), "Idempotency-Key": key},
    )
    assert response.status_code == 200
    replay = client.put(
        f"/api/virtual-office/projects/{project}/members/{target}",
        json={"grant_role": "COLLABORATOR", "audience": "INTERNAL", "expected_version": 0},
        headers={**_headers(root, token_version, mfa_version, proof), "Idempotency-Key": key},
    )
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    receipt_plan = client.portal.call(
        _assert_hot_plan,
        "SELECT request_digest,jax_membership_id,jax_event_id,office_grant_version,office_grant_status,"
        "external_relation_id,external_relation_version,recipient_group_id,grant_role,audience "
        "FROM office_membership_mutation_receipt WHERE namespace='OFFICE_CONFIDENTIAL' "
        "AND idempotency_key=%s FOR UPDATE",
        (key,),
    )
    assert receipt_plan[0][5] == "PRIMARY"
