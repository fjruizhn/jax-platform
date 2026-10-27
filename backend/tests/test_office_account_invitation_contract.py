"""Unit contracts that prevent global account invitations becoming project grants."""
from __future__ import annotations

import pytest

from virtual_office.account_invitations import AccountInvitationError, _email, _token_hash
from virtual_office.migrations import OFFICE_ACCOUNT_INVITATION_DDL


def test_account_invitation_schema_has_its_own_outbox_and_no_project_grant_columns():
    ddl = " ".join(OFFICE_ACCOUNT_INVITATION_DDL)
    assert "office_account_invitation_outbox" in ddl
    assert "office_account_invitation_receipt" in ddl
    assert "project_id" not in ddl
    assert "office_project_grant" not in ddl
    assert "jax_project_scope" not in ddl
    assert "no_update_office_account_invitation_receipt" in ddl
    assert "no_delete_office_account_invitation_receipt" in ddl


def test_account_invitation_web_principal_has_only_lifecycle_dml():
    from db.runtime_privileges import runtime_table_privilege_map

    grants = runtime_table_privilege_map()
    assert grants["office_account_invitation"] == frozenset({"INSERT", "UPDATE"})
    assert grants["office_account_invitation_outbox"] == frozenset({"INSERT", "UPDATE"})
    assert grants["office_account_invitation_receipt"] == frozenset({"INSERT"})


@pytest.mark.parametrize("email", [" invalid@example.test", "invalido", "josé@example.test"])
def test_account_invitation_rejects_noncanonical_email(email):
    with pytest.raises(AccountInvitationError) as error:
        _email(email)
    assert error.value.code == "office_account_invitation_email_invalid"


def test_account_invitation_bearer_is_only_hashed():
    token = "a" * 43
    assert len(_token_hash(token)) == 32
    with pytest.raises(AccountInvitationError):
        _token_hash("no")


def test_account_worker_uses_shared_fragment_email_template(monkeypatch):
    """The account queue may share mail copy, never a project membership path."""
    import asyncio
    from contextlib import asynccontextmanager
    from email.message import EmailMessage
    import smtp_config
    from virtual_office import account_invitations as service
    from virtual_office.invitation_config import InvitationConfig

    delivery = service.ClaimedAccountInvitationDelivery(
        "outbox", "attempt", "invitation", "new@example.test", "a" * 43, "2030-01-01T00:00:00+00:00")
    captured = {}

    @asynccontextmanager
    async def cursor():
        yield object()

    async def claim(_cur): return "CLAIMED", delivery
    async def settle(_cur, **kwargs):
        captured.update(kwargs)
        return "APPLIED"
    def message(_settings, recipient, _subject, text, html):
        captured.update(recipient=recipient, text=text, html=html)
        value = EmailMessage(); value["Message-ID"] = "<account@example.test>"; return value

    monkeypatch.setattr("virtual_office.invitations._delivery_authority_cursor", cursor)
    monkeypatch.setattr(service, "claim_next_account_invitation_delivery_v1_in_cursor", claim)
    monkeypatch.setattr(service, "settle_account_invitation_delivery_v1_in_cursor", settle)
    monkeypatch.setattr(smtp_config, "construir_mensaje", message)
    monkeypatch.setattr(smtp_config, "enviar", lambda *_args: None)
    config = InvitationConfig(60, 30, 10, 10, "https://virtualoffice.example.test", 1)
    assert asyncio.run(service.deliver_one_account_invitation_worker(config=config, smtp_settings=object())) is True
    assert captured["recipient"] == "new@example.test"
    assert "/office/invitation#token=" in captured["text"]
    assert captured["relay_accepted"] is True


def test_shared_timer_drains_project_then_account_queue(monkeypatch):
    import asyncio
    import smtp_config
    from virtual_office import invitation_delivery_worker as worker

    calls = []
    async def project(): calls.append("project")
    async def settings(): return object()
    async def account(*, config, smtp_settings):
        assert smtp_settings is not None
        calls.append("account")
    monkeypatch.setattr(worker, "drain_invitation_deliveries", project)
    monkeypatch.setattr(worker, "drain_account_invitation_deliveries", account)
    monkeypatch.setattr(smtp_config, "cargar_settings", settings)
    async def close_pool(): pass
    monkeypatch.setattr("db.connection.close_pool", close_pool)
    monkeypatch.setattr("virtual_office.invitation_config.load_invitation_config_from_environment",
                        lambda: __import__("virtual_office.invitation_config", fromlist=["InvitationConfig"]).InvitationConfig(60, 30, 10, 10, "https://virtualoffice.example.test", 1))
    asyncio.run(worker.run_once())
    assert calls == ["project", "account"]
