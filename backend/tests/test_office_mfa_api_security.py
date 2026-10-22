"""HTTP boundary tests for MFA secrets, cache controls, and bcrypt rate gates."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from auth.jwt import create_access_token
from tests.identidades import crear_usuario


def _mfa_env(monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120",
        "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300",
        "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)


def _headers(client):
    user_id, _ = client.portal.call(crear_usuario)
    return {"Authorization": f"Bearer {create_access_token(str(user_id), '1', 'operator', 0)}"}


@pytest.mark.parametrize(("path", "payload"), [
    ("/api/auth/mfa/login/verify", {"challenge_token": "á" * 43, "otp": "000000"}),
    ("/api/auth/mfa/step-up/verify", {"challenge_token": "á" * 43, "otp": "000000"}),
    ("/api/auth/mfa/enrollment/confirm", {"enrollment_token": "á" * 43, "otp": "000000"}),
    ("/api/auth/mfa/recover", {"recovery_code": "á" * 32}),
])
def test_mfa_opaque_dtos_reject_non_ascii_without_echoing_secret(client, path, payload):
    headers = _headers(client) if path == "/api/auth/mfa/step-up/verify" else None
    response = client.post(path, headers=headers, json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "mfa_invalid_request"}
    assert response.headers["cache-control"] == "no-store"
    assert "á" not in response.text


@pytest.mark.parametrize(("path", "payload"), [
    ("/api/auth/mfa/login/verify", {"challenge_token": 1, "otp": "000000"}),
    ("/api/auth/mfa/step-up/verify", {"challenge_token": "A" * 43, "otp": ["000000"]}),
    ("/api/auth/mfa/enrollment/confirm", {"enrollment_token": "A" * 43, "otp": 0}),
    ("/api/auth/mfa/recover", {"recovery_code": ["A" * 32]}),
    ("/api/auth/mfa/enrollment/start", {"password": ["not-a-string"]}),
    ("/api/auth/mfa/recovery-codes/regenerate", {"password": 1}),
    ("/api/auth/mfa/disable", {"password": {"wrong": "type"}}),
    ("/api/auth/mfa/root-reset", {"target_user_id": "1"}),
])
def test_mfa_strict_dtos_reject_non_strings_without_input_echo(client, path, payload):
    response = client.post(path, headers=_headers(client), json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "mfa_invalid_request"}
    assert response.headers["cache-control"] == "no-store"
    assert "not-a-string" not in response.text


@pytest.mark.parametrize(("path", "payload"), [
    ("/api/auth/mfa/login/verify", {"challenge_token": "A" * 43, "otp": "000000", "extra": "sentinel-secret"}),
    ("/api/auth/mfa/enrollment/start", {"password": "sentinel-secret", "extra": True}),
    ("/api/auth/mfa/root-reset", {"target_user_id": 1, "extra": "sentinel-secret"}),
])
def test_mfa_dtos_reject_extras_without_echoing_body(client, path, payload):
    response = client.post(path, headers=_headers(client), json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "mfa_invalid_request"}
    assert response.headers["cache-control"] == "no-store"
    assert "sentinel-secret" not in response.text


def test_mfa_http_errors_and_status_success_are_never_cacheable(client, monkeypatch):
    headers = _headers(client)
    unauthenticated = client.get("/api/auth/mfa/status")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["cache-control"] == "no-store"

    unavailable = client.post("/api/auth/mfa/step-up", headers=headers)
    assert unavailable.status_code == 503
    assert unavailable.headers["cache-control"] == "no-store"

    status = client.get("/api/auth/mfa/status", headers=headers)
    assert status.status_code == 200
    assert status.headers["cache-control"] == "no-store"


def test_legacy_recovery_bearer_hop_is_gone_and_not_cacheable(client):
    response = client.post("/api/auth/mfa/recovery/enrollment/start", json={"recovery_token": "A" * 43})
    assert response.status_code == 410
    assert response.json() == {"detail": "mfa_recovery_hop_retired"}
    assert response.headers["cache-control"] == "no-store"


def test_recovery_uses_its_own_rate_bucket_before_code_lookup(client, monkeypatch):
    _mfa_env(monkeypatch)
    import api.auth as auth_api

    class Bucket:
        def __init__(self, retry): self.retry, self.keys = retry, []
        def hit(self, key): self.keys.append(key); return self.retry
        def reset(self): pass

    login, recovery = Bucket(None), Bucket(30.0)
    monkeypatch.setattr(auth_api.rate_limit, "LOGIN_IP_LIMITER", login)
    monkeypatch.setattr(auth_api.rate_limit, "MFA_RECOVERY_IP_LIMITER", recovery)
    response = client.post("/api/auth/mfa/recover", json={"recovery_code": "A" * 32})
    assert response.status_code == 429
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "30"
    assert recovery.keys and not login.keys


def test_mfa_password_routes_rate_limit_before_bcrypt(client, monkeypatch):
    """The existing canonical limiter rejects before any password hash work."""
    _mfa_env(monkeypatch)
    import api.auth as auth_api

    checked = []
    async def bcrypt_must_not_run(*_args, **_kwargs):
        raise AssertionError("bcrypt ran after the rate gate")

    def reject_rate(request, email):
        checked.append((request.url.path, email))
        raise HTTPException(status_code=429, detail="rate-test")

    monkeypatch.setattr(auth_api, "verify_password", bcrypt_must_not_run)
    monkeypatch.setattr(auth_api.rate_limit, "check_login_rate", reject_rate)
    headers = _headers(client)
    for path in (
        "/api/auth/mfa/enrollment/start",
        "/api/auth/mfa/recovery-codes/regenerate",
        "/api/auth/mfa/disable",
    ):
        response = client.post(path, headers=headers, json={"password": "sentinel-secret"})
        assert response.status_code == 429
        assert response.headers["cache-control"] == "no-store"
        assert "sentinel-secret" not in response.text
    assert [path for path, _email in checked] == [
        "/api/auth/mfa/enrollment/start",
        "/api/auth/mfa/recovery-codes/regenerate",
        "/api/auth/mfa/disable",
    ]
