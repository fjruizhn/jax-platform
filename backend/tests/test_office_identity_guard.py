"""Classic administration cannot take over an Office-bound target."""
import datetime as dt

import pyotp
import pytest

from auth.jwt import create_access_token
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.identidades import auth, crear_usuario, sql, token_para
from virtual_office.mfa import (
    MFAConfig,
    confirm_enrollment_in_cursor,
    issue_step_up_challenge_in_cursor,
    start_first_enrollment_after_password_in_cursor,
    verify_and_consume_totp_in_cursor,
)


_CONFIG = MFAConfig(120, 3, 300, 300, 3)


def _otp(secret: str) -> str:
    return pyotp.TOTP(secret, digits=6, interval=30).at(
        (dt.datetime.now().timestamp() // 30 - 1) * 30,
    )


def _current_otp(secret: str) -> str:
    return pyotp.TOTP(secret, digits=6, interval=30).now()


async def _office_root_with_step_up():
    root, _ = await crear_usuario(role="superadmin")
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        enrollment = await start_first_enrollment_after_password_in_cursor(
            cur, preauthenticated_user_id=root, config=_CONFIG,
        )
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await confirm_enrollment_in_cursor(
            cur, enrollment_capability_token=enrollment.enrollment_id,
            otp=_current_otp(enrollment.secret), config=_CONFIG, server_correlation_id=b"E" * 16,
        )
    await sql(
        "INSERT INTO office_account_profile (user_id,global_role,status) "
        "VALUES (%s,'OFFICE_SUPERUSER','ACTIVE')", (root,),
    )
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        challenge = await issue_step_up_challenge_in_cursor(
            cur, actor_user_id=root, config=_CONFIG,
        )
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        proof = await verify_and_consume_totp_in_cursor(
            cur, challenge_token=challenge.token, otp=_otp(enrollment.secret),
            server_correlation_id=b"S" * 16,
        )
    versions = (await sql(
        "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (root,), True,
    ))[0]
    return root, proof, versions


async def _bind_office_target(user_id: int, revoked: bool = False):
    await sql(
        "INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR',%s)",
        (user_id, "REVOKED" if revoked else "ACTIVE"),
    )


async def _unbind_office_target(user_id: int):
    await sql("DELETE FROM office_account_profile WHERE user_id=%s", (user_id,))


def test_classic_superadmin_cannot_change_office_bound_target_credentials_or_email(client, usuarios):
    actor, _ = usuarios(role="superadmin")
    target, original_email = usuarios()
    client.portal.call(_bind_office_target, target)
    headers = auth(token_para(actor, role="superadmin"))
    try:
        password = client.post(f"/api/admin/users/{target}/password", json={"new_password": "nueva-clave-987"}, headers=headers)
        email = client.put(f"/api/admin/users/{target}", json={"email": "target-new@example.invalid"}, headers=headers)
        assert (password.status_code, password.json()["detail"]) == (403, "office_identity_guard_required")
        assert (email.status_code, email.json()["detail"]) == (403, "office_identity_guard_required")
        rows = client.portal.call(sql, "SELECT email,must_change_password FROM jax_users WHERE user_id=%s", (target,), True)
        assert rows == ((original_email, 0),)
    finally:
        client.portal.call(_unbind_office_target, target)


def test_revoked_office_profile_remains_a_permanent_identity_binding(client, usuarios):
    actor, _ = usuarios(role="superadmin")
    target, _ = usuarios()
    client.portal.call(_bind_office_target, target, True)
    try:
        response = client.post(f"/api/admin/users/{target}/unlock", headers=auth(token_para(actor, role="superadmin")))
        assert (response.status_code, response.json()["detail"]) == (403, "office_identity_guard_required")
    finally:
        client.portal.call(_unbind_office_target, target)


def test_reset_link_target_guard_runs_before_smtp_configuration(client, usuarios, monkeypatch):
    from api.admin import users as users_mod

    actor, _ = usuarios(role="superadmin")
    target, _ = usuarios()
    client.portal.call(_bind_office_target, target)
    called = []

    async def smtp_should_not_run():
        called.append(True)
        raise AssertionError("SMTP configuration was reached")

    monkeypatch.setattr(users_mod.smtp_config, "cargar_settings", smtp_should_not_run)
    try:
        response = client.post(f"/api/admin/users/{target}/reset-link", headers=auth(token_para(actor, role="superadmin")))
        assert (response.status_code, response.json()["detail"]) == (403, "office_identity_guard_required")
        assert called == []
    finally:
        client.portal.call(_unbind_office_target, target)


def test_classic_target_and_session_revoke_keep_existing_behavior(client, usuarios):
    actor, _ = usuarios(role="superadmin")
    target, _ = usuarios()
    headers = auth(token_para(actor, role="superadmin"))
    assert client.post(f"/api/admin/users/{target}/unlock", headers=headers).status_code == 200
    assert client.post(f"/api/admin/users/{target}/revoke-sessions", headers=headers).status_code == 200


def test_office_root_step_up_from_jwt_allows_office_target_identity_mutation(client, usuarios, monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120",
        "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300",
        "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)
    root, proof, versions = client.portal.call(_office_root_with_step_up)
    target, old_email = usuarios()
    client.portal.call(_bind_office_target, target)
    try:
        token = create_access_token(
            str(root), "1", "superadmin", versions[0],
            mfa_proof_event_id=proof.event_id, mfa_version=versions[1],
            mfa_at=int(proof.occurred_at.timestamp()),
        )
        response = client.put(
            f"/api/admin/users/{target}", json={"email": "office-target@example.invalid"},
            headers=auth(token),
        )
        assert response.status_code == 200, response.text
        assert client.portal.call(
            sql, "SELECT email FROM jax_users WHERE user_id=%s", (target,), True,
        ) == (("office-target@example.invalid",),)
    finally:
        client.portal.call(_unbind_office_target, target)


def _set_mfa_env(monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120",
        "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300",
        "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)


def _root_headers(root, proof, versions):
    return auth(create_access_token(
        str(root), "1", "superadmin", versions[0],
        mfa_proof_event_id=proof.event_id, mfa_version=versions[1],
        mfa_at=int(proof.occurred_at.timestamp()),
    ))


@pytest.mark.parametrize(
    ("name", "method", "path", "body"),
    (
        ("email", "put", "/api/admin/users/{target}", {"email": "denied-email@example.invalid"}),
        ("role", "put", "/api/admin/users/{target}", {"role": "viewer"}),
        ("reactivate", "put", "/api/admin/users/{target}", {"status": "active"}),
        ("unlock", "post", "/api/admin/users/{target}/unlock", None),
        ("password", "post", "/api/admin/users/{target}/password", {"new_password": "denied-password-987"}),
        ("reset_link", "post", "/api/admin/users/{target}/reset-link", None),
        ("baja", "post", "/api/admin/users/{target}/baja", None),
    ),
)
def test_office_bound_identity_endpoints_deny_classic_or_unproved_actor(
    client, usuarios, monkeypatch, name, method, path, body,
):
    _set_mfa_env(monkeypatch)
    actor, _ = usuarios(role="superadmin")
    target, email = usuarios(status="inactive" if name == "reactivate" else "active")
    client.portal.call(_bind_office_target, target)
    try:
        response = getattr(client, method)(path.format(target=target), json=body,
                                           headers=auth(token_para(actor, role="superadmin")))
        assert (response.status_code, response.json()["detail"]) == (403, "office_identity_guard_required"), name
        state = client.portal.call(sql, "SELECT email,role,status,mfa_version FROM jax_users WHERE user_id=%s", (target,), True)
        assert state[0][:3] == (email, "operator", "inactive" if name == "reactivate" else "active"), name
    finally:
        client.portal.call(_unbind_office_target, target)


@pytest.mark.parametrize(
    ("name", "method", "path", "body"),
    (
        ("email", "put", "/api/admin/users/{target}", {"email": "root-email@example.invalid"}),
        ("role", "put", "/api/admin/users/{target}", {"role": "viewer"}),
        ("reactivate", "put", "/api/admin/users/{target}", {"status": "active"}),
        ("unlock", "post", "/api/admin/users/{target}/unlock", None),
        ("password", "post", "/api/admin/users/{target}/password", {"new_password": "root-password-987"}),
        ("reset_link", "post", "/api/admin/users/{target}/reset-link", None),
        ("baja", "post", "/api/admin/users/{target}/baja", None),
    ),
)
def test_office_root_recent_proof_allows_each_guarded_identity_endpoint(
    client, usuarios, monkeypatch, name, method, path, body,
):
    _set_mfa_env(monkeypatch)
    from api.admin import users as users_mod
    root, proof, versions = client.portal.call(_office_root_with_step_up)
    target, _ = usuarios(status="inactive" if name == "reactivate" else "active")
    client.portal.call(_bind_office_target, target)
    calls = []

    async def smtp_settings():
        calls.append("settings")
        return object()

    def send_stub(*_args):
        calls.append("send")

    monkeypatch.setattr(users_mod.smtp_config, "cargar_settings", smtp_settings)
    monkeypatch.setattr(users_mod.auth_api, "_send_reset_email", send_stub)
    try:
        response = getattr(client, method)(path.format(target=target), json=body,
                                           headers=_root_headers(root, proof, versions))
        assert response.status_code == 200, (name, response.text)
        if name == "reset_link":
            assert calls == ["settings", "send"]
    finally:
        client.portal.call(_unbind_office_target, target)


def test_reset_link_never_reaches_smtp_on_initial_guard_denial_or_claim_injection(client, usuarios, monkeypatch):
    _set_mfa_env(monkeypatch)
    from api.admin import users as users_mod
    root, proof, versions = client.portal.call(_office_root_with_step_up)
    target, _ = usuarios()
    client.portal.call(_bind_office_target, target)
    calls = []

    async def no_smtp():
        calls.append(True)
        raise AssertionError("SMTP must not be consulted")

    monkeypatch.setattr(users_mod.smtp_config, "cargar_settings", no_smtp)
    # A body/query reference is not the authenticated JWT proof pointer.
    no_claim = auth(create_access_token(str(root), "1", "superadmin", versions[0]))
    try:
        response = client.post(
            f"/api/admin/users/{target}/reset-link?mfa_proof_event_id={proof.event_id}",
            json={"mfa_proof_event_id": proof.event_id}, headers=no_claim,
        )
        assert (response.status_code, response.json()["detail"]) == (403, "office_identity_guard_required")
        assert calls == []
    finally:
        client.portal.call(_unbind_office_target, target)


def test_revoked_target_is_guarded_but_classic_target_and_exceptions_preserve_behavior(client, usuarios, monkeypatch):
    _set_mfa_env(monkeypatch)
    actor, _ = usuarios(role="superadmin")
    office_target, _ = usuarios()
    classic_target, _ = usuarios()
    client.portal.call(_bind_office_target, office_target, True)
    headers = auth(token_para(actor, role="superadmin"))
    try:
        assert client.post(f"/api/admin/users/{office_target}/unlock", headers=headers).status_code == 403
        # Emergency session revocation deliberately remains outside the guard.
        assert client.post(f"/api/admin/users/{office_target}/revoke-sessions", headers=headers).status_code == 200
        # A configuration present but an INTERNAL target without a profile is
        # still a classic path.
        assert client.post(f"/api/admin/users/{classic_target}/unlock", headers=headers).status_code == 200
        # Deactivation alone remains classic; combining it with email invokes
        # the guard before any mutation.
        assert client.put(f"/api/admin/users/{office_target}", json={"status": "inactive"}, headers=headers).status_code == 200
        blocked = client.put(
            f"/api/admin/users/{office_target}",
            json={"status": "inactive", "email": "combined-denied@example.invalid"}, headers=headers,
        )
        assert (blocked.status_code, blocked.json()["detail"]) == (403, "office_identity_guard_required")
    finally:
        client.portal.call(_unbind_office_target, office_target)


def test_root_must_still_be_classic_superadmin_and_current_under_profile_or_version_cut(client, usuarios, monkeypatch):
    _set_mfa_env(monkeypatch)
    root, proof, versions = client.portal.call(_office_root_with_step_up)
    target, _ = usuarios()
    client.portal.call(_bind_office_target, target)
    try:
        # Office root with a JWT role below classic superadmin stops at the
        # classic HTTP boundary before it can invoke Office authority.
        client.portal.call(sql, "UPDATE jax_users SET role='operator' WHERE user_id=%s", (root,))
        assert client.post(f"/api/admin/users/{target}/unlock", headers=_root_headers(root, proof, versions)).status_code == 403
        client.portal.call(sql, "UPDATE jax_users SET role='superadmin' WHERE user_id=%s", (root,))
        client.portal.call(sql, "UPDATE office_account_profile SET status='REVOKED' WHERE user_id=%s", (root,))
        assert client.post(f"/api/admin/users/{target}/unlock", headers=_root_headers(root, proof, versions)).status_code == 403
        client.portal.call(sql, "UPDATE office_account_profile SET status='ACTIVE' WHERE user_id=%s", (root,))
        # The JWT remains transport-valid; the cursor resolver sees the new
        # database MFA version and rejects the old durable proof.
        client.portal.call(sql, "UPDATE jax_users SET mfa_version=mfa_version+1 WHERE user_id=%s", (root,))
        assert client.post(f"/api/admin/users/{target}/unlock", headers=_root_headers(root, proof, versions)).status_code == 403
    finally:
        client.portal.call(_unbind_office_target, target)


async def _controlled_root_step_up_event(root: int, *, timestamp: str) -> int:
    """Append a schema-valid test proof; ledger time is never rewritten."""
    timestamp_sql = {
        "old": "DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 301 SECOND)",
        "future": "DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 1 SECOND)",
    }[timestamp]
    versions = (await sql(
        "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (root,), True,
    ))[0]
    return await sql(
        "INSERT INTO office_mfa_security_event "
        "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,"
        "token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,"
        "authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
        "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,"
        "NULL,NULL,'VERIFIED',%s,NULL," + timestamp_sql + ")",
        (root, root, *versions, __import__("uuid").uuid4().bytes),
    )


@pytest.mark.parametrize("kind", ("old", "future", "foreign"))
def test_guard_http_rejects_old_future_or_foreign_durable_proof(client, usuarios, monkeypatch, kind):
    _set_mfa_env(monkeypatch)
    root, proof, versions = client.portal.call(_office_root_with_step_up)
    target, _ = usuarios()
    client.portal.call(_bind_office_target, target)
    try:
        if kind == "foreign":
            other_root, other_proof, other_versions = client.portal.call(_office_root_with_step_up)
            headers = auth(create_access_token(
                str(root), "1", "superadmin", versions[0],
                mfa_proof_event_id=other_proof.event_id, mfa_version=other_versions[1],
                mfa_at=int(other_proof.occurred_at.timestamp()),
            ))
        else:
            event_id = client.portal.call(
                lambda: _controlled_root_step_up_event(root, timestamp=kind),
            )
            headers = auth(create_access_token(
                str(root), "1", "superadmin", versions[0], mfa_proof_event_id=event_id,
                mfa_version=versions[1], mfa_at=int(proof.occurred_at.timestamp()),
            ))
        response = client.post(f"/api/admin/users/{target}/unlock", headers=headers)
        assert (response.status_code, response.json()["detail"]) == (403, "office_identity_guard_required")
    finally:
        client.portal.call(_unbind_office_target, target)


def test_reset_link_rechecks_office_authority_before_creating_token_or_sending(client, usuarios, monkeypatch):
    _set_mfa_env(monkeypatch)
    from api.admin import users as users_mod
    root, proof, versions = client.portal.call(_office_root_with_step_up)
    target, _ = usuarios()
    client.portal.call(_bind_office_target, target)
    calls = []

    async def revoke_root_after_first_guard():
        calls.append("settings")
        await sql("UPDATE office_account_profile SET status='REVOKED' WHERE user_id=%s", (root,))
        return object()

    def send_must_not_run(*_args):
        calls.append("send")
        raise AssertionError("second guard must run before SMTP send")

    monkeypatch.setattr(users_mod.smtp_config, "cargar_settings", revoke_root_after_first_guard)
    monkeypatch.setattr(users_mod.auth_api, "_send_reset_email", send_must_not_run)
    try:
        response = client.post(
            f"/api/admin/users/{target}/reset-link", headers=_root_headers(root, proof, versions),
        )
        assert (response.status_code, response.json()["detail"]) == (403, "office_identity_guard_required")
        assert calls == ["settings"]
        assert client.portal.call(
            sql, "SELECT token FROM password_reset_tokens WHERE user_id=%s", (target,), True,
        ) == ()
    finally:
        client.portal.call(_unbind_office_target, target)
