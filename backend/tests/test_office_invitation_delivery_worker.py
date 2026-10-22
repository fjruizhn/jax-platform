"""Pure safety contracts for the dedicated invitation worker."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from email.message import EmailMessage

import pytest

from virtual_office import invitations
from virtual_office.invitation_config import InvitationConfig


_CONFIG = InvitationConfig(60, 30, 10, 10, "https://virtualoffice.example.test", 2)


def test_delivery_batch_config_is_required_and_bounded():
    with pytest.raises(ValueError):
        InvitationConfig(60, 30, 10, 10, "https://virtualoffice.example.test", 0)
    InvitationConfig(60, 30, 10, 10, "https://virtualoffice.example.test", 25)
    with pytest.raises(ValueError):
        InvitationConfig(60, 30, 10, 10, "https://virtualoffice.example.test", 26)


def test_worker_preflight_fails_before_it_can_claim(monkeypatch):
    """SMTP configuration failure happens before the first transaction/claim."""
    import smtp_config

    async def broken_settings():
        raise smtp_config.SmtpNoConfigurado("missing")

    monkeypatch.setattr(smtp_config, "cargar_settings", broken_settings)
    import virtual_office.invitation_config
    monkeypatch.setattr(virtual_office.invitation_config, "load_invitation_config_from_environment", lambda: _CONFIG)

    class ForbiddenTransaction:
        async def __aenter__(self):
            raise AssertionError("worker claimed or maintained before SMTP preflight")

        async def __aexit__(self, *_args):
            return False

    import db.transaccion
    monkeypatch.setattr(db.transaccion, "transaccion", lambda *_args: ForbiddenTransaction())
    with pytest.raises(smtp_config.SmtpNoConfigurado):
        asyncio.run(invitations.drain_invitation_deliveries())


def test_stale_claim_repair_stops_the_oneshot_before_any_new_claim(monkeypatch):
    """The maintenance transaction completes, then OnFailure can alert."""
    import db.transaccion
    import smtp_config
    import virtual_office.invitation_config

    async def settings():
        return object()

    async def expired(_cur, *, limit):
        return 0

    async def stale(_cur, *, config):
        return 1

    async def forbidden_claim(_cur):
        raise AssertionError("a stale repair must end this oneshot before a new claim")

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(virtual_office.invitation_config, "load_invitation_config_from_environment", lambda: _CONFIG)
    monkeypatch.setattr(smtp_config, "cargar_settings", settings)
    monkeypatch.setattr(db.transaccion, "transaccion", lambda *_args: Transaction())
    monkeypatch.setattr(invitations, "expire_due_invitation_deliveries_v1_in_cursor", expired)
    monkeypatch.setattr(invitations, "settle_stale_invitation_claims_v1_in_cursor", stale)
    monkeypatch.setattr(invitations, "claim_next_invitation_delivery_in_cursor", forbidden_claim)
    with pytest.raises(invitations.InvitationDeliveryMaintenanceFailure):
        asyncio.run(invitations.drain_invitation_deliveries())


def test_worker_cli_returns_nonzero_without_serializing_exception(monkeypatch, caplog):
    from virtual_office import invitation_delivery_worker

    async def broken():
        raise RuntimeError("recipient@example.test token-value")

    monkeypatch.setattr(invitation_delivery_worker, "run_once", broken)
    assert invitation_delivery_worker.main() == 1
    assert "recipient@example.test" not in caplog.text
    assert "token-value" not in caplog.text


def test_oneshot_closes_the_database_pool_after_a_delivery_failure(monkeypatch):
    from virtual_office import invitation_delivery_worker
    import db.connection

    closed = []

    async def broken():
        raise RuntimeError("delivery failure")

    async def close_pool():
        closed.append(True)

    monkeypatch.setattr(invitation_delivery_worker, "drain_invitation_deliveries", broken)
    monkeypatch.setattr(db.connection, "close_pool", close_pool)
    with pytest.raises(RuntimeError, match="delivery failure"):
        asyncio.run(invitation_delivery_worker.run_once())
    assert closed == [True]


def test_fenced_post_smtp_settlement_fails_before_worker_counts_delivery(monkeypatch):
    """Relay acceptance is not a durable delivery if its claim was fenced."""
    import smtp_config

    delivery = invitations.ClaimedInvitationDelivery(
        outbox_id="outbox", attempt_id="attempt", invitation_id="invitation",
        recipient="recipient@example.test", bearer_token="bearer", expires_at="2030-01-01T00:00:00Z")

    @asynccontextmanager
    async def cursor():
        yield object()

    async def claim(_cur):
        return "CLAIMED", delivery

    async def settle(_cur, **_kwargs):
        return "FENCED"

    def message(*_args):
        value = EmailMessage()
        value["Message-ID"] = "<fixture@example.test>"
        return value

    monkeypatch.setattr(invitations, "_delivery_authority_cursor", cursor)
    monkeypatch.setattr(invitations, "claim_next_invitation_delivery_v1_in_cursor", claim)
    monkeypatch.setattr(invitations, "settle_invitation_delivery_v1_in_cursor", settle)
    monkeypatch.setattr(invitations, "_invitation_email", lambda *_args: ("subject", "text", "html"))
    monkeypatch.setattr(smtp_config, "construir_mensaje", message)
    monkeypatch.setattr(smtp_config, "enviar", lambda *_args: None)
    with pytest.raises(invitations.InvitationError, match="office_invitation_delivery_fenced"):
        asyncio.run(invitations.deliver_one_invitation_worker(config=_CONFIG, smtp_settings=object()))
