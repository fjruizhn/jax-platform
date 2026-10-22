"""Real-MariaDB delivery-finalization contracts for Office invitations."""
from __future__ import annotations

import uuid
from unittest.mock import ANY

from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.identidades import sql
from tests.test_office_projects_api import _office_root_with_current_proof
from virtual_office.invitation_config import InvitationConfig
from virtual_office.invitations import (
    finalize_invitation_delivery_in_cursor,
    issue_invitation_in_cursor,
    revoke_invitation_in_cursor,
    settle_stale_invitation_claims_in_cursor,
)
from virtual_office.mfa import MFAConfig


_MFA_CONFIG = MFAConfig(120, 3, 300, 300, 3)
_INVITATION_CONFIG = InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30)


def test_late_relay_acceptance_remains_observed_after_revocation(client):
    """A cancellation fences future SMTP work but cannot falsify relay truth."""
    issuer, _token_version, _mfa_version, proof_event_id = client.portal.call(
        _office_root_with_current_proof)

    async def scenario():
        project_id = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("invitation-outbox-" + uuid.uuid4().hex,))
        await sql(
            "INSERT INTO jax_project_scope "
            "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
            (project_id,))
        await sql(
            "INSERT INTO office_project_grant "
            "(project_id,user_id,grant_role,audience,status,granted_by) "
            "VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
            (project_id, issuer, issuer))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id,
                email="outbox-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                proof_event_id=proof_event_id, mfa_config=_MFA_CONFIG,
                invitation_config=_INVITATION_CONFIG, idempotency_key=str(uuid.uuid4()))
            attempt_id = str(uuid.uuid4())
            await cur.execute(
                "UPDATE office_invitation_outbox SET state='CLAIMED',attempt_id=%s,attempt_count=1,"
                "claimed_at=UTC_TIMESTAMP(6),updated_at=DATE_ADD(updated_at,INTERVAL 1 MICROSECOND) "
                "WHERE invitation_id=%s AND state='QUEUED'",
                (attempt_id, invitation.invitation_id))
            assert cur.rowcount == 1
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await revoke_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id,
                invitation_id=invitation.invitation_id, expected_version=1,
                proof_event_id=proof_event_id, mfa_config=_MFA_CONFIG,
                idempotency_key=str(uuid.uuid4())) == (2, False)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await finalize_invitation_delivery_in_cursor(
                cur, outbox_id=(await _outbox_id(cur, invitation.invitation_id)), attempt_id=attempt_id,
                relay_accepted=True, message_id="<late-relay@example.test>") is True
        invitation_row = await sql(
            "SELECT status,version FROM office_invitation WHERE invitation_id=%s",
            (invitation.invitation_id,), True)
        outbox_row = await sql(
            "SELECT state,cancel_requested,payload_ciphertext,message_id,result_code "
            "FROM office_invitation_outbox WHERE invitation_id=%s",
            (invitation.invitation_id,), True)
        events = await sql(
            "SELECT event_kind,observed_outbox_state FROM office_invitation_event "
            "WHERE invitation_id=%s ORDER BY event_id", (invitation.invitation_id,), True)
        return invitation_row, outbox_row, events

    invitation_row, outbox_row, events = client.portal.call(scenario)
    assert invitation_row == (("REVOKED", 2),)
    assert outbox_row == (("SMTP_ACCEPTED", 1, None, "<late-relay@example.test>", "SMTP_ACCEPTED"),)
    assert events == (("ISSUED", None), ("REVOKED", None), ("DELIVERY_OBSERVED", "SMTP_ACCEPTED"))


async def _outbox_id(cur, invitation_id: str) -> str:
    await cur.execute(
        "SELECT outbox_id FROM office_invitation_outbox WHERE invitation_id=%s FOR UPDATE",
        (invitation_id,))
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


def test_stale_lease_can_refine_only_its_same_attempt_to_relay_accepted(client):
    issuer, _token_version, _mfa_version, proof_event_id = client.portal.call(
        _office_root_with_current_proof)

    async def scenario():
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-stale-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(cur, actor_user_id=issuer, project_id=project_id,
                email="stale-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                proof_event_id=proof_event_id, mfa_config=_MFA_CONFIG,
                invitation_config=_INVITATION_CONFIG, idempotency_key=str(uuid.uuid4()))
            outbox_id = await _outbox_id(cur, invitation.invitation_id)
            attempt_id = str(uuid.uuid4())
            await cur.execute("UPDATE office_invitation_outbox SET state='CLAIMED',attempt_id=%s,attempt_count=1,claimed_at=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 31 SECOND),updated_at=UTC_TIMESTAMP(6) WHERE outbox_id=%s", (attempt_id, outbox_id))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await settle_stale_invitation_claims_in_cursor(cur, config=_INVITATION_CONFIG) == 1
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await finalize_invitation_delivery_in_cursor(cur, outbox_id=outbox_id, attempt_id=str(uuid.uuid4()), relay_accepted=True, message_id='<wrong@example.test>') is False
            assert await finalize_invitation_delivery_in_cursor(cur, outbox_id=outbox_id, attempt_id=attempt_id, relay_accepted=True, message_id='<late@example.test>') is True
        return await sql("SELECT state,attempt_id,message_id FROM office_invitation_outbox WHERE outbox_id=%s", (outbox_id,), True)

    assert client.portal.call(scenario) == (("SMTP_ACCEPTED", ANY, "<late@example.test>"),)
