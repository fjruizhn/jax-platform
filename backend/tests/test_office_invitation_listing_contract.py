"""Contract tests for the authenticated Office invitation list."""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
from contextlib import asynccontextmanager

import pytest
from auth.models import AuthUser
from fastapi import HTTPException

import api.virtual_office.invitations as invitation_api
from virtual_office.invitations import (
    InvitationError, can_manage_invitations_in_cursor, decode_invitation_list_cursor, encode_invitation_list_cursor,
    list_active_invitations_in_cursor,
)


class _Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.statements = []

    async def execute(self, statement, params=()):
        self.statements.append((statement, params))

    async def fetchall(self):
        return self.rows


def _user() -> AuthUser:
    return AuthUser(user_id="7", tenant_id="1", role="operator",
                    email="admin@example.test", account_class="OFFICE_INTERNAL",
                    mfa_proof_event_id=9)


def _opaque_cursor(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_list_cursor_has_domain_project_and_total_order_binding():
    expiry = dt.datetime(2026, 10, 22, 12, tzinfo=dt.timezone.utc)
    encoded = encode_invitation_list_cursor(
        project_id=12, expires_at=expiry,
        invitation_id="12345678-1234-4123-8123-123456789abc",
    )
    assert decode_invitation_list_cursor(encoded, project_id=12) == (
        expiry, "12345678-1234-4123-8123-123456789abc",
    )
    with pytest.raises(InvitationError) as rejected:
        decode_invitation_list_cursor(encoded, project_id=13)
    assert rejected.value.status_code == 422


@pytest.mark.parametrize("cursor", [
    _opaque_cursor({
        "domain": "office-invitation-list/v1", "project_id": 12,
        "expires_at": "2026-10-22T12:00:00+00:00",
        "invitation_id": "12345678-1234-4123-8123-123456789ABC",
    }),
    _opaque_cursor({
        "domain": "office-invitation-list/v1", "project_id": 12,
        "expires_at": "9999-12-31T23:59:59-14:00",
        "invitation_id": "12345678-1234-4123-8123-123456789abc",
    }),
])
def test_list_cursor_rejects_noncanonical_uuid_and_unrepresentable_utc(cursor):
    with pytest.raises(InvitationError) as rejected:
        decode_invitation_list_cursor(cursor, project_id=12)
    assert rejected.value.status_code == 422


def test_listing_gates_before_confidential_email_query(monkeypatch):
    cursor = _Cursor()

    async def denied(*_args, **_kwargs):
        raise InvitationError("office_invitation_issuer_denied", status_code=403)

    monkeypatch.setattr("virtual_office.invitations._session_authorization_snapshot", denied)
    with pytest.raises(InvitationError) as rejected:
        asyncio.run(list_active_invitations_in_cursor(
            cursor, actor_user_id=7, project_id=12, proof_event_id=9,
            mfa_config=object(), limit=25, cursor=None,
        ))
    assert rejected.value.status_code == 403
    assert cursor.statements == []


def test_capability_uses_normal_project_admin_session_without_mfa(monkeypatch):
    async def allowed(*_args, **_kwargs):
        return object(), 1, 1

    monkeypatch.setattr("virtual_office.invitations._session_authorization_snapshot", allowed)
    assert asyncio.run(can_manage_invitations_in_cursor(
        _Cursor(), actor_user_id=7, project_id=12, proof_event_id=None,
        mfa_config=None,
    )) is True


def test_session_snapshot_authorizes_project_admin_without_mfa_arguments(monkeypatch):
    class BasisCursor(_Cursor):
        async def fetchone(self):
            return (4, 9)

    observed = {}

    async def no_op_lock(*_args, **_kwargs):
        return None

    async def allowed(_cur, actor, operation, project, **kwargs):
        observed.update(actor=actor, operation=operation.value, project=project, **kwargs)
        return type("Decision", (), {"allowed": True})()

    monkeypatch.setattr("virtual_office.invitations.lock_office_actor_project_tenants_in_cursor", no_op_lock)
    monkeypatch.setattr("virtual_office.invitations.authorize_office_in_cursor", allowed)
    from virtual_office.invitations import _session_authorization_snapshot

    decision, profile_version, grant_version = asyncio.run(
        _session_authorization_snapshot(BasisCursor(), actor_user_id=7, project_id=12)
    )
    assert decision.allowed is True
    assert (profile_version, grant_version) == (4, 9)
    assert observed == {
        "actor": 7, "operation": "ADMINISTER_PROJECT_MEMBERS", "project": 12,
        "require_recent_mfa": False,
    }


def test_listing_uses_limit_plus_one_live_project_index_keyset(monkeypatch):
    expiry = dt.datetime(2026, 10, 22, 12)
    cursor = _Cursor([
        ("12345678-1234-4123-8123-123456789abc", "one@example.test", "INTERNAL", "ISSUED", 1, expiry, "SMTP_ACCEPTED", "SMTP_ACCEPTED"),
        ("22345678-1234-4123-8123-123456789abc", "two@example.test", "CLIENT_PUBLISHED", "ISSUED", 1, expiry, "UNKNOWN", "SMTP_UNKNOWN"),
    ])

    async def allowed(*_args, **_kwargs):
        return object(), 1, 1

    monkeypatch.setattr("virtual_office.invitations._session_authorization_snapshot", allowed)
    page, next_cursor = asyncio.run(list_active_invitations_in_cursor(
        cursor, actor_user_id=7, project_id=12, proof_event_id=9,
        mfa_config=object(), limit=1, cursor=None,
    ))
    assert [item.email for item in page] == ["one@example.test"]
    assert page[0].delivery_state == "SMTP_ACCEPTED"
    assert page[0].delivery_result_code == "SMTP_ACCEPTED"
    assert next_cursor is not None
    statement, params = cursor.statements[-1]
    assert "i.project_id=%s AND i.status='ISSUED' AND i.expires_at>UTC_TIMESTAMP(6)" in statement
    assert "ORDER BY i.expires_at ASC,i.invitation_id ASC LIMIT %s" in statement
    assert params == (12, 2)


def test_http_list_has_no_store_vary_and_opaque_denial(monkeypatch):
    @asynccontextmanager
    async def transaction_stub(*_args):
        yield object()

    async def denied(*_args, **_kwargs):
        raise InvitationError("office_invitation_issuer_denied", status_code=403)

    monkeypatch.setattr(invitation_api, "transaccion", transaction_stub)
    monkeypatch.setattr(invitation_api, "load_mfa_config_from_environment", lambda: object())
    monkeypatch.setattr(invitation_api, "list_active_invitations_in_cursor", denied)
    from starlette.responses import Response
    response = Response()
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(invitation_api.list_invitations(12, response, 25, None, _user()))
    assert rejected.value.status_code == 403
    assert rejected.value.detail == "office_invitation_issuer_denied"


def test_http_list_sets_no_store_and_authorization_vary(monkeypatch):
    @asynccontextmanager
    async def transaction_stub(*_args):
        yield object()

    async def allowed(*_args, **_kwargs):
        return [], None

    monkeypatch.setattr(invitation_api, "transaccion", transaction_stub)
    monkeypatch.setattr(invitation_api, "load_mfa_config_from_environment", lambda: object())
    monkeypatch.setattr(invitation_api, "list_active_invitations_in_cursor", allowed)
    from starlette.responses import Response
    response = Response()
    payload = asyncio.run(invitation_api.list_invitations(12, response, 25, None, _user()))
    assert payload == {"invitations": [], "next_cursor": None}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Vary"] == "Authorization"


def test_http_list_exposes_terminal_transport_uncertainty_to_project_admin(monkeypatch):
    """Removing the outbox projection would hide a non-delivery as ISSUED."""
    @asynccontextmanager
    async def transaction_stub(*_args):
        yield object()

    expiry = dt.datetime(2026, 10, 22, 12, tzinfo=dt.timezone.utc)

    async def allowed(*_args, **_kwargs):
        from virtual_office.invitations import ListedInvitation
        return [ListedInvitation(
            invitation_id="12345678-1234-4123-8123-123456789abc",
            email="delivery-failed@example.test", audience="INTERNAL", status="ISSUED",
            version=1, expires_at=expiry, delivery_state="UNKNOWN",
            delivery_result_code="SMTP_UNKNOWN",
        )], None

    monkeypatch.setattr(invitation_api, "transaccion", transaction_stub)
    monkeypatch.setattr(invitation_api, "load_mfa_config_from_environment", lambda: object())
    monkeypatch.setattr(invitation_api, "list_active_invitations_in_cursor", allowed)
    from starlette.responses import Response
    payload = asyncio.run(invitation_api.list_invitations(12, Response(), 25, None, _user()))
    assert payload["invitations"] == [{
        "id": "12345678-1234-4123-8123-123456789abc",
        "email": "delivery-failed@example.test", "audience": "INTERNAL", "status": "ISSUED",
        "version": 1, "expires_at": "2026-10-22T12:00:00+00:00",
        "delivery_state": "UNKNOWN", "delivery_result_code": "SMTP_UNKNOWN",
    }]


def test_http_list_rejects_malformed_cursor_before_confidential_query(monkeypatch):
    @asynccontextmanager
    async def transaction_stub(*_args):
        yield _Cursor()

    async def allowed(*_args, **_kwargs):
        return object(), 1, 1

    cursor = _opaque_cursor({
        "domain": "office-invitation-list/v1", "project_id": 12,
        "expires_at": "9999-12-31T23:59:59-14:00",
        "invitation_id": "12345678-1234-4123-8123-123456789abc",
    })
    monkeypatch.setattr(invitation_api, "transaccion", transaction_stub)
    monkeypatch.setattr(invitation_api, "load_mfa_config_from_environment", lambda: object())
    monkeypatch.setattr("virtual_office.invitations._session_authorization_snapshot", allowed)
    from starlette.responses import Response
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(invitation_api.list_invitations(12, Response(), 25, cursor, _user()))
    assert rejected.value.status_code == 422
    assert rejected.value.detail == "office_invitation_cursor_invalid"
