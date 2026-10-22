"""Real HTTP/DB checks for the MFA login cut and its transport receipts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pyotp
import pytest

from auth.jwt import create_access_token, create_refresh_token, create_refresh_token_preserving_window, decode_token
from auth.rate_limit import reset_login_limiters
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.identidades import crear_usuario, sql
from virtual_office.mfa import (
    MFAConfig, confirm_enrollment_in_cursor,
    issue_step_up_challenge_in_cursor, start_first_enrollment_after_password_in_cursor,
    consume_recovery_code_in_cursor, start_recovery_enrollment_in_cursor,
)
from virtual_office.migrations import apply_office_migrations

CONFIG = MFAConfig(120, 3, 300, 300, 3)
PASSWORD = "mfa-login-api-password"


def _mfa_env(monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120",
        "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300",
        "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)
    reset_login_limiters()


def _previous_otp(secret: str) -> str:
    return pyotp.TOTP(secret, digits=6, interval=30).at((int(__import__("time").time()) // 30 - 1) * 30)


async def _active_factor_user():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await apply_office_migrations(cur)
    user_id, email = await crear_usuario(password=PASSWORD)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        started = await start_first_enrollment_after_password_in_cursor(
            cur, preauthenticated_user_id=user_id, config=CONFIG,
        )
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await confirm_enrollment_in_cursor(
            cur, enrollment_capability_token=started.enrollment_id,
            otp=pyotp.TOTP(started.secret, digits=6, interval=30).now(),
            config=CONFIG, server_correlation_id=b"L" * 16,
        )
    return user_id, email, started.secret


def _login_challenge(client, email: str):
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["mfa_required"] is True
    assert "access_token" not in response.json()
    assert "refresh_token" not in response.headers.get("set-cookie", "")
    assert response.headers["cache-control"] == "no-store"
    return response.json()["challenge_token"]


def test_mfa_login_http_emits_only_post_cut_durable_claims(client, monkeypatch):
    _mfa_env(monkeypatch)
    user_id, email, secret = client.portal.call(_active_factor_user)
    challenge = _login_challenge(client, email)
    before = client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True)[0]
    response = client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge, "otp": _previous_otp(secret)})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    access = decode_token(response.json()["access_token"])
    refresh = decode_token(response.cookies.get("refresh_token"))
    rows = client.portal.call(sql,
        "SELECT token_version,mfa_version,UNIX_TIMESTAMP(last_login),"
        "(SELECT UNIX_TIMESTAMP(occurred_at) FROM office_mfa_security_event WHERE event_id=%s) "
        "FROM jax_users WHERE user_id=%s", (access["mfa_proof_event_id"], user_id), True)
    token_version, mfa_version, last_login_epoch, proof_epoch = rows[0]
    assert (token_version, mfa_version) == (before[0] + 1, before[1])
    assert access["tv"] == token_version == refresh["tv"]
    assert access["mfa_version"] == mfa_version == refresh["mfa_version"]
    assert access["mfa_at"] == int(proof_epoch) == refresh["mfa_at"]
    assert abs(int(last_login_epoch) - access["mfa_at"]) < 3


def test_refresh_preserves_login_proof_recency_and_absolute_window(client, monkeypatch):
    _mfa_env(monkeypatch)
    _user_id, email, secret = client.portal.call(_active_factor_user)
    challenge = _login_challenge(client, email)
    login = client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge, "otp": _previous_otp(secret)})
    assert login.status_code == 200
    original_refresh = decode_token(login.cookies.get("refresh_token"))
    refreshed = client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={login.cookies.get('refresh_token')}"})
    assert refreshed.status_code == 200
    access = decode_token(refreshed.json()["access_token"])
    for claim in ("mfa_proof_event_id", "mfa_version", "mfa_at"):
        assert access[claim] == original_refresh[claim]
    assert "refresh_token" not in refreshed.headers.get("set-cookie", "")


@pytest.mark.parametrize("zone", ["-06:00", "+05:30"])
def test_http_mfa_login_keeps_utc_claims_under_hostile_database_session_zone(client, monkeypatch, zone):
    """Run the actual HTTP route through a one-connection pool fixed to zone."""
    _mfa_env(monkeypatch)
    import db.connection as connection

    real_create_pool = connection.aiomysql.create_pool
    async def one_connection_pool(*args, **kwargs):
        kwargs["minsize"] = 1
        kwargs["maxsize"] = 1
        return await real_create_pool(*args, **kwargs)
    monkeypatch.setattr(connection.aiomysql, "create_pool", one_connection_pool)

    async def reset_and_set_zone():
        await connection.close_pool()
        pool = await connection.get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SET time_zone=%s", (zone,))
                await cur.execute("SELECT @@session.time_zone")
                assert (await cur.fetchone())[0] == zone

    client.portal.call(reset_and_set_zone)
    try:
        user_id, email, secret = client.portal.call(_active_factor_user)
        challenge = _login_challenge(client, email)
        # The challenge's DATETIME is written/read under the actual hostile
        # endpoint pool session but must still be a UTC TTL.
        ttl = client.portal.call(sql,
            "SELECT TIMESTAMPDIFF(SECOND,UTC_TIMESTAMP(6),expires_at) FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))",
            (challenge,), True)[0][0]
        assert 115 <= int(ttl) <= 120
        login = client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge, "otp": _previous_otp(secret)})
        assert login.status_code == 200
        access = decode_token(login.json()["access_token"])
        refresh = decode_token(login.cookies.get("refresh_token"))
        proof_epoch, login_epoch, live_zone = client.portal.call(sql,
            "SELECT (SELECT TIMESTAMPDIFF(SECOND,'1970-01-01 00:00:00',occurred_at) FROM office_mfa_security_event WHERE event_id=%s),"
            "UNIX_TIMESTAMP(last_login),@@session.time_zone FROM jax_users WHERE user_id=%s",
            (access["mfa_proof_event_id"], user_id), True)[0]
        assert live_zone == zone
        assert access["mfa_at"] == int(proof_epoch) == refresh["mfa_at"]
        assert abs(int(login_epoch) - access["mfa_at"]) < 3
        renewed = client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={login.cookies.get('refresh_token')}"})
        assert renewed.status_code == 200
        renewed_access = decode_token(renewed.json()["access_token"])
        for claim in ("mfa_proof_event_id", "mfa_version", "mfa_at"):
            assert renewed_access[claim] == refresh[claim]
    finally:
        client.portal.call(connection.close_pool)


def test_mfa_login_invalid_replay_and_exhaustion_never_cut_or_mint_cookie(client, monkeypatch):
    _mfa_env(monkeypatch)
    user_id, email, secret = client.portal.call(_active_factor_user)
    before = client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True)[0]
    challenge = _login_challenge(client, email)
    for _ in range(3):
        denied = client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge, "otp": "000000"})
        assert denied.status_code == 401
        assert "refresh_token" not in denied.headers.get("set-cookie", "")
    exhausted = client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge, "otp": _previous_otp(secret)})
    assert exhausted.status_code == 401
    assert client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True) == (before,)


def test_expired_mfa_login_capability_never_mints_a_session(client, monkeypatch):
    _mfa_env(monkeypatch)
    user_id, email, secret = client.portal.call(_active_factor_user)
    before = client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True)
    challenge = _login_challenge(client, email)
    client.portal.call(sql,
        "UPDATE office_mfa_challenge SET expires_at=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND WHERE token_hash=UNHEX(SHA2(%s,256))",
        (challenge,))
    denied = client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge, "otp": _previous_otp(secret)})
    assert denied.status_code == 401
    assert "refresh_token" not in denied.headers.get("set-cookie", "")
    assert client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True) == before


def test_successful_mfa_login_cuts_real_ws_and_sse_registries_after_commit(client, monkeypatch):
    _mfa_env(monkeypatch)
    import auth.conexiones as conexiones

    user_id, email, secret = client.portal.call(_active_factor_user)
    calls = []
    async def ws(subject): calls.append(("ws", subject))
    async def sse(subject): calls.append(("sse", subject))
    monkeypatch.setattr(conexiones.ws_hub, "close_user", ws)
    monkeypatch.setattr(conexiones, "close_user_streams", sse)
    challenge = _login_challenge(client, email)
    accepted = client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge, "otp": _previous_otp(secret)})
    assert accepted.status_code == 200
    assert calls == [("ws", str(user_id)), ("sse", str(user_id))]


@pytest.mark.parametrize("mutation", ["token", "mfa"])
def test_mfa_login_version_changes_between_challenge_and_verify_never_mint_session(client, monkeypatch, mutation):
    _mfa_env(monkeypatch)
    user_id, email, secret = client.portal.call(_active_factor_user)
    challenge = _login_challenge(client, email)
    before = client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True)[0]
    column = "token_version" if mutation == "token" else "mfa_version"
    client.portal.call(sql, f"UPDATE jax_users SET {column}={column}+1 WHERE user_id=%s", (user_id,))
    denied = client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge, "otp": _previous_otp(secret)})
    assert denied.status_code == 401
    assert "refresh_token" not in denied.headers.get("set-cookie", "")
    expected = (before[0] + 1, before[1]) if mutation == "token" else (before[0], before[1] + 1)
    assert client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True) == (expected,)


def test_mfa_login_append_failure_rolls_back_counter_cut_and_connection_close(client, monkeypatch):
    _mfa_env(monkeypatch)
    import api.auth as auth_api
    import virtual_office.mfa as mfa

    user_id, email, secret = client.portal.call(_active_factor_user)
    challenge = _login_challenge(client, email)
    before = client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True)
    counter_before = client.portal.call(sql, "SELECT COUNT(*) FROM office_mfa_totp_use WHERE user_id=%s", (user_id,), True)
    cuts = []
    async def fail_append(*_args, **_kwargs):
        raise RuntimeError("append failure")
    async def record_cut(subject):
        cuts.append(subject)
    monkeypatch.setattr(mfa, "_append_proof", fail_append)
    monkeypatch.setattr(auth_api, "_cortar_conexiones", record_cut)
    with pytest.raises(RuntimeError, match="append failure"):
        client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge, "otp": _previous_otp(secret)})
    assert cuts == []
    assert client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True) == before
    assert client.portal.call(sql, "SELECT state,attempt_count FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))", (challenge,), True) == (("ISSUED", 0),)
    assert client.portal.call(sql, "SELECT COUNT(*) FROM office_mfa_totp_use WHERE user_id=%s", (user_id,), True) == counter_before


def test_two_http_mfa_login_verifiers_have_exactly_one_winner(client, monkeypatch):
    _mfa_env(monkeypatch)
    user_id, email, secret = client.portal.call(_active_factor_user)
    challenge = _login_challenge(client, email)
    before = client.portal.call(sql, "SELECT token_version FROM jax_users WHERE user_id=%s", (user_id,), True)[0][0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda _unused: client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge, "otp": _previous_otp(secret)}),
            range(2),
        ))
    assert sorted(response.status_code for response in responses) == [200, 401]
    assert client.portal.call(sql, "SELECT token_version FROM jax_users WHERE user_id=%s", (user_id,), True) == ((before + 1,),)


@pytest.mark.parametrize("bad", [None, "", " 120", "0120", "true", "1.5", "901"])
def test_missing_or_noncanonical_mfa_config_never_falls_back_for_active_factor(client, monkeypatch, bad):
    _mfa_env(monkeypatch)
    _user_id, email, _secret = client.portal.call(_active_factor_user)
    if bad is None:
        monkeypatch.delenv("OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS")
    else:
        monkeypatch.setenv("OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS", bad)
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 503
    assert response.json() == {"detail": "office_mfa_unavailable"}
    assert "access_token" not in response.text
    assert "refresh_token" not in response.headers.get("set-cookie", "")


def test_classic_no_factor_login_remains_compatible_when_mfa_config_is_absent(client, monkeypatch):
    monkeypatch.delenv("OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS", raising=False)
    reset_login_limiters()
    async def create():
        return await crear_usuario(password=PASSWORD)
    user_id, email = client.portal.call(create)
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["user_id"] == user_id
    assert "access_token" in response.json()
    assert "refresh_token" in response.headers.get("set-cookie", "")


def test_auth_validation_hides_login_password_and_sets_no_store(client):
    sentinel = "password-SENTINEL-must-not-echo"
    response = client.post("/api/auth/login", json={"email": [sentinel], "password": sentinel})
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert sentinel not in response.text
    assert response.json() == {"detail": "auth_invalid_request"}


def test_reenrollment_http_preserves_active_factor_until_confirmation(client, monkeypatch):
    _mfa_env(monkeypatch)
    user_id, _email, old_secret = client.portal.call(_active_factor_user)
    versions = client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True)[0]
    access = create_access_token(str(user_id), "1", "operator", versions[0])
    now = int(__import__("time").time())
    refresh = create_refresh_token_preserving_window(
        str(user_id), "1", "operator", versions[0], issued_at=now - 2, expires_at=now + 600,
        mfa_proof_event_id=1, mfa_version=versions[1], mfa_at=now - 2,
    )
    issued = client.post("/api/auth/mfa/step-up", headers={"Authorization": f"Bearer {access}"})
    proof = client.post("/api/auth/mfa/step-up/verify", headers={
        "Authorization": f"Bearer {access}", "Cookie": f"refresh_token={refresh}",
    }, json={"challenge_token": issued.json()["challenge_token"], "otp": _previous_otp(old_secret)})
    assert proof.status_code == 200, proof.text
    started = client.post("/api/auth/mfa/reenrollment/start", headers={
        "Authorization": f"Bearer {proof.json()['access_token']}"}, json={"password": PASSWORD})
    assert started.status_code == 200 and started.headers["cache-control"] == "no-store"
    assert client.portal.call(sql, "SELECT state,secret_ciphertext IS NOT NULL,pending_secret_ciphertext IS NOT NULL FROM office_mfa_credential WHERE user_id=%s", (user_id,), True) == (("ACTIVE", 1, 1),)
    confirmed = client.post("/api/auth/mfa/enrollment/confirm", json={
        "enrollment_token": started.json()["enrollment_token"],
        "otp": pyotp.TOTP(started.json()["secret"], digits=6, interval=30).now(),
    })
    assert confirmed.status_code == 200
    assert client.portal.call(sql, "SELECT state,pending_secret_ciphertext FROM office_mfa_credential WHERE user_id=%s", (user_id,), True) == (("ACTIVE", None),)


def test_recovery_required_password_login_never_mints_session(client, monkeypatch):
    _mfa_env(monkeypatch)
    user_id, email, _secret = client.portal.call(_active_factor_user)
    async def recovery():
        rows = await sql("SELECT code_hash FROM office_mfa_recovery_code WHERE user_id=%s LIMIT 1", (user_id,), True)
        # Use the stored plaintext unavailable here: switch state directly only
        # for the RECOVERY_REQUIRED HTTP boundary, then construct pending with
        # the real recovery lineage primitives in their dedicated tests.
        await sql("UPDATE office_mfa_credential SET state='RECOVERY_REQUIRED',secret_ciphertext=NULL,pending_secret_ciphertext=NULL,pending_enrollment_id=NULL,pending_expires_at=NULL WHERE user_id=%s", (user_id,))
    client.portal.call(recovery)
    denied = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert denied.status_code == 403
    assert "refresh_token" not in denied.headers.get("set-cookie", "")


def test_recovery_origin_pending_password_login_never_mints_session(client, monkeypatch):
    _mfa_env(monkeypatch)
    async def arrange():
        from virtual_office.migrations import apply_office_migrations
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur: await apply_office_migrations(cur)
        user_id, email = await crear_usuario(password=PASSWORD)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            first = await start_first_enrollment_after_password_in_cursor(cur, preauthenticated_user_id=user_id, config=CONFIG)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            enrolled = await confirm_enrollment_in_cursor(cur, enrollment_capability_token=first.enrollment_id, otp=pyotp.TOTP(first.secret, digits=6, interval=30).now(), config=CONFIG, server_correlation_id=b"P" * 16)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            recovery = await consume_recovery_code_in_cursor(cur, recovery_code=enrolled.recovery_codes[0], config=CONFIG, server_correlation_id=b"Q" * 16)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            pending = await start_recovery_enrollment_in_cursor(cur, recovery_capability_token=recovery.token, config=CONFIG)
        return user_id, email, pending
    user_id, email, pending = client.portal.call(arrange)
    assert pending.enrollment_id
    assert client.portal.call(sql, "SELECT state FROM office_mfa_credential WHERE user_id=%s", (user_id,), True) == (("PENDING",),)
    denied = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert denied.status_code == 403
    assert "access_token" not in denied.text and "refresh_token" not in denied.headers.get("set-cookie", "")


def test_live_lock_retires_login_challenge_without_proof_or_version_bump(client, monkeypatch):
    _mfa_env(monkeypatch)
    user_id, email, secret = client.portal.call(_active_factor_user)
    before = client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True)
    challenge = _login_challenge(client, email)
    client.portal.call(sql, "UPDATE jax_users SET locked_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 5 MINUTE) WHERE user_id=%s", (user_id,))
    denied = client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge, "otp": _previous_otp(secret)})
    assert denied.status_code in {401, 429}
    assert client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True) == before
    assert client.portal.call(sql, "SELECT state FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))", (challenge,), True) == (("INVALIDATED",),)


def test_step_up_refresh_obeys_current_absolute_session_policy(client, monkeypatch):
    _mfa_env(monkeypatch)
    user_id, _email, secret = client.portal.call(_active_factor_user)
    versions = client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True)[0]
    access = create_access_token(str(user_id), "1", "operator", versions[0])
    now = int(__import__("time").time())
    refresh = create_refresh_token_preserving_window(
        str(user_id), "1", "operator", versions[0], issued_at=now - 2, expires_at=now + 600,
        mfa_proof_event_id=1, mfa_version=versions[1], mfa_at=now - 2,
    )
    import api.auth as auth_api
    # JWT exp remains ten minutes away; only the *current* absolute policy is
    # shortened below its original iat age.
    async def shortened_lifetime(): return 1
    monkeypatch.setattr(auth_api, "vida_de_sesion_segundos", shortened_lifetime)
    issued = client.post("/api/auth/mfa/step-up", headers={"Authorization": f"Bearer {access}"})
    denied = client.post("/api/auth/mfa/step-up/verify", headers={"Authorization": f"Bearer {access}", "Cookie": f"refresh_token={refresh}"}, json={"challenge_token": issued.json()["challenge_token"], "otp": _previous_otp(secret)})
    assert denied.status_code == 401
    assert denied.headers["cache-control"] == "no-store"
