"""Real database contract for the bounded Virtual Office entry session."""
from __future__ import annotations

import time
import uuid
from functools import partial

from auth.jwt import create_access_token
from tests.identidades import crear_usuario, sql


def _configure_mfa(monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120",
        "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300",
        "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)


async def _office_user(*, account_class="INTERNAL", global_role="OFFICE_SUPERUSER", proof_time="UTC_TIMESTAMP(6)"):
    user_id, _ = await crear_usuario()
    await sql("UPDATE jax_users SET account_class=%s WHERE user_id=%s", (account_class, user_id))
    await sql(
        "INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,%s,'ACTIVE')",
        (user_id, global_role),
    )
    token_version, mfa_version = (await sql(
        "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True,
    ))[0]
    event_id = await sql(
        "INSERT INTO office_mfa_security_event "
        "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
        "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL," + proof_time + ")",
        (user_id, user_id, int(token_version), int(mfa_version), uuid.uuid4().bytes),
    )
    return user_id, int(token_version), int(mfa_version), int(event_id)


async def _make_project_admin(user_id: int):
    project_id = await sql(
        "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
        ("office-session-project-" + uuid.uuid4().hex,),
    )
    await sql(
        "INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
        "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
        (project_id,),
    )
    await sql(
        "INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) "
        "VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
        (str(uuid.uuid4()), project_id, user_id),
    )
    await sql(
        "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
        "VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
        (project_id, user_id, user_id),
    )


def _headers(user_id: int, token_version: int, mfa_version: int, proof_id: int | None):
    token = create_access_token(
        str(user_id), "1", "operator", token_version,
        mfa_proof_event_id=proof_id, mfa_version=mfa_version if proof_id is not None else None,
        mfa_at=int(time.time()) if proof_id is not None else None,
    )
    return {"Authorization": "Bearer " + token}


def test_office_session_derives_root_capabilities_from_db_and_current_mfa(client, monkeypatch):
    _configure_mfa(monkeypatch)
    user_id, tv, mv, proof = client.portal.call(_office_user)
    response = client.get("/api/virtual-office/session", headers=_headers(user_id, tv, mv, proof))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "state": "ACTIVE",
        "identity": {"user_id": user_id, "tenant_id": 1, "account_class": "INTERNAL", "global_role": "OFFICE_SUPERUSER"},
        "mfa": {"verified": True, "recent": True},
        "capabilities": {
            "create_project": True,
            "administer_office": True,
            "administer_identity_security": True,
        },
    }


def test_classic_superadmin_can_bootstrap_office_with_current_password_without_mfa(client):
    async def scenario():
        from db.connection import get_pool
        from virtual_office.migrations import OFFICE_BOOTSTRAP_DDL, apply_office_migrations

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("DROP TRIGGER IF EXISTS no_update_office_bootstrap_state")
                await cur.execute("DROP TRIGGER IF EXISTS no_delete_office_bootstrap_state")
                await cur.execute("DROP TABLE IF EXISTS office_bootstrap_state")
                for statement in OFFICE_BOOTSTRAP_DDL:
                    await cur.execute(statement)
        user_id, _ = await crear_usuario(role="superadmin", password="office-bootstrap-current-password")
        return user_id

    user_id = client.portal.call(scenario)
    token = create_access_token(str(user_id), "1", "superadmin", 0)
    headers = {"Authorization": "Bearer " + token}
    entry = client.get("/api/virtual-office/session", headers=headers)
    assert entry.status_code == 200
    assert entry.json() == {
        "state": "BOOTSTRAP_REQUIRED",
        "identity": {"user_id": user_id, "tenant_id": 1, "account_class": "INTERNAL", "global_role": None},
        "bootstrap": {"method": "CURRENT_PASSWORD", "target_user_id": user_id},
    }
    rejected = client.post("/api/virtual-office/bootstrap", headers=headers, json={"current_password": "wrong"})
    assert (rejected.status_code, rejected.json()) == (401, {"detail": "bootstrap_reauth_failed"})
    activated = client.post(
        "/api/virtual-office/bootstrap", headers=headers,
        json={"current_password": "office-bootstrap-current-password"},
    )
    assert activated.status_code == 200
    payload = activated.json()
    assert payload["state"] == "ACTIVE"
    assert payload["verification_method"] == "PASSWORD_REAUTH"
    assert payload["replay"] is False
    assert isinstance(payload["access_token"], str)
    assert client.get("/api/virtual-office/session", headers=headers).status_code == 401
    fresh = {"Authorization": "Bearer " + payload["access_token"]}
    assert client.get("/api/virtual-office/session", headers=fresh).json()["state"] == "ACTIVE"


def test_office_session_allows_root_project_creation_without_mfa(client, monkeypatch):
    _configure_mfa(monkeypatch)
    user_id, token_version, _mfa_version, _proof = client.portal.call(_office_user)
    response = client.get("/api/virtual-office/session", headers=_headers(user_id, token_version, 0, None))
    assert response.status_code == 200
    assert response.json()["mfa"] == {"verified": False, "recent": False}
    assert response.json()["capabilities"] == {
        "create_project": True,
        "administer_office": False,
        "administer_identity_security": False,
    }


def test_office_session_keeps_project_admin_and_external_client_out_of_global_capabilities(client, monkeypatch):
    _configure_mfa(monkeypatch)
    project_admin, tv, mv, proof = client.portal.call(partial(_office_user, global_role="COLLABORATOR"))
    client.portal.call(_make_project_admin, project_admin)
    external, external_tv, external_mv, _ = client.portal.call(partial(
        _office_user, account_class="EXTERNAL_CLIENT", global_role="COLLABORATOR",
    ))
    malformed_external, malformed_tv, malformed_mv, malformed_proof = client.portal.call(partial(
        _office_user, account_class="EXTERNAL_CLIENT", global_role="GENERAL_ADMIN",
    ))
    admin = client.get("/api/virtual-office/session", headers=_headers(project_admin, tv, mv, proof))
    client_entry = client.get("/api/virtual-office/session", headers=_headers(external, external_tv, external_mv, None))
    assert admin.status_code == client_entry.status_code == 200
    assert admin.json()["capabilities"] == {
        "create_project": False, "administer_office": False, "administer_identity_security": False,
    }
    assert client_entry.json()["identity"]["account_class"] == "EXTERNAL_CLIENT"
    assert client_entry.json()["mfa"] == {"verified": False, "recent": False}
    assert client_entry.json()["capabilities"] == admin.json()["capabilities"]
    malformed = client.get(
        "/api/virtual-office/session",
        headers=_headers(malformed_external, malformed_tv, malformed_mv, malformed_proof),
    )
    assert (malformed.status_code, malformed.json()) == (403, {"detail": "office_access_denied"})


def test_office_session_fails_closed_for_expired_revoked_and_unknown_mfa_references(client, monkeypatch):
    _configure_mfa(monkeypatch)
    expired, tv, mv, expired_proof = client.portal.call(partial(
        _office_user, proof_time="UTC_TIMESTAMP(6) - INTERVAL 301 SECOND",
    ))
    expired_response = client.get("/api/virtual-office/session", headers=_headers(expired, tv, mv, expired_proof))
    assert expired_response.status_code == 200
    assert expired_response.json()["mfa"] == {"verified": True, "recent": False}
    assert expired_response.json()["capabilities"] == {
        "create_project": True,
        "administer_office": False,
        "administer_identity_security": False,
    }

    revoked, old_tv, mv, revoked_proof = client.portal.call(_office_user)
    client.portal.call(sql, "UPDATE jax_users SET token_version=token_version+1 WHERE user_id=%s", (revoked,))
    revoked_response = client.get("/api/virtual-office/session", headers=_headers(revoked, old_tv, mv, revoked_proof))
    unknown_response = client.get("/api/virtual-office/session", headers=_headers(revoked, old_tv + 1, mv, 999999999))
    assert (revoked_response.status_code, revoked_response.json()) == (401, {"detail": "sesion_invalida"})
    assert unknown_response.status_code == 200
    assert unknown_response.json()["mfa"] == {"verified": False, "recent": False}
    assert all(value is False for value in unknown_response.json()["capabilities"].values())


def test_office_session_rechecks_token_cut_after_dependency_before_response(client, monkeypatch):
    _configure_mfa(monkeypatch)
    user_id, tv, mv, proof = client.portal.call(_office_user)
    import api.virtual_office.session as session_api

    original_fetch_actor = session_api.repository.fetch_actor

    async def cut_after_actor_lock(cur, actor_user_id, *, for_update):
        actor = await original_fetch_actor(cur, actor_user_id, for_update=for_update)
        await cur.execute("UPDATE jax_users SET token_version=token_version+1 WHERE user_id=%s", (actor_user_id,))
        return actor

    monkeypatch.setattr(session_api.repository, "fetch_actor", cut_after_actor_lock)
    response = client.get("/api/virtual-office/session", headers=_headers(user_id, tv, mv, proof))
    assert (response.status_code, response.json()) == (401, {"detail": "sesion_invalida"})


def test_office_session_never_returns_partial_capabilities_on_configuration_or_database_failure(client, monkeypatch):
    _configure_mfa(monkeypatch)
    user_id, tv, mv, proof = client.portal.call(_office_user)
    for name in ("OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS", "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS",
                 "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS", "OFFICE_MFA_RECENCY_SECONDS",
                 "OFFICE_MFA_RECOVERY_CODE_COUNT"):
        monkeypatch.delenv(name)
    unavailable = client.get("/api/virtual-office/session", headers=_headers(user_id, tv, mv, proof))
    assert (unavailable.status_code, unavailable.json()) == (503, {"detail": "office_session_unavailable"})

    _configure_mfa(monkeypatch)
    import api.virtual_office.session as session_api

    async def broken_actor(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(session_api.repository, "fetch_actor", broken_actor)
    broken = client.get("/api/virtual-office/session", headers=_headers(user_id, tv, mv, proof))
    assert (broken.status_code, broken.json()) == (503, {"detail": "office_session_unavailable"})
