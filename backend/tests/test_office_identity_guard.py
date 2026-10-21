"""Classic administration cannot take over an Office-bound target."""
from tests.identidades import auth, sql, token_para


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
