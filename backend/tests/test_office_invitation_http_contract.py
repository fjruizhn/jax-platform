"""HTTP boundary contract shared with the isolated invitation UI."""
from __future__ import annotations

import asyncio
import inspect
import uuid
from contextlib import asynccontextmanager

import pytest
from auth.models import AuthUser
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

import api.virtual_office.invitations as invitation_api
from api.virtual_office.invitations import (
    _invitation_rate_key,
    _require_durable_idempotency,
    InvitationAcceptRequest,
    accept_invitation,
    issue_invitation,
    revoke_invitation,
    router,
)


def _operation(path: str, method: str) -> dict:
    app = FastAPI()
    app.include_router(router)
    return app.openapi()["paths"][path][method]


def test_issue_and_revoke_expose_the_ui_header_and_expected_version_contract():
    issue = _operation("/api/virtual-office/projects/{project_id}/invitations", "post")
    revoke = _operation(
        "/api/virtual-office/projects/{project_id}/invitations/{invitation_id}", "delete")
    issue_header = next(parameter for parameter in issue["parameters"]
                        if parameter["name"] == "Idempotency-Key")
    revoke_header = next(parameter for parameter in revoke["parameters"]
                         if parameter["name"] == "Idempotency-Key")
    expected_version = next(parameter for parameter in revoke["parameters"]
                            if parameter["name"] == "expected_version")
    assert issue_header["in"] == revoke_header["in"] == "header"
    assert issue_header["required"] is revoke_header["required"] is True
    assert expected_version == {
        "name": "expected_version", "in": "query", "required": True,
        "schema": {"type": "integer", "minimum": 1, "title": "Expected Version"},
    }
    assert "idempotency_key" in inspect.signature(issue_invitation).parameters
    assert "idempotency_key" in inspect.signature(revoke_invitation).parameters


def test_invitation_retry_uuid_is_validated_and_returned_for_the_receipt_service():
    with pytest.raises(HTTPException) as invalid:
        _require_durable_idempotency("not-a-uuid")
    assert invalid.value.status_code == 422
    assert invalid.value.detail == "office_invitation_idempotency_invalid"

    key = str(uuid.uuid4())
    assert _require_durable_idempotency(key) == key


def test_invitation_bearer_rate_key_is_domain_separated_and_does_not_retain_the_bearer():
    bearer = "A" * 43
    key = _invitation_rate_key(bearer)
    assert key != bearer
    assert len(key) == 64
    assert key == _invitation_rate_key(bearer)
    assert key != _invitation_rate_key("B" * 43)


def test_unicode_bearer_is_rejected_before_limiter_preflight_or_bcrypt(monkeypatch):
    def limiter_must_not_run(*_args) -> None:
        raise AssertionError("limiter must not receive a non-ASCII bearer")

    async def preflight_must_not_run(_token: str) -> bool:
        raise AssertionError("preflight must not receive a non-ASCII bearer")

    def bcrypt_must_not_run(_password: str) -> str:
        raise AssertionError("bcrypt must not receive a non-ASCII bearer")

    monkeypatch.setattr(invitation_api.rate_limit, "check_login_rate", limiter_must_not_run)
    monkeypatch.setattr(invitation_api, "invitation_bearer_preflight", preflight_must_not_run)
    monkeypatch.setattr(invitation_api, "_hash", bcrypt_must_not_run)
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(accept_invitation(
            InvitationAcceptRequest(token="ñ" * 43, password="Valid-passphrase-123!"),
            _anonymous_request(), None))
    assert rejected.value.status_code == 403
    assert rejected.value.detail == "office_invitation_invalid"


def _anonymous_request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/virtual-office/invitations/accept",
                    "headers": [], "client": ("127.0.0.1", 54321)})


def test_invalid_bearer_fails_before_bcrypt(monkeypatch):
    async def invalid_preflight(_token: str) -> bool:
        return False

    def bcrypt_must_not_run(_password: str) -> str:
        raise AssertionError("bcrypt must not run for an invalid invitation bearer")

    monkeypatch.setattr(invitation_api.rate_limit, "check_login_rate", lambda *_: None)
    monkeypatch.setattr(invitation_api, "invitation_bearer_preflight", invalid_preflight)
    monkeypatch.setattr(invitation_api, "_hash", bcrypt_must_not_run)
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(accept_invitation(InvitationAcceptRequest(token="A" * 43, password="Valid-passphrase-123!"),
                                      _anonymous_request(), None))
    assert rejected.value.status_code == 403


def test_rate_rejection_happens_before_preflight_and_bcrypt(monkeypatch):
    def too_many(*_args) -> None:
        raise HTTPException(status_code=429, detail="rate limited")

    async def preflight_must_not_run(_token: str) -> bool:
        raise AssertionError("preflight must not run after rate rejection")

    def bcrypt_must_not_run(_password: str) -> str:
        raise AssertionError("bcrypt must not run after rate rejection")

    monkeypatch.setattr(invitation_api.rate_limit, "check_login_rate", too_many)
    monkeypatch.setattr(invitation_api, "invitation_bearer_preflight", preflight_must_not_run)
    monkeypatch.setattr(invitation_api, "_hash", bcrypt_must_not_run)
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(accept_invitation(InvitationAcceptRequest(token="A" * 43, password="Valid-passphrase-123!"),
                                      _anonymous_request(), None))
    assert rejected.value.status_code == 429


def test_jax_retryable_authority_error_is_a_stable_retryable_http_response(monkeypatch):
    from jax.memory.project_authority import ProjectAuthorityRetryable

    @asynccontextmanager
    async def transaction_stub(*_args):
        yield object()

    async def retryable_authority_error(*_args, **_kwargs):
        raise ProjectAuthorityRetryable()

    monkeypatch.setattr(invitation_api, "transaccion", transaction_stub)
    monkeypatch.setattr(invitation_api, "accept_invitation_in_cursor", retryable_authority_error)
    authenticated_user = AuthUser(user_id="1", tenant_id="1", role="operator",
                                  email="person@example.test", account_class="OFFICE_INTERNAL")
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(accept_invitation(
            InvitationAcceptRequest(token="A" * 43, password=None),
            _anonymous_request(), authenticated_user))
    assert rejected.value.status_code == 503
    assert rejected.value.detail == "office_invitation_retryable"


@pytest.mark.parametrize(
    ("authority_error", "scenario"),
    [
        ("AuthorizationDenied", "issuer_mfa_revoked"),
        ("AuthorizationDenied", "issuer_grant_revoked"),
        ("ProjectStateConflict", "project_closed"),
    ],
)
def test_jax_authority_denials_are_stable_invitation_denials(
    monkeypatch, authority_error, scenario,
):
    from jax.memory import project_authority

    @asynccontextmanager
    async def transaction_stub(*_args):
        yield object()

    async def denied_authority_error(*_args, **_kwargs):
        raise getattr(project_authority, authority_error)(scenario)

    monkeypatch.setattr(invitation_api, "transaccion", transaction_stub)
    monkeypatch.setattr(invitation_api, "accept_invitation_in_cursor", denied_authority_error)
    authenticated_user = AuthUser(user_id="1", tenant_id="1", role="operator",
                                  email="person@example.test", account_class="OFFICE_INTERNAL")
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(accept_invitation(
            InvitationAcceptRequest(token="A" * 43, password=None),
            _anonymous_request(), authenticated_user))
    assert rejected.value.status_code == 403
    assert rejected.value.detail == "office_invitation_issuer_denied"
