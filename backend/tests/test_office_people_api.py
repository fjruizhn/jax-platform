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


def _headers(user_id: int, token_version: int, mfa_version: int, proof: int):
    return {"Authorization": "Bearer " + create_access_token(
        str(user_id), "1", "operator", token_version,
        mfa_proof_event_id=proof, mfa_version=mfa_version, mfa_at=int(time.time()),
    )}


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
    assert set(row) == {"user_id", "tenant_id", "email", "account_class", "global_role", "status", "version"}
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


def test_global_role_route_does_not_demote_a_root_without_recovery_ceremony(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_root_with_proof)
    response = client.put(f"/api/virtual-office/people/{root}/global-role", json={"global_role": "COLLABORATOR", "expected_version": 1}, headers=_headers(root, tv, mv, proof))
    assert response.status_code == 409
    assert response.json() == {"detail": "office_root_demotion_requires_recovery"}
