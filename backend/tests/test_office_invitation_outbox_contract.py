"""Real-MariaDB delivery-finalization contracts for Office invitations."""
from __future__ import annotations

import asyncio
from functools import partial
from email.message import EmailMessage
import uuid
from unittest.mock import ANY

import smtp_config
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.identidades import sql
from tests.test_office_projects_api import _office_root_with_current_proof
from virtual_office.invitation_config import InvitationConfig as _InvitationConfig
from virtual_office.invitation_email_i18n import (
    DEFAULT_INVITATION_EMAIL_LOCALE, render_invitation_email,
)
from virtual_office import invitations as invitation_service
from virtual_office.invitations import (
    ClaimedInvitationDelivery,
    _decode_outbox_payload,
    _invitation_email,
    finalize_invitation_delivery_in_cursor,
    deliver_one_invitation,
    issue_invitation_in_cursor,
    revoke_invitation_in_cursor,
    settle_stale_invitation_claims_in_cursor,
)
from virtual_office.mfa import MFAConfig

InvitationConfig = partial(_InvitationConfig, public_base_url="https://virtualoffice.example.test")


_MFA_CONFIG = MFAConfig(120, 3, 300, 300, 3)
_INVITATION_CONFIG = InvitationConfig(lifetime_seconds=60, claim_timeout_seconds=30, stale_claim_batch_limit=50, expiry_sweep_batch_limit=50)


def test_delivery_uses_configured_https_fragment_link_and_localized_templates(client, monkeypatch):
    """The SMTP boundary receives a clickable fragment URL, never a raw code."""
    issuer, _token_version, _mfa_version, proof_event_id = client.portal.call(
        _office_root_with_current_proof)
    config = _InvitationConfig(
        lifetime_seconds=60, claim_timeout_seconds=30, stale_claim_batch_limit=50,
        expiry_sweep_batch_limit=50, public_base_url="https://virtualoffice.example.test",
        )
    captured = {}

    async def fake_settings():
        return object()

    def fake_message(settings, recipient, subject, text, body_html):
        captured.update(recipient=recipient, subject=subject, text=text, html=body_html)
        message = EmailMessage()
        message["Message-ID"] = "<office-invitation@example.test>"
        return message

    def fake_send(settings, message):
        captured["sent"] = message["Message-ID"]

    monkeypatch.setattr(smtp_config, "cargar_settings", fake_settings)
    monkeypatch.setattr(smtp_config, "construir_mensaje", fake_message)
    monkeypatch.setattr(smtp_config, "enviar", fake_send)
    monkeypatch.setenv("OFFICE_INVITATION_LIFETIME_SECONDS", "60")
    monkeypatch.setenv("OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT", "50")
    monkeypatch.setenv("OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT", "50")
    monkeypatch.setenv("OFFICE_INVITATION_DELIVERY_BATCH_LIMIT", "25")
    monkeypatch.setenv("OFFICE_INVITATION_PUBLIC_BASE_URL", config.public_base_url)

    async def scenario():
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-mail-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id,
                email="mail-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                proof_event_id=proof_event_id, mfa_config=_MFA_CONFIG,
                invitation_config=config, idempotency_key=str(uuid.uuid4()))
            await cur.execute(
                "SELECT outbox_id,payload_ciphertext FROM office_invitation_outbox "
                "WHERE invitation_id=%s FOR UPDATE", (invitation.invitation_id,))
            outbox_id, ciphertext = await cur.fetchone()
            recipient, bearer_token, expires_at = _decode_outbox_payload(ciphertext)
            attempt_id = str(uuid.uuid4())
            await cur.execute(
                "UPDATE office_invitation_outbox SET state='CLAIMED',attempt_id=%s,attempt_count=1,"
                "claimed_at=UTC_TIMESTAMP(6),updated_at=DATE_ADD(updated_at,INTERVAL 1 MICROSECOND) "
                "WHERE outbox_id=%s AND state='QUEUED'", (attempt_id, outbox_id))
            assert cur.rowcount == 1
            return invitation, ClaimedInvitationDelivery(
                str(outbox_id), attempt_id, invitation.invitation_id, recipient, bearer_token, expires_at)

    invitation, delivery = client.portal.call(scenario)

    async def claim_only_test_delivery(_cur):
        return delivery

    monkeypatch.setattr(invitation_service, "claim_next_invitation_delivery_in_cursor", claim_only_test_delivery)
    assert client.portal.call(deliver_one_invitation) is True
    assert captured["sent"] == "<office-invitation@example.test>"
    expected_prefix = "https://virtualoffice.example.test/office/invitation#token="
    assert expected_prefix in captured["text"]
    assert expected_prefix in captured["html"]
    assert "<code>" not in captured["html"]
    assert captured["recipient"].startswith("mail-")
    assert invitation.invitation_id not in captured["text"]
    assert client.portal.call(
        sql, "SELECT state FROM office_invitation_outbox WHERE invitation_id=%s",
        (invitation.invitation_id,), True) == (("SMTP_ACCEPTED",),)


def test_email_catalog_uses_recipient_locale_or_documented_default_and_percent_encodes_tokens():
    bearer = "a" * 42 + "+"
    delivery = ClaimedInvitationDelivery("outbox", "attempt", "invitation", "ana@example.test", bearer, "2026-10-22T12:00:00+00:00")
    url = invitation_service._invitation_acceptance_url(
        _InvitationConfig(60, 30, 50, 50, "https://virtualoffice.example.test"), delivery.bearer_token)
    subject, text, body_html = render_invitation_email(
        recipient_locale="en", url=url, expires_at=delivery.expires_at)
    assert subject == "Axioma — Virtual Office invitation"
    assert "Accept invitation" in body_html
    assert "https://virtualoffice.example.test/office/invitation#token=" + "a" * 42 + "%2B" in text
    default_subject, _default_text, _default_html = render_invitation_email(
        recipient_locale=None, url=url, expires_at=delivery.expires_at)
    assert DEFAULT_INVITATION_EMAIL_LOCALE == "es"
    assert default_subject == "Axioma — Invitación a Oficina Virtual"


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


def test_delivery_maintenance_expires_terminal_unknown_without_rewriting_transport_history(client, monkeypatch):
    issuer, _token_version, _mfa_version, proof_event_id = client.portal.call(
        _office_root_with_current_proof)
    monkeypatch.setenv("OFFICE_INVITATION_LIFETIME_SECONDS", "1")
    monkeypatch.setenv("OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT", "50")
    monkeypatch.setenv("OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT", "1")
    monkeypatch.setenv("OFFICE_INVITATION_DELIVERY_BATCH_LIMIT", "25")
    monkeypatch.setenv("OFFICE_INVITATION_PUBLIC_BASE_URL", "https://virtualoffice.example.test")

    async def no_delivery(_cur):
        return None
    monkeypatch.setattr(invitation_service, "claim_next_invitation_delivery_in_cursor", no_delivery)

    async def scenario():
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-expiry-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))
        config = InvitationConfig(lifetime_seconds=1, claim_timeout_seconds=30,
                                  stale_claim_batch_limit=50, expiry_sweep_batch_limit=1)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id,
                email="expiry-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                proof_event_id=proof_event_id, mfa_config=_MFA_CONFIG,
                invitation_config=config, idempotency_key=str(uuid.uuid4()))
            outbox_id = await _outbox_id(cur, invitation.invitation_id)
            attempt_id = str(uuid.uuid4())
            await cur.execute("UPDATE office_invitation_outbox SET state='CLAIMED',attempt_id=%s,attempt_count=1,claimed_at=UTC_TIMESTAMP(6),updated_at=DATE_ADD(updated_at,INTERVAL 1 MICROSECOND) WHERE outbox_id=%s", (attempt_id, outbox_id))
            assert await finalize_invitation_delivery_in_cursor(
                cur, outbox_id=outbox_id, attempt_id=attempt_id, relay_accepted=False,
                result_code="SMTP_UNKNOWN") is True
        await asyncio.sleep(1.1)
        assert await deliver_one_invitation() is False
        return await sql("SELECT i.status,o.state,o.result_code FROM office_invitation i JOIN office_invitation_outbox o ON o.invitation_id=i.invitation_id WHERE i.invitation_id=%s", (invitation.invitation_id,), True)

    assert client.portal.call(scenario) == (("EXPIRED", "UNKNOWN", "SMTP_UNKNOWN"),)


def test_stale_settler_and_revoke_race_on_one_fenced_invitation_without_deadlock(client):
    """Both paths lock invitation then outbox; the winner cannot strand the other at 1213."""
    issuer, _token_version, _mfa_version, proof_event_id = client.portal.call(
        _office_root_with_current_proof)

    async def scenario():
        project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
                               ("invitation-stale-revoke-race-" + uuid.uuid4().hex,))
        await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
        await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
                  (project_id, issuer, issuer))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id,
                email="stale-revoke-race-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                proof_event_id=proof_event_id, mfa_config=_MFA_CONFIG,
                invitation_config=_INVITATION_CONFIG, idempotency_key=str(uuid.uuid4()))
            outbox_id = await _outbox_id(cur, invitation.invitation_id)
            await cur.execute(
                "UPDATE office_invitation_outbox SET state='CLAIMED',attempt_id=%s,attempt_count=1,"
                "claimed_at=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 31 SECOND),updated_at=UTC_TIMESTAMP(6) "
                "WHERE outbox_id=%s", (str(uuid.uuid4()), outbox_id))

        start = asyncio.Event()

        async def settle():
            await start.wait()
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await settle_stale_invitation_claims_in_cursor(cur, config=_INVITATION_CONFIG)

        async def revoke():
            await start.wait()
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await revoke_invitation_in_cursor(
                    cur, actor_user_id=issuer, project_id=project_id,
                    invitation_id=invitation.invitation_id, expected_version=1,
                    proof_event_id=proof_event_id, mfa_config=_MFA_CONFIG,
                    idempotency_key=str(uuid.uuid4()))

        settling, revoking = asyncio.create_task(settle()), asyncio.create_task(revoke())
        start.set()
        settled, revoked = await asyncio.wait_for(asyncio.gather(settling, revoking), timeout=10)
        invitation_row = await sql("SELECT status,version FROM office_invitation WHERE invitation_id=%s",
                                   (invitation.invitation_id,), True)
        outbox_row = await sql("SELECT state,cancel_requested FROM office_invitation_outbox WHERE outbox_id=%s",
                               (outbox_id,), True)
        return settled, revoked, invitation_row, outbox_row

    settled, revoked, invitation_row, outbox_row = client.portal.call(scenario)
    assert settled == 1
    assert revoked == (2, False)
    assert invitation_row == (("REVOKED", 2),)
    # If revocation obtains the lifecycle row first, its trigger marks the
    # claimed outbox cancelled before the stale settler records UNKNOWN.  If
    # the settler wins, UNKNOWN is already terminal when revoke follows.  In
    # both serializations the attempt stays non-retryable and no delivery can
    # be resurrected.
    assert outbox_row in ((("UNKNOWN", 0),), (("UNKNOWN", 1),))
