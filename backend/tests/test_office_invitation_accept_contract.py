"""Small pure contract checks for the bearer-acceptance ingress."""
from __future__ import annotations

import inspect
import uuid
import asyncio

import pytest
from api.virtual_office.invitations import InvitationAcceptRequest, accept_invitation
from db.seed import _hash
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.identidades import sql
from tests.test_office_projects_api import _office_root_with_current_proof
from virtual_office.invitation_config import InvitationConfig
from virtual_office.invitations import (
    InvitationError, _decode_outbox_payload, _proof_digest, accept_invitation_in_cursor,
    issue_invitation_in_cursor, revoke_invitation_in_cursor,
)
from virtual_office.mfa import MFAConfig
import virtual_office.invitations as invitation_service


def test_accept_request_exposes_only_token_and_optional_password():
    fields = InvitationAcceptRequest.model_fields
    assert set(fields) == {"token", "password"}
    assert fields["password"].default is None
    assert "idempotency_key" not in inspect.signature(accept_invitation).parameters


def test_immutable_issuance_proof_supports_unbound_new_identity():
    common = dict(
        token_hash=b"x" * 32, email="new@example.test", tenant_id=1, project_id=2,
        recipient_group_id=None, account_class="OFFICE_INTERNAL", actor_user_id=3,
        profile_version=1, grant_version=2, proof_event_id=4, mfa_version=5,
        issued_at=__import__("datetime").datetime(2026, 1, 1),
        expires_at=__import__("datetime").datetime(2026, 1, 2), authorization_event_id=6,
    )
    assert _proof_digest(target_user_id=None, **common) != _proof_digest(target_user_id=7, **common)


def test_issue_derives_an_unbound_internal_target_from_the_email(client):
    issuer, _token_version, _mfa_version, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project_id = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("invitation-issue-" + uuid.uuid4().hex,))
        await sql(
            "INSERT INTO jax_project_scope "
            "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
            (project_id,))
        await sql(
            "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
            "VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
            (project_id, issuer, issuer))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id,
                email="new-invitee@example.test", audience="INTERNAL",
                proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30),
                idempotency_key=str(uuid.uuid4()))
        rows = await sql(
            "SELECT i.target_user_id,s.target_account_class,s.audience FROM office_invitation i "
            "JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id WHERE i.invitation_id=%s",
            (invitation.invitation_id,), True)
        return rows[0]

    assert client.portal.call(scenario) == (None, "OFFICE_INTERNAL", "INTERNAL")


def test_issue_idempotency_replays_exact_intent_and_rejects_payload_drift(client):
    issuer, _token_version, _mfa_version, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project_id = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("invitation-replay-" + uuid.uuid4().hex,))
        await sql(
            "INSERT INTO jax_project_scope "
            "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
            (project_id,))
        await sql(
            "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
            "VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
            (project_id, issuer, issuer))
        key = str(uuid.uuid4())
        kwargs = dict(actor_user_id=issuer, project_id=project_id,
                      email="replay-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                      proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                      invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30),
                      idempotency_key=key)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            first = await issue_invitation_in_cursor(cur, **kwargs)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            replay = await issue_invitation_in_cursor(cur, **kwargs)
        assert replay.invitation_id == first.invitation_id and replay.replayed is True
        with pytest.raises(InvitationError, match="office_invitation_idempotency_conflict"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await issue_invitation_in_cursor(cur, **{**kwargs, "email": "other-" + uuid.uuid4().hex + "@example.test"})
        receipts = await sql(
            "SELECT COUNT(*) FROM office_invitation_mutation_receipt WHERE idempotency_key=%s", (key,), True)
        return receipts

    assert client.portal.call(scenario) == ((1,),)


def test_concurrent_same_issue_key_commits_one_invitation_and_one_receipt(client):
    issuer, _token_version, _mfa_version, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project_id = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("invitation-race-" + uuid.uuid4().hex,))
        await sql(
            "INSERT INTO jax_project_scope "
            "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
            (project_id,))
        await sql(
            "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
            "VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
            (project_id, issuer, issuer))
        kwargs = dict(actor_user_id=issuer, project_id=project_id,
                      email="race-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                      proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                      invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30),
                      idempotency_key=str(uuid.uuid4()))

        async def issue_once():
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await issue_invitation_in_cursor(cur, **kwargs)

        first, second = await asyncio.gather(issue_once(), issue_once())
        assert first.invitation_id == second.invitation_id
        assert sorted((first.replayed, second.replayed)) == [False, True]
        invitations = await sql("SELECT COUNT(*) FROM office_invitation WHERE project_id=%s", (project_id,), True)
        receipts = await sql("SELECT COUNT(*) FROM office_invitation_mutation_receipt WHERE idempotency_key=%s",
                             (kwargs["idempotency_key"],), True)
        return invitations, receipts

    assert client.portal.call(scenario) == (((1,),), ((1,),))


def test_cross_actor_key_collision_creates_no_second_invitation(client):
    first, _first_tv, _first_mv, first_proof = client.portal.call(_office_root_with_current_proof)
    second, _tv, _mv, second_proof = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-collision-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s),(%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
                  (project_id, first, first, project_id, second, first))
        key = str(uuid.uuid4())
        config = InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            winner = await issue_invitation_in_cursor(cur, actor_user_id=first, project_id=project_id,
                email="winner-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                proof_event_id=first_proof, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                invitation_config=config, idempotency_key=key)
        with pytest.raises(InvitationError, match="office_invitation_idempotency_conflict"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await issue_invitation_in_cursor(cur, actor_user_id=second, project_id=project_id,
                    email="loser-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                    proof_event_id=second_proof, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                    invitation_config=config, idempotency_key=key)
        count = await sql("SELECT COUNT(*) FROM office_invitation WHERE project_id=%s", (project_id,), True)
        return winner.invitation_id, count
    invitation_id, count = client.portal.call(scenario)
    assert invitation_id and count == ((1,),)


def test_receipt_write_failure_rolls_back_issue_companions(client, monkeypatch):
    issuer, _tv, _mv, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def fail_receipt(*_args, **_kwargs):
        raise RuntimeError("receipt storage unavailable")

    monkeypatch.setattr(invitation_service, "_write_mutation_receipt", fail_receipt)

    async def scenario():
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-receipt-rollback-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))
        with pytest.raises(RuntimeError, match="receipt storage unavailable"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await issue_invitation_in_cursor(cur, actor_user_id=issuer, project_id=project_id,
                    email="rollback-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                    proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                    invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30),
                    idempotency_key=str(uuid.uuid4()))
        return await sql("SELECT COUNT(*) FROM office_invitation WHERE project_id=%s", (project_id,), True)

    assert client.portal.call(scenario) == ((0,),)


def test_revoke_replay_reauthorizes_and_new_key_cannot_revoke_terminal_invitation(client):
    issuer, _tv, _mv, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-revoke-replay-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))
        config = MFAConfig(120, 3, 300, 300, 3)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(cur, actor_user_id=issuer, project_id=project_id,
                email="revoke-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                proof_event_id=proof_event_id, mfa_config=config,
                invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30),
                idempotency_key=str(uuid.uuid4()))
        key = str(uuid.uuid4())
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await revoke_invitation_in_cursor(cur, actor_user_id=issuer, project_id=project_id,
                invitation_id=invitation.invitation_id, expected_version=1, proof_event_id=proof_event_id,
                mfa_config=config, idempotency_key=key) == (2, False)
        with pytest.raises(InvitationError, match="office_invitation_terminal"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await revoke_invitation_in_cursor(cur, actor_user_id=issuer, project_id=project_id,
                    invitation_id=invitation.invitation_id, expected_version=1, proof_event_id=proof_event_id,
                    mfa_config=config, idempotency_key=str(uuid.uuid4()))
        await sql("UPDATE jax_users SET mfa_version=mfa_version+1 WHERE user_id=%s", (issuer,))
        with pytest.raises(InvitationError, match="office_invitation_issuer_denied"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await revoke_invitation_in_cursor(cur, actor_user_id=issuer, project_id=project_id,
                    invitation_id=invitation.invitation_id, expected_version=1, proof_event_id=proof_event_id,
                    mfa_config=config, idempotency_key=key)

    client.portal.call(scenario)


def test_bearer_acceptance_creates_only_the_invited_identity(client, monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120",
        "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300",
        "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)
    issuer, _token_version, _mfa_version, proof_event_id = client.portal.call(_office_root_with_current_proof)
    password_hash = _hash("password-for-new-invitee")
    invitee_email = "accept-" + uuid.uuid4().hex + "@example.test"

    async def scenario():
        project_id = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("invitation-accept-" + uuid.uuid4().hex,))
        await sql(
            "INSERT INTO jax_project_scope "
            "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
            (project_id,))
        await sql(
            "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
            "VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
            (project_id, issuer, issuer))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id,
                email=invitee_email, audience="INTERNAL",
                proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30),
                idempotency_key=str(uuid.uuid4()))
        ciphertext = (await sql(
            "SELECT o.payload_ciphertext FROM office_invitation_outbox o WHERE o.invitation_id=%s",
            (invitation.invitation_id,), True))[0][0]
        _recipient, bearer_token, _expires_at = _decode_outbox_payload(ciphertext)
        async def consume(candidate_hash):
            try:
                async with transaccion(AISLAMIENTO_ADMIN) as cur:
                    return await accept_invitation_in_cursor(
                        cur, accepting_user_id=None, accepting_email=None, new_password_hash=candidate_hash,
                        bearer_token=bearer_token)
            except InvitationError as error:
                return error.code
        first, second = await asyncio.gather(
            consume(password_hash), consume(_hash("a-different-password")))
        assert sorted((first, second), key=str) == [False, "office_invitation_existing_session_required"]
        with pytest.raises(InvitationError, match="office_invitation_existing_session_required"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await accept_invitation_in_cursor(
                    cur, accepting_user_id=None, accepting_email=None,
                    new_password_hash=_hash("a-different-password"), bearer_token=bearer_token)
        rows = await sql(
            "SELECT u.email,u.account_class,p.status,g.grant_role FROM jax_users u "
            "JOIN office_account_profile p ON p.user_id=u.user_id "
            "JOIN office_project_grant g ON g.user_id=u.user_id AND g.project_id=%s "
                "WHERE u.email=%s", (project_id, invitee_email), True)
        return rows[0]

    assert client.portal.call(scenario) == (invitee_email, "OFFICE_INTERNAL", "ACTIVE", "COLLABORATOR")
