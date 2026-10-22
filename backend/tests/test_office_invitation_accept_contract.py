"""Small pure contract checks for the bearer-acceptance ingress."""
from __future__ import annotations

import inspect
import uuid
import asyncio
from functools import partial

import pytest
from pymysql.err import OperationalError
from api.virtual_office.invitations import InvitationAcceptRequest, accept_invitation
from db.seed import _hash
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.identidades import crear_usuario, sql
from tests.test_office_projects_api import _office_root_with_current_proof
from virtual_office.invitation_config import InvitationConfig as _InvitationConfig
from virtual_office.invitations import (
    InvitationError, _decode_outbox_payload, _proof_digest, accept_invitation_in_cursor,
    _translate_acceptance_deadline_guard, invitation_bearer_preflight, issue_invitation_in_cursor,
    revoke_invitation_in_cursor,
)
from virtual_office.mfa import MFAConfig

InvitationConfig = partial(_InvitationConfig, public_base_url="https://virtualoffice.example.test", email_locale="es")
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


def test_deadline_guard_translates_only_the_mariadb_trigger_error():
    class Cursor:
        async def execute(self, _query):
            return None

        async def fetchone(self):
            return (__import__("datetime").datetime.now(__import__("datetime").timezone.utc),)

    async def scenario():
        with pytest.raises(InvitationError) as expired:
            await _translate_acceptance_deadline_guard(
                Cursor(), OperationalError(1644, "office invitation expiry evidence denied"),
                expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        assert expired.value.status_code == 410
        unrelated = RuntimeError("programming defect")
        with pytest.raises(RuntimeError) as propagated:
            await _translate_acceptance_deadline_guard(
                Cursor(), unrelated,
                expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        assert propagated.value is unrelated

    asyncio.run(scenario())


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
                invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50),
                idempotency_key=str(uuid.uuid4()))
        rows = await sql(
            "SELECT i.target_user_id,s.target_account_class,s.audience FROM office_invitation i "
            "JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id WHERE i.invitation_id=%s",
            (invitation.invitation_id,), True)
        return rows[0]

    assert client.portal.call(scenario) == (None, "OFFICE_INTERNAL", "INTERNAL")


def test_issue_is_opaque_for_missing_and_incompatible_email_accounts(client):
    """Each address gets the same pending delivery; only a compatible account binds."""
    issuer, _token_version, _mfa_version, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project_id = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("invitation-opacity-" + uuid.uuid4().hex,))
        await sql(
            "INSERT INTO jax_project_scope "
            "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
            (project_id,))
        await sql(
            "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
            "VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
            (project_id, issuer, issuer))
        compatible, compatible_email = await crear_usuario()
        cross_tenant, cross_tenant_email = await crear_usuario()
        wrong_class, wrong_class_email = await crear_usuario()
        inactive, inactive_email = await crear_usuario()
        tenant_name = "invitation-opacity-tenant-" + uuid.uuid4().hex
        other_tenant = await sql("INSERT INTO jax_tenants (name,plan,status) VALUES (%s,'test','active')", (tenant_name,))
        await sql("UPDATE jax_users SET account_class='OFFICE_INTERNAL' WHERE user_id=%s", (compatible,))
        await sql("UPDATE jax_users SET tenant_id=%s,account_class='OFFICE_INTERNAL' WHERE user_id=%s",
                  (other_tenant, cross_tenant))
        await sql("UPDATE jax_users SET account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (wrong_class,))
        await sql("UPDATE jax_users SET account_class='OFFICE_INTERNAL',status='inactive' WHERE user_id=%s", (inactive,))
        addresses = [
            ("missing-" + uuid.uuid4().hex + "@example.test", None),
            (compatible_email, compatible),
            (cross_tenant_email, None),
            (wrong_class_email, None),
            (inactive_email, None),
        ]
        observed = []
        for email, expected_target in addresses:
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                invitation = await issue_invitation_in_cursor(
                    cur, actor_user_id=issuer, project_id=project_id, email=email, audience="INTERNAL",
                    proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                    invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50),
                    idempotency_key=str(uuid.uuid4()))
            row = (await sql(
                "SELECT i.target_user_id,i.status,o.state FROM office_invitation i "
                "JOIN office_invitation_outbox o ON o.invitation_id=i.invitation_id WHERE i.invitation_id=%s",
                (invitation.invitation_id,), True))[0]
            observed.append((row, expected_target))
        return observed

    for (target_user_id, status, outbox_state), expected_target in client.portal.call(scenario):
        assert target_user_id == expected_target
        assert (status, outbox_state) == ("ISSUED", "QUEUED")


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
                      invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50),
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
                      invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50),
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
        config = InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50)
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


def test_concurrent_cross_project_key_collision_is_a_stable_domain_conflict(client):
    issuer, _tv, _mv, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        projects = []
        for label in ("left", "right"):
            project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                                   ("invitation-cross-project-" + label + "-" + uuid.uuid4().hex,))
            await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
            await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))
            projects.append(project_id)
        key = str(uuid.uuid4())
        async def issue(project_id):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await issue_invitation_in_cursor(
                    cur, actor_user_id=issuer, project_id=project_id,
                    email="cross-" + str(project_id) + "-" + uuid.uuid4().hex + "@example.test",
                    audience="INTERNAL", proof_event_id=proof_event_id,
                    mfa_config=MFAConfig(120, 3, 300, 300, 3),
                    invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30,
                                                       stale_claim_batch_limit=50, expiry_sweep_batch_limit=50),
                    idempotency_key=key)
        outcomes = await asyncio.gather(*(issue(project_id) for project_id in projects), return_exceptions=True)
        invitations = await sql("SELECT COUNT(*) FROM office_invitation WHERE project_id IN (%s,%s)", tuple(projects), True)
        return outcomes, invitations

    outcomes, invitations = client.portal.call(scenario)
    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    errors = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], InvitationError)
    assert errors[0].code == "office_invitation_idempotency_conflict"
    assert invitations == ((1,),)


def test_accept_and_revoke_share_issuer_scope_then_invitation_lock_order(client):
    issuer, _tv, _mv, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-accept-revoke-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))
        config = InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30,
                                  stale_claim_batch_limit=50, expiry_sweep_batch_limit=50)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id,
                email="accept-revoke-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                invitation_config=config, idempotency_key=str(uuid.uuid4()))
            await cur.execute("SELECT payload_ciphertext FROM office_invitation_outbox WHERE invitation_id=%s", (invitation.invitation_id,))
            ciphertext = (await cur.fetchone())[0]
        bearer = _decode_outbox_payload(ciphertext)[1]
        start = asyncio.Event()
        async def accept():
            await start.wait()
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await accept_invitation_in_cursor(cur, accepting_user_id=None, accepting_email=None,
                                                          new_password_hash=_hash("accept-revoke-password"), bearer_token=bearer)
        async def revoke():
            await start.wait()
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await revoke_invitation_in_cursor(
                    cur, actor_user_id=issuer, project_id=project_id,
                    invitation_id=invitation.invitation_id, expected_version=1,
                    proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                    idempotency_key=str(uuid.uuid4()))
        tasks = (asyncio.create_task(accept()), asyncio.create_task(revoke()))
        start.set()
        outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10)
        terminal = await sql("SELECT status FROM office_invitation WHERE invitation_id=%s", (invitation.invitation_id,), True)
        return outcomes, terminal

    outcomes, terminal = client.portal.call(scenario)
    assert not any(getattr(outcome, "args", [None])[0] == 1213 for outcome in outcomes if isinstance(outcome, Exception))
    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert terminal[0][0] in {"ACCEPTED", "REVOKED"}


def test_concurrent_cross_project_revoke_key_collision_is_stable(client):
    issuer, _tv, _mv, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        config = InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30,
                                  stale_claim_batch_limit=50, expiry_sweep_batch_limit=50)
        invitations = []
        for label in ("left", "right"):
            project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                                   ("invitation-revoke-collision-" + label + "-" + uuid.uuid4().hex,))
            await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
            await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                invitation = await issue_invitation_in_cursor(
                    cur, actor_user_id=issuer, project_id=project_id,
                    email="revoke-" + label + "-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                    proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                    invitation_config=config, idempotency_key=str(uuid.uuid4()))
            invitations.append((project_id, invitation.invitation_id))
        key = str(uuid.uuid4())
        async def revoke(project_id, invitation_id):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await revoke_invitation_in_cursor(
                    cur, actor_user_id=issuer, project_id=project_id, invitation_id=invitation_id,
                    expected_version=1, proof_event_id=proof_event_id,
                    mfa_config=MFAConfig(120, 3, 300, 300, 3), idempotency_key=key)
        outcomes = await asyncio.gather(*(revoke(*item) for item in invitations), return_exceptions=True)
        statuses = await sql("SELECT status FROM office_invitation WHERE invitation_id IN (%s,%s) ORDER BY invitation_id", tuple(sorted(item[1] for item in invitations)), True)
        return outcomes, statuses

    outcomes, statuses = client.portal.call(scenario)
    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    errors = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], InvitationError)
    assert errors[0].code == "office_invitation_idempotency_conflict"
    assert sorted(status[0] for status in statuses) == ["ISSUED", "REVOKED"]


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
                    invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50),
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
                invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50),
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


def test_service_receipts_bind_issue_and_revoke_to_their_own_evidence(client):
    """The service emits the exact event authority, intent digest, and expiry it attests."""
    issuer, _tv, _mv, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-receipt-binding-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
                  (project_id, issuer, issuer))
        issue_key, revoke_key = str(uuid.uuid4()), str(uuid.uuid4())
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id,
                email="receipt-binding-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50),
                idempotency_key=issue_key)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await revoke_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id,
                invitation_id=invitation.invitation_id, expected_version=1,
                proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                idempotency_key=revoke_key) == (2, False)
        return await sql(
            "SELECT r.operation,r.authorization_event_id=e.authorization_event_id,"
            "r.request_digest=e.invocation_digest,"
            "(r.operation='REVOKE' OR r.expires_at=s.expires_at),"
            "r.expected_version,r.response_version "
            "FROM office_invitation_mutation_receipt r "
            "JOIN office_invitation i ON i.invitation_id=r.invitation_id "
            "JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id "
            "JOIN office_invitation_event e ON e.event_id=r.result_event_id "
            "WHERE r.idempotency_key IN (%s,%s) ORDER BY r.operation",
            (issue_key, revoke_key), True)

    assert client.portal.call(scenario) == (
        ("ISSUE", 1, 1, 1, 0, 1),
        ("REVOKE", 1, 1, 1, 1, 2),
    )


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
                invitation_config=InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50),
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


def test_delegated_acceptance_allows_an_issuance_proof_older_than_901_seconds(client):
    issuer, _tv, _mv, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-old-proof-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))
        # The proof is still inside the issuance window (1,000s), but beyond
        # the ordinary interactive 901s threshold accepted by the contract.
        versions = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (issuer,), True))[0]
        old_proof = await sql(
            "INSERT INTO office_mfa_security_event "
            "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
            "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 902 SECOND))",
            (issuer, issuer, *versions, uuid.uuid4().bytes))
        email = "old-proof-" + uuid.uuid4().hex + "@example.test"
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id, email=email, audience="INTERNAL",
                proof_event_id=old_proof, mfa_config=MFAConfig(120, 3, 300, 1000, 3),
                invitation_config=InvitationConfig(lifetime_seconds=3600, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50),
                idempotency_key=str(uuid.uuid4()))
        ciphertext = (await sql("SELECT payload_ciphertext FROM office_invitation_outbox WHERE invitation_id=%s", (invitation.invitation_id,), True))[0][0]
        _recipient, bearer, _expiry = _decode_outbox_payload(ciphertext)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            accepted = await accept_invitation_in_cursor(cur, accepting_user_id=None, accepting_email=None,
                                                          new_password_hash=_hash("old-proof-password"), bearer_token=bearer)
        return accepted, await sql("SELECT status FROM office_invitation WHERE invitation_id=%s", (invitation.invitation_id,), True)

    accepted, status = client.portal.call(scenario)
    assert accepted is False
    assert status == (("ACCEPTED",),)


def test_delegated_acceptance_rechecks_versions_grant_and_live_project_with_rollback(client):
    from jax.memory.project_authority import AuthorizationDenied, ProjectStateConflict

    issuer, _tv, _mv, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        prior_role = (await sql("SELECT global_role FROM office_account_profile WHERE user_id=%s", (issuer,), True))[0][0]
        # Exercise the project-admin grant path instead of the root bypass.
        await sql("UPDATE office_account_profile SET global_role='COLLABORATOR' WHERE user_id=%s", (issuer,))
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-recheck-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,grant_origin,created_at,created_by,updated_at) VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE','OFFICE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (str(uuid.uuid4()), project_id, issuer))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))

        async def issue(label):
            email = f"recheck-{label}-{uuid.uuid4().hex}@example.test"
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                invitation = await issue_invitation_in_cursor(cur, actor_user_id=issuer, project_id=project_id,
                    email=email, audience="INTERNAL", proof_event_id=proof_event_id,
                    mfa_config=MFAConfig(120, 3, 300, 300, 3),
                    invitation_config=InvitationConfig(lifetime_seconds=3600, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50), idempotency_key=str(uuid.uuid4()))
            ciphertext = (await sql("SELECT payload_ciphertext FROM office_invitation_outbox WHERE invitation_id=%s", (invitation.invitation_id,), True))[0][0]
            return invitation.invitation_id, email, _decode_outbox_payload(ciphertext)[1]

        results = []
        invitation_id, email, bearer = await issue("ordinary-login")
        await sql("UPDATE jax_users SET token_version=token_version+1 WHERE user_id=%s", (issuer,))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await accept_invitation_in_cursor(
                cur, accepting_user_id=None, accepting_email=None,
                new_password_hash=_hash("ordinary-login-password"), bearer_token=bearer) is False
        await sql("UPDATE jax_users SET token_version=token_version-1 WHERE user_id=%s", (issuer,))
        assert await sql("SELECT status FROM office_invitation WHERE invitation_id=%s", (invitation_id,), True) == (("ACCEPTED",),)
        for label, deny, restore in (
            ("mfa", "UPDATE jax_users SET mfa_version=mfa_version+1 WHERE user_id=%s", "UPDATE jax_users SET mfa_version=mfa_version-1 WHERE user_id=%s"),
            ("grant", "UPDATE office_project_grant SET status='REVOKED' WHERE project_id=%s AND user_id=%s", "UPDATE office_project_grant SET status='ACTIVE' WHERE project_id=%s AND user_id=%s"),
            ("project", "UPDATE jax_project_scope SET status='ARCHIVED' WHERE project_id=%s", "UPDATE jax_project_scope SET status='ACTIVE' WHERE project_id=%s"),
        ):
            invitation_id, email, bearer = await issue(label)
            args = (issuer,) if label == "mfa" else ((project_id, issuer) if label == "grant" else (project_id,))
            await sql(deny, args)
            try:
                async with transaccion(AISLAMIENTO_ADMIN) as cur:
                    await accept_invitation_in_cursor(cur, accepting_user_id=None, accepting_email=None,
                                                      new_password_hash=_hash("rollback-password"), bearer_token=bearer)
            except (AuthorizationDenied, ProjectStateConflict, InvitationError) as denied:
                assert denied
            else:
                raise AssertionError(f"delegated acceptance unexpectedly survived {label} revocation")
            await sql(restore, args)
            invitation = await sql("SELECT status,accepted_user_id FROM office_invitation WHERE invitation_id=%s", (invitation_id,), True)
            identities = await sql("SELECT COUNT(*) FROM jax_users WHERE email=%s", (email,), True)
            grants = await sql("SELECT COUNT(*) FROM office_project_grant WHERE project_id=%s AND user_id IN (SELECT user_id FROM jax_users WHERE email=%s)", (project_id, email), True)
            results.append((invitation, identities, grants))
        await sql("UPDATE office_account_profile SET global_role=%s WHERE user_id=%s", (prior_role, issuer))
        return results

    for invitation, identities, grants in client.portal.call(scenario):
        assert invitation == (("ISSUED", None),)
        assert identities == ((0,),)
        assert grants == ((0,),)


def test_bearer_preflight_cannot_authorize_a_raced_revocation(client):
    issuer, _tv, _mv, proof_event_id = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-preflight-race-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(cur, actor_user_id=issuer, project_id=project_id,
                email="preflight-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                proof_event_id=proof_event_id, mfa_config=MFAConfig(120, 3, 300, 300, 3),
                invitation_config=InvitationConfig(lifetime_seconds=3600, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50), idempotency_key=str(uuid.uuid4()))
        cipher = (await sql("SELECT payload_ciphertext FROM office_invitation_outbox WHERE invitation_id=%s", (invitation.invitation_id,), True))[0][0]
        bearer = _decode_outbox_payload(cipher)[1]
        assert await invitation_bearer_preflight(bearer) is True
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await revoke_invitation_in_cursor(cur, actor_user_id=issuer, project_id=project_id,
                invitation_id=invitation.invitation_id, expected_version=1, proof_event_id=proof_event_id,
                mfa_config=MFAConfig(120, 3, 300, 300, 3), idempotency_key=str(uuid.uuid4())) == (2, False)
        with pytest.raises(InvitationError, match="office_invitation_invalid"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await accept_invitation_in_cursor(cur, accepting_user_id=None, accepting_email=None,
                                                  new_password_hash=_hash("race-password"), bearer_token=bearer)
        return await sql("SELECT status,accepted_user_id FROM office_invitation WHERE invitation_id=%s", (invitation.invitation_id,), True)

    assert client.portal.call(scenario) == (("REVOKED", None),)
