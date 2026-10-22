"""The cursor-only MFA boundary persists durable evidence before returning it."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import datetime as dt
import secrets

import pyotp
import pytest

from auth.jwt import create_access_token, create_refresh_token, decode_token
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.identidades import crear_usuario, sql
from virtual_office.mfa import (
    MFAConfig, MFADenied, confirm_enrollment_in_cursor,
    consume_recovery_code_in_cursor, disable_mfa_after_password_in_cursor,
    issue_login_challenge_after_password_in_cursor,
    issue_step_up_challenge_in_cursor,
    regenerate_recovery_codes_after_password_in_cursor,
    reload_current_self_mfa_proof_in_cursor,
    start_first_enrollment_after_password_in_cursor,
    start_reenrollment_after_password_in_cursor,
    start_recovery_enrollment_in_cursor,
    verify_and_consume_totp_in_cursor,
)
from virtual_office.migrations import apply_office_migrations

CONFIG = MFAConfig(120, 3, 300, 300, 3)

async def _migrate():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await apply_office_migrations(cur)

async def _first_enrollment(user_id: int):
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        return await start_first_enrollment_after_password_in_cursor(cur, preauthenticated_user_id=user_id, config=CONFIG)

async def _active_user():
    user_id, _ = await crear_usuario()
    start = await _first_enrollment(user_id)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        confirmed = await confirm_enrollment_in_cursor(cur, enrollment_capability_token=start.enrollment_id, otp=_otp(start.secret), config=CONFIG, server_correlation_id=b"z" * 16)
    return user_id, start, confirmed

def _otp(secret: str) -> str:
    return pyotp.TOTP(secret, digits=6, interval=30).now()

def _previous_otp(secret: str) -> str:
    return pyotp.TOTP(secret, digits=6, interval=30).at((dt.datetime.now().timestamp() // 30 - 1) * 30)

async def _step_up_proof(user_id: int, secret: str, correlation: bytes):
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        challenge = await issue_step_up_challenge_in_cursor(cur, actor_user_id=user_id, config=CONFIG)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        return await verify_and_consume_totp_in_cursor(cur, challenge_token=challenge.token, otp=_previous_otp(secret), server_correlation_id=correlation)


@asynccontextmanager
async def _session_timezone_transaction(zone: str):
    """Run one MFA transaction under a hostile session zone and restore it."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT @@session.time_zone")
            original = (await cur.fetchone())[0]
            await cur.execute("SET time_zone=%s", (zone,))
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                yield cur
        except BaseException:
            await conn.rollback()
            raise
        else:
            await conn.commit()
        finally:
            async with conn.cursor() as cur:
                await cur.execute("SET time_zone=%s", (original,))

def test_cursor_enrollment_confirms_with_durable_proof_and_no_authorization_event(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        before = await sql("SELECT COUNT(*) FROM office_authorization_event", (), True)
        start = await _first_enrollment(user_id)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            result = await confirm_enrollment_in_cursor(cur, enrollment_capability_token=start.enrollment_id, otp=_otp(start.secret), config=CONFIG, server_correlation_id=b"a" * 16)
        assert result.mfa_version == 1
        events = await sql("SELECT event_kind,result,purpose FROM office_mfa_security_event WHERE subject_user_id=%s ORDER BY event_id", (user_id,), True)
        assert events == (("MFA_PROOF", "VERIFIED", "ENROLLMENT"), ("MFA_STATE_CHANGE", "RECORDED", "ENROLLMENT"))
        assert await sql("SELECT COUNT(*) FROM office_authorization_event", (), True) == before
    client.portal.call(scenario)

def test_login_invalid_otp_commits_attempt_and_returns_denial(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        start = await _first_enrollment(user_id)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            await confirm_enrollment_in_cursor(cur, enrollment_capability_token=start.enrollment_id, otp=_otp(start.secret), config=CONFIG, server_correlation_id=b"b" * 16)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            challenge = await issue_login_challenge_after_password_in_cursor(cur, preauthenticated_user_id=user_id, config=CONFIG)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            denied = await verify_and_consume_totp_in_cursor(cur, challenge_token=challenge.token, otp="000000", server_correlation_id=b"c" * 16)
        assert isinstance(denied, MFADenied) and denied.attempts_remaining == 2
        assert await sql("SELECT attempt_count FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))", (challenge.token,), True) == ((1,),)
    client.portal.call(scenario)


def _enable_mfa_api(monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120",
        "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300",
        "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)


def test_step_up_verify_mints_current_durable_mfa_claims_without_extending_refresh(client, monkeypatch):
    _enable_mfa_api(monkeypatch)
    async def scenario():
        await _migrate()
        user_id, start, _ = await _active_user()
        return user_id, start.secret, (await sql(
            "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True,
        ))[0]

    user_id, secret, versions = client.portal.call(scenario)
    access = create_access_token(str(user_id), "1", "operator", versions[0])
    original_refresh = create_refresh_token(
        str(user_id), "1", "operator", versions[0], vida_segundos=600,
    )
    original_payload = decode_token(original_refresh)
    headers = {"Authorization": f"Bearer {access}", "Cookie": f"refresh_token={original_refresh}"}
    issued = client.post("/api/auth/mfa/step-up", headers={"Authorization": f"Bearer {access}"})
    assert issued.status_code == 200
    assert issued.headers["cache-control"] == "no-store"
    verified = client.post("/api/auth/mfa/step-up/verify", headers=headers, json={
        "challenge_token": issued.json()["challenge_token"], "otp": _previous_otp(secret),
    })
    assert verified.status_code == 200, verified.json()
    assert verified.headers["cache-control"] == "no-store"
    access_payload = decode_token(verified.json()["access_token"])
    refresh_payload = decode_token(verified.cookies.get("refresh_token"))
    assert access_payload["mfa_proof_event_id"] > 0
    assert access_payload["mfa_version"] == versions[1]
    assert refresh_payload["mfa_proof_event_id"] == access_payload["mfa_proof_event_id"]
    assert (refresh_payload["iat"], refresh_payload["exp"]) == (original_payload["iat"], original_payload["exp"])
    assert client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True) == (versions,)


def test_step_up_verify_rejects_other_subject_before_consuming_its_challenge(client, monkeypatch):
    _enable_mfa_api(monkeypatch)
    async def scenario():
        await _migrate()
        owner, owner_start, _ = await _active_user()
        other, _, _ = await _active_user()
        return owner, owner_start.secret, other

    owner, owner_secret, other = client.portal.call(scenario)
    owner_versions = client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (owner,), True)[0]
    owner_access = create_access_token(str(owner), "1", "operator", owner_versions[0])
    owner_refresh = create_refresh_token(str(owner), "1", "operator", owner_versions[0], vida_segundos=600)
    issued = client.post("/api/auth/mfa/step-up", headers={"Authorization": f"Bearer {owner_access}"})
    assert issued.status_code == 200
    other_versions = client.portal.call(sql, "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (other,), True)[0]
    other_access = create_access_token(str(other), "1", "operator", other_versions[0])
    other_refresh = create_refresh_token(str(other), "1", "operator", other_versions[0], vida_segundos=600)
    denied = client.post("/api/auth/mfa/step-up/verify", headers={
        "Authorization": f"Bearer {other_access}", "Cookie": f"refresh_token={other_refresh}",
    }, json={"challenge_token": issued.json()["challenge_token"], "otp": _previous_otp(owner_secret)})
    assert denied.status_code == 401
    assert denied.headers["cache-control"] == "no-store"
    # The owner can still consume the capability: the other subject did not
    # burn its TOTP counter or turn its one-time challenge into a denial.
    accepted = client.post("/api/auth/mfa/step-up/verify", headers={
        "Authorization": f"Bearer {owner_access}", "Cookie": f"refresh_token={owner_refresh}",
    }, json={"challenge_token": issued.json()["challenge_token"], "otp": _previous_otp(owner_secret)})
    assert accepted.status_code == 200


def test_pending_enrollment_budget_is_committed_and_reissue_does_not_replace_secret(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        first = await _first_enrollment(user_id)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            denied = await confirm_enrollment_in_cursor(
                cur, enrollment_capability_token=first.enrollment_id, otp="000000",
                config=CONFIG, server_correlation_id=b"d" * 16,
            )
        assert isinstance(denied, MFADenied) and denied.attempts_remaining == 2
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            reissue = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert isinstance(reissue, MFADenied) and reissue.code == "challenge_live"
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            confirmed = await confirm_enrollment_in_cursor(
                cur, enrollment_capability_token=first.enrollment_id, otp=_otp(first.secret),
                config=CONFIG, server_correlation_id=b"e" * 16,
            )
        assert confirmed.mfa_version == 1
    client.portal.call(scenario)


def test_enrollment_capability_cannot_be_verified_by_login_consumer(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        start = await _first_enrollment(user_id)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            denied = await verify_and_consume_totp_in_cursor(
                cur, challenge_token=start.enrollment_id, otp=_otp(start.secret),
                server_correlation_id=b"f" * 16,
            )
        assert isinstance(denied, MFADenied) and denied.code == "challenge_unavailable"
        count = await sql("SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s", (user_id,), True)
        assert count == ((0,),)
    client.portal.call(scenario)


def test_recovery_consumes_one_code_then_reenrolls_without_minting_login_proof(client):
    async def scenario():
        await _migrate()
        user_id, _, enrolled = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            capability = await consume_recovery_code_in_cursor(
                cur, recovery_code=enrolled.recovery_codes[0], config=CONFIG,
                server_correlation_id=b"g" * 16,
            )
        assert not isinstance(capability, MFADenied)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            replacement = await start_recovery_enrollment_in_cursor(
                cur, recovery_capability_token=capability.token, config=CONFIG,
            )
        assert not isinstance(replacement, MFADenied)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            result = await confirm_enrollment_in_cursor(
                cur, enrollment_capability_token=replacement.enrollment_id,
                otp=_otp(replacement.secret), config=CONFIG,
                server_correlation_id=b"h" * 16,
            )
        assert result.mfa_version == 3
        proofs = await sql("SELECT purpose FROM office_mfa_security_event WHERE subject_user_id=%s AND event_kind='MFA_PROOF' ORDER BY event_id", (user_id,), True)
        assert proofs == (("ENROLLMENT",), ("RECOVERY",), ("ENROLLMENT",))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            used = await start_recovery_enrollment_in_cursor(
                cur, recovery_capability_token=capability.token, config=CONFIG,
            )
        assert isinstance(used, MFADenied)
    client.portal.call(scenario)


def test_durable_self_proof_is_bound_to_subject_and_current_versions(client):
    async def scenario():
        await _migrate()
        user_id, start, _ = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            challenge = await issue_login_challenge_after_password_in_cursor(cur, preauthenticated_user_id=user_id, config=CONFIG)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            proof = await verify_and_consume_totp_in_cursor(cur, challenge_token=challenge.token, otp=_previous_otp(start.secret), server_correlation_id=b"i" * 16)
        other, _ = await crear_usuario()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            wrong = await reload_current_self_mfa_proof_in_cursor(cur, actor_user_id=other, proof_event_id=proof.event_id, config=CONFIG)
        assert isinstance(wrong, MFADenied)
        await sql("UPDATE jax_users SET mfa_version=mfa_version+1 WHERE user_id=%s", (user_id,))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            stale = await reload_current_self_mfa_proof_in_cursor(cur, actor_user_id=user_id, proof_event_id=proof.event_id, config=CONFIG)
        assert isinstance(stale, MFADenied) and stale.code == "proof_stale"
    client.portal.call(scenario)


def test_concurrent_otp_and_recovery_consumption_have_one_durable_winner(client):
    async def scenario():
        await _migrate()
        user_id, start, enrolled = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            challenge = await issue_login_challenge_after_password_in_cursor(cur, preauthenticated_user_id=user_id, config=CONFIG)

        async def consume_login():
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await verify_and_consume_totp_in_cursor(
                    cur, challenge_token=challenge.token, otp=_previous_otp(start.secret),
                    server_correlation_id=secrets.token_bytes(16),
                )

        login_results = await asyncio.gather(consume_login(), consume_login())
        assert sum(not isinstance(result, MFADenied) for result in login_results) == 1

        async def consume_code():
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await consume_recovery_code_in_cursor(
                    cur, recovery_code=enrolled.recovery_codes[0], config=CONFIG,
                    server_correlation_id=secrets.token_bytes(16),
                )

        recovery_results = await asyncio.gather(consume_code(), consume_code())
        assert sum(not isinstance(result, MFADenied) for result in recovery_results) == 1
        assert await sql("SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s AND purpose='RECOVERY' AND event_kind='MFA_STATE_CHANGE'", (user_id,), True) == ((1,),)
    client.portal.call(scenario)


def test_regeneration_and_disable_require_current_durable_self_proofs(client):
    async def scenario():
        await _migrate()
        user_id, start, _ = await _active_user()
        proof = await _step_up_proof(user_id, start.secret, b"j" * 16)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            regenerated = await regenerate_recovery_codes_after_password_in_cursor(
                cur, actor_user_id=user_id, proof_event_id=proof.event_id, config=CONFIG,
                server_correlation_id=b"k" * 16,
            )
        assert not isinstance(regenerated, MFADenied) and regenerated.mfa_version == 2
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            stale_disable = await disable_mfa_after_password_in_cursor(
                cur, actor_user_id=user_id, proof_event_id=proof.event_id, config=CONFIG,
                server_correlation_id=b"l" * 16,
            )
        assert isinstance(stale_disable, MFADenied)
        proof2 = await _step_up_proof(user_id, start.secret, b"m" * 16)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            disabled = await disable_mfa_after_password_in_cursor(
                cur, actor_user_id=user_id, proof_event_id=proof2.event_id, config=CONFIG,
                server_correlation_id=b"n" * 16,
            )
        assert not isinstance(disabled, MFADenied) and disabled.mfa_version == 3
        assert await sql("SELECT state FROM office_mfa_credential WHERE user_id=%s", (user_id,), True) == (("DISABLED",),)
    client.portal.call(scenario)


def test_office_root_reset_is_actor_bound_records_both_ledgers_and_cuts_only_target(client, monkeypatch):
    """The HTTP surface supplies only a target; all authority comes from JWT+DB."""
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120",
        "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300",
        "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)

    async def arrange():
        await _migrate()
        root, root_start, _ = await _active_user()
        target, _, _ = await _active_user()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'OFFICE_SUPERUSER','ACTIVE')", (root,))
        proof = await _step_up_proof(root, root_start.secret, b"R" * 16)
        before_root = await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (root,), True)
        before_target = await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (target,), True)
        token = create_access_token(
            str(root), "1", "operator", before_root[0][0],
            mfa_proof_event_id=proof.event_id, mfa_version=proof.mfa_version,
            mfa_at=int(proof.occurred_at.timestamp()),
        )
        return root, target, proof, before_root, before_target, token

    root, target, proof, before_root, before_target, token = client.portal.call(arrange)
    import api.auth as auth_api
    cuts = []
    async def observe_cut(user_id):
        # This runs only after the route's transaction context has exited.
        cuts.append((int(user_id), await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (int(user_id),), True)))
    monkeypatch.setattr(auth_api, "_cortar_conexiones", observe_cut)
    response = client.post("/api/auth/mfa/root-reset", json={"target_user_id": target},
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert response.json()["target_user_id"] == target
    assert cuts == [(target, ((before_target[0][0] + 1, before_target[0][1] + 1),))]

    async def assert_result():
        assert await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (root,), True) == before_root
        assert await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (target,), True) == ((before_target[0][0] + 1, before_target[0][1] + 1),)
        assert await sql("SELECT state,secret_ciphertext,pending_secret_ciphertext FROM office_mfa_credential WHERE user_id=%s", (target,), True) == (("DISABLED", None, None),)
        state = await sql(
            "SELECT e.actor_user_id,e.subject_user_id,e.operation,e.purpose,e.principal_kind,"
            "e.proof_event_id,a.operation,a.result FROM office_mfa_security_event e "
            "JOIN office_authorization_event a ON a.event_id=e.authorization_event_id "
            "WHERE e.subject_user_id=%s AND e.operation='MFA_RESET'", (target,), True,
        )
        assert state == ((root, target, "MFA_RESET", "STEP_UP", "USER_SESSION", proof.event_id,
                          "ADMINISTER_OFFICE_IDENTITY_SECURITY", "ALLOWED"),)
    client.portal.call(assert_result)


def test_office_root_reset_http_denials_do_not_mutate_target(client, monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120",
        "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300",
        "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)

    async def arrange():
        await _migrate()
        root, root_start, _ = await _active_user()
        general, general_start, _ = await _active_user()
        collaborator, collaborator_start, _ = await _active_user()
        target, _, _ = await _active_user()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'OFFICE_SUPERUSER','ACTIVE'),(%s,'GENERAL_ADMIN','ACTIVE'),(%s,'COLLABORATOR','ACTIVE')", (root, general, collaborator))
        root_proof = await _step_up_proof(root, root_start.secret, b"a" * 16)
        general_proof = await _step_up_proof(general, general_start.secret, b"b" * 16)
        collaborator_proof = await _step_up_proof(collaborator, collaborator_start.secret, b"c" * 16)
        async def token(user, proof):
            versions = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user,), True))[0]
            return create_access_token(str(user), "1", "operator", versions[0], mfa_proof_event_id=proof.event_id, mfa_version=versions[1], mfa_at=int(proof.occurred_at.timestamp()))
        return {
            "root": root, "general": general, "collaborator": collaborator, "target": target,
            "root_token": await token(root, root_proof),
            "general_token": await token(general, general_proof),
            "collaborator_token": await token(collaborator, collaborator_proof),
            "wrong_subject_token": await token(root, collaborator_proof),
            "before": await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (target,), True),
        }

    fixture = client.portal.call(arrange)
    headers = lambda token: {"Authorization": f"Bearer {token}"}
    cases = (
        (fixture["general_token"], fixture["target"]),
        (fixture["collaborator_token"], fixture["target"]),
        (fixture["root_token"], fixture["root"]),
        (fixture["wrong_subject_token"], fixture["target"]),
    )
    for token, target in cases:
        response = client.post("/api/auth/mfa/root-reset", json={"target_user_id": target}, headers=headers(token))
        assert response.status_code == 403, response.text

    async def assert_unchanged():
        assert await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (fixture["target"],), True) == fixture["before"]
        assert await sql("SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s AND operation='MFA_RESET'", (fixture["target"],), True) == ((0,),)
    client.portal.call(assert_unchanged)


def test_root_reset_reloads_authorization_and_append_failure_rolls_everything_back(client, monkeypatch):
    async def scenario():
        await _migrate()
        root, root_start, _ = await _active_user()
        target, _, _ = await _active_user()
        proof = await _step_up_proof(root, root_start.secret, b"D" * 16)
        before = await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (target,), True)
        # A real ALLOWED audit record with the wrong operation is not a reset authority.
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            await cur.execute("INSERT INTO office_authorization_event (actor_user_id,event_kind,operation,result,reason_code,occurred_at) VALUES (%s,'AUTHORIZATION','ADMINISTER_OFFICE','ALLOWED','test',UTC_TIMESTAMP(6))", (root,))
            wrong = await __import__("virtual_office.mfa", fromlist=["reset_mfa_by_office_root_in_cursor"]).reset_mfa_by_office_root_in_cursor(
                cur, actor_user_id=root, target_user_id=target, proof_event_id=proof.event_id,
                authorization_event_id=cur.lastrowid, config=CONFIG, server_correlation_id=b"E" * 16,
            )
            assert isinstance(wrong, MFADenied)
        from virtual_office import mfa
        original = mfa._append_state
        async def fail_append(*_args, **_kwargs):
            raise RuntimeError("reset durable append unavailable")
        monkeypatch.setattr(mfa, "_append_state", fail_append)
        with pytest.raises(RuntimeError, match="reset durable append unavailable"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await cur.execute("INSERT INTO office_authorization_event (actor_user_id,event_kind,operation,result,reason_code,occurred_at) VALUES (%s,'AUTHORIZATION','ADMINISTER_OFFICE_IDENTITY_SECURITY','ALLOWED','test',UTC_TIMESTAMP(6))", (root,))
                await mfa.reset_mfa_by_office_root_in_cursor(
                    cur, actor_user_id=root, target_user_id=target, proof_event_id=proof.event_id,
                    authorization_event_id=cur.lastrowid, config=CONFIG, server_correlation_id=b"F" * 16,
                )
        monkeypatch.setattr(mfa, "_append_state", original)
        assert await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (target,), True) == before
        assert await sql("SELECT state FROM office_mfa_credential WHERE user_id=%s", (target,), True) == (("ACTIVE",),)
        assert await sql("SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s AND operation='MFA_RESET'", (target,), True) == ((0,),)
    client.portal.call(scenario)


def test_office_root_reset_denies_future_stale_and_revoked_root_without_target_effect(client, monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120", "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300", "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items(): monkeypatch.setenv(name, value)
    async def arrange():
        await _migrate()
        root, start, _ = await _active_user(); target, _, _ = await _active_user()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'OFFICE_SUPERUSER','ACTIVE')", (root,))
        proof = await _step_up_proof(root, start.secret, b"G" * 16)
        versions = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (root,), True))[0]
        await sql(
            "INSERT INTO office_mfa_security_event (event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
            "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 HOUR))", (root, root, *versions, b"H" * 16),
        )
        future = (await sql("SELECT event_id FROM office_mfa_security_event WHERE server_correlation_id=%s", (b"H" * 16,), True))[0][0]
        def token(event): return create_access_token(str(root), "1", "operator", versions[0], mfa_proof_event_id=event, mfa_version=versions[1], mfa_at=1)
        return root, target, proof.event_id, token(future), token(proof.event_id), await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (target,), True)
    root, target, proof_id, future_token, regular_token, before = client.portal.call(arrange)
    headers = lambda token: {"Authorization": f"Bearer {token}"}
    assert client.post("/api/auth/mfa/root-reset", json={"target_user_id": target}, headers=headers(future_token)).status_code == 403
    async def revoke(): await sql("UPDATE office_account_profile SET status='REVOKED' WHERE user_id=%s", (root,))
    client.portal.call(revoke)
    assert client.post("/api/auth/mfa/root-reset", json={"target_user_id": target}, headers=headers(regular_token)).status_code == 403
    async def stale():
        await sql("UPDATE office_account_profile SET status='ACTIVE' WHERE user_id=%s", (root,))
        await sql("UPDATE jax_users SET mfa_version=mfa_version+1 WHERE user_id=%s", (root,))
    client.portal.call(stale)
    assert client.post("/api/auth/mfa/root-reset", json={"target_user_id": target}, headers=headers(regular_token)).status_code == 403
    async def assert_unchanged():
        assert await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (target,), True) == before
        assert await sql("SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s AND operation='MFA_RESET'", (target,), True) == ((0,),)
    client.portal.call(assert_unchanged)


def test_office_root_reset_authorization_append_failure_has_no_effect_or_cut(client, monkeypatch):
    for name, value in {"OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120", "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3", "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300", "OFFICE_MFA_RECENCY_SECONDS": "300", "OFFICE_MFA_RECOVERY_CODE_COUNT": "3"}.items(): monkeypatch.setenv(name, value)
    async def arrange():
        await _migrate(); root, start, _ = await _active_user(); target, _, _ = await _active_user()
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'OFFICE_SUPERUSER','ACTIVE')", (root,))
        proof = await _step_up_proof(root, start.secret, b"I" * 16)
        version = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (root,), True))[0]
        return root, target, proof, version, await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (target,), True)
    root, target, proof, version, before = client.portal.call(arrange)
    import api.auth as auth_api
    cuts = []
    async def fail_audit(*_args, **_kwargs): raise RuntimeError("authorization append unavailable")
    async def cut(user_id): cuts.append(user_id)
    monkeypatch.setattr(auth_api, "record_authorization_event", fail_audit)
    monkeypatch.setattr(auth_api, "_cortar_conexiones", cut)
    token = create_access_token(str(root), "1", "operator", version[0], mfa_proof_event_id=proof.event_id, mfa_version=version[1], mfa_at=int(proof.occurred_at.timestamp()))
    with pytest.raises(RuntimeError, match="authorization append unavailable"):
        client.post("/api/auth/mfa/root-reset", json={"target_user_id": target}, headers={"Authorization": f"Bearer {token}"})
    async def assert_unchanged():
        assert await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (target,), True) == before
        assert await sql("SELECT COUNT(*) FROM office_authorization_event WHERE actor_user_id=%s AND operation='ADMINISTER_OFFICE_IDENTITY_SECURITY'", (root,), True) == ((0,),)
    client.portal.call(assert_unchanged)
    assert cuts == []


def test_schema_rejects_future_or_wrong_purpose_proof_rows(client):
    async def scenario():
        await _migrate()
        user_id, _, _ = await _active_user()
        # The frozen truth CHECK itself rejects impossible durable authority;
        # no service code may manufacture a future or wrong-purpose proof row.
        await sql(
            "INSERT INTO office_mfa_security_event "
            "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
            "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',1,1,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,DATE_ADD(NOW(6),INTERVAL 1 HOUR))",
            (user_id, user_id, b"q" * 16),
        )
        future_id = (await sql("SELECT event_id FROM office_mfa_security_event WHERE server_correlation_id=%s", (b"q" * 16,), True))[0][0]
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            future = await reload_current_self_mfa_proof_in_cursor(cur, actor_user_id=user_id, proof_event_id=future_id, config=CONFIG)
        assert isinstance(future, MFADenied) and future.code in {"proof_stale", "proof_invalid"}
        with pytest.raises(Exception):
            await sql(
            "INSERT INTO office_mfa_security_event "
            "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key) "
            "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','RECOVERY',1,1,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL)",
            (user_id, user_id, b"r" * 16),
            )
    client.portal.call(scenario)


def test_state_append_failure_rolls_back_versions_secret_and_consumption(client, monkeypatch):
    async def scenario():
        await _migrate()
        from virtual_office import mfa
        user_id, _ = await crear_usuario()
        start = await _first_enrollment(user_id)
        original = mfa._append_state

        async def fail_state(*_args, **_kwargs):
            raise RuntimeError("durable state unavailable")

        monkeypatch.setattr(mfa, "_append_state", fail_state)
        with pytest.raises(RuntimeError, match="durable state unavailable"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await confirm_enrollment_in_cursor(cur, enrollment_capability_token=start.enrollment_id, otp=_otp(start.secret), config=CONFIG, server_correlation_id=b"s" * 16)
        monkeypatch.setattr(mfa, "_append_state", original)
        assert await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True) == ((0, 0),)
        assert await sql("SELECT state,secret_ciphertext FROM office_mfa_credential WHERE user_id=%s", (user_id,), True) == (("PENDING", None),)
        assert await sql("SELECT state FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))", (start.enrollment_id,), True) == (("ISSUED",),)
        assert await sql("SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s", (user_id,), True) == ((0,),)
    client.portal.call(scenario)


def test_budget_lookup_explain_uses_frozen_index_and_records_plan(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        await _first_enrollment(user_id)
        exhausted_plan = await sql(
            "EXPLAIN SELECT 1 FROM office_mfa_challenge "
            "WHERE user_id=%s AND purpose='ENROLLMENT' AND expected_token_version=0 "
            "AND expected_mfa_version=0 AND state='EXHAUSTED' "
            "AND expires_at >= NOW(6) LIMIT 1",
            (user_id,), True,
        )
        issued_plan = await sql(
            "EXPLAIN SELECT attempt_count,max_attempts FROM office_mfa_challenge "
            "WHERE user_id=%s AND purpose='ENROLLMENT' AND expected_token_version=0 "
            "AND expected_mfa_version=0 AND state='ISSUED' AND expires_at >= NOW(6) LIMIT 1",
            (user_id,), True,
        )
        for plan in (exhausted_plan, issued_plan):
            assert plan and plan[0][5] == "idx_office_mfa_challenge_user"
            assert "filesort" not in str(plan[0][-1]).lower()
            assert "temporary" not in str(plan[0][-1]).lower()
            print("MFA_BUDGET_EXPLAIN", plan[0])
    client.portal.call(scenario)


def test_replayed_otp_consumes_budget_without_opening_a_new_challenge_window(client):
    async def scenario():
        await _migrate()
        user_id, start, _ = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            first = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            proof = await verify_and_consume_totp_in_cursor(
                cur, challenge_token=first.token, otp=_previous_otp(start.secret),
                server_correlation_id=b"u" * 16,
            )
        assert not isinstance(proof, MFADenied)

        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            retry = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        for correlation in (b"v" * 16, b"w" * 16):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                denied = await verify_and_consume_totp_in_cursor(
                    cur, challenge_token=retry.token, otp=_previous_otp(start.secret),
                    server_correlation_id=correlation,
                )
            assert isinstance(denied, MFADenied)

        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            blocked = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert isinstance(blocked, MFADenied)
        assert blocked.code == "challenge_live"
        assert await sql(
            "SELECT state,attempt_count FROM office_mfa_challenge "
            "WHERE token_hash=UNHEX(SHA2(%s,256))",
            (retry.token,), True,
        ) == (("ISSUED", 2),)
    client.portal.call(scenario)


def test_first_enrollment_rejects_recovery_lineage_before_and_after_pending_recovery(client):
    async def scenario():
        await _migrate()
        user_id, _, enrolled = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            recovery = await consume_recovery_code_in_cursor(
                cur, recovery_code=enrolled.recovery_codes[0], config=CONFIG,
                server_correlation_id=b"x" * 16,
            )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            denied_before = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert isinstance(denied_before, MFADenied)
        assert denied_before.code == "recovery_required"

        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            recovery_start = await start_recovery_enrollment_in_cursor(
                cur, recovery_capability_token=recovery.token, config=CONFIG,
            )
        assert not isinstance(recovery_start, MFADenied)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            denied_pending = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert isinstance(denied_pending, MFADenied)
        assert denied_pending.code == "recovery_required"
    client.portal.call(scenario)


def test_reenrollment_retains_active_factor_until_atomic_confirmation(client):
    async def scenario():
        await _migrate()
        user_id, start, _ = await _active_user()
        proof = await _step_up_proof(user_id, start.secret, b"y" * 16)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            reenrollment = await start_reenrollment_after_password_in_cursor(
                cur, actor_user_id=user_id, proof_event_id=proof.event_id, config=CONFIG,
            )
        assert not isinstance(reenrollment, MFADenied)
        assert await sql(
            "SELECT state,secret_ciphertext IS NOT NULL,pending_secret_ciphertext IS NOT NULL "
            "FROM office_mfa_credential WHERE user_id=%s", (user_id,), True,
        ) == (("ACTIVE", 1, 1),)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            second = await start_reenrollment_after_password_in_cursor(
                cur, actor_user_id=user_id, proof_event_id=proof.event_id, config=CONFIG,
            )
        assert isinstance(second, MFADenied)
        assert second.code == "challenge_live"
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            confirmed = await confirm_enrollment_in_cursor(
                cur, enrollment_capability_token=reenrollment.enrollment_id,
                otp=_otp(reenrollment.secret), config=CONFIG,
                server_correlation_id=b"1" * 16,
            )
        assert confirmed.mfa_version == 2
        assert await sql(
            "SELECT state,pending_secret_ciphertext FROM office_mfa_credential WHERE user_id=%s",
            (user_id,), True,
        ) == (("ACTIVE", None),)
    client.portal.call(scenario)


def test_corrupt_mfa_ciphertext_rolls_back_instead_of_spending_otp_budget(client):
    async def scenario():
        await _migrate()
        user_id, _, _ = await _active_user()
        await sql(
            "UPDATE office_mfa_credential SET secret_ciphertext='not-a-fernet-token' WHERE user_id=%s",
            (user_id,),
        )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            challenge = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        from virtual_office.mfa import MFAError
        with pytest.raises(MFAError, match="mfa_secret_unavailable"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await verify_and_consume_totp_in_cursor(
                    cur, challenge_token=challenge.token, otp="000000",
                    server_correlation_id=b"2" * 16,
                )
        assert await sql(
            "SELECT state,attempt_count FROM office_mfa_challenge "
            "WHERE token_hash=UNHEX(SHA2(%s,256))", (challenge.token,), True,
        ) == (("ISSUED", 0),)
    client.portal.call(scenario)


@pytest.mark.parametrize("key", [None, "not-a-fernet-key"])
def test_missing_or_malformed_fernet_key_rolls_back_mfa_attempt(client, monkeypatch, key):
    async def scenario():
        await _migrate()
        user_id, _, _ = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            challenge = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        from virtual_office.mfa import MFAError
        if key is None:
            monkeypatch.delenv("FERNET_KEY")
        else:
            monkeypatch.setenv("FERNET_KEY", key)
        with pytest.raises(MFAError, match="mfa_secret_unavailable"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await verify_and_consume_totp_in_cursor(
                    cur, challenge_token=challenge.token, otp="000000",
                    server_correlation_id=b"3" * 16,
                )
        assert await sql(
            "SELECT attempt_count FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))",
            (challenge.token,), True,
        ) == ((0,),)

    client.portal.call(scenario)


def test_expired_password_pending_enrollment_restarts_but_recovery_pending_never_does(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        first = await _first_enrollment(user_id)
        await sql(
            "UPDATE office_mfa_challenge SET expires_at=DATE_SUB(NOW(6),INTERVAL 1 SECOND) "
            "WHERE token_hash=UNHEX(SHA2(%s,256))", (first.enrollment_id,),
        )
        await sql(
            "UPDATE office_mfa_credential SET pending_expires_at=DATE_SUB(NOW(6),INTERVAL 1 SECOND) "
            "WHERE user_id=%s", (user_id,),
        )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            restarted = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert not isinstance(restarted, MFADenied)
        assert restarted.enrollment_id != first.enrollment_id

        recovery_user, _, enrolled = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            recovery = await consume_recovery_code_in_cursor(
                cur, recovery_code=enrolled.recovery_codes[0], config=CONFIG,
                server_correlation_id=b"4" * 16,
            )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            pending = await start_recovery_enrollment_in_cursor(
                cur, recovery_capability_token=recovery.token, config=CONFIG,
            )
        await sql(
            "UPDATE office_mfa_credential SET pending_expires_at=DATE_SUB(NOW(6),INTERVAL 1 SECOND) "
            "WHERE user_id=%s", (recovery_user,),
        )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            denied = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=recovery_user, config=CONFIG,
            )
        assert pending.enrollment_id
        assert isinstance(denied, MFADenied) and denied.code == "recovery_required"
    client.portal.call(scenario)


@pytest.mark.parametrize("terminal_state", ("EXHAUSTED", "INVALIDATED"))
def test_expired_historical_terminal_anchor_keeps_its_terminal_cause(client, terminal_state):
    """Replacing an expired first-enrollment capability cannot rewrite its history."""
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        first = await _first_enrollment(user_id)

        if terminal_state == "EXHAUSTED":
            for correlation in (b"q" * 16, b"r" * 16, b"s" * 16):
                async with transaccion(AISLAMIENTO_ADMIN) as cur:
                    denied = await confirm_enrollment_in_cursor(
                        cur, enrollment_capability_token=first.enrollment_id, otp="000000",
                        config=CONFIG, server_correlation_id=correlation,
                    )
            assert isinstance(denied, MFADenied) and denied.code == "challenge_exhausted"
        else:
            # A token-only session cut makes the real confirmation CAS reject
            # the capability and records INVALIDATED on the exact anchor.
            await sql("UPDATE jax_users SET token_version=token_version+1 WHERE user_id=%s", (user_id,))
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                denied = await confirm_enrollment_in_cursor(
                    cur, enrollment_capability_token=first.enrollment_id, otp=_otp(first.secret),
                    config=CONFIG, server_correlation_id=b"t" * 16,
                )
            assert isinstance(denied, MFADenied) and denied.code == "enrollment_version"

        before = await sql(
            "SELECT state,attempt_count,used_at FROM office_mfa_challenge "
            "WHERE token_hash=UNHEX(SHA2(%s,256))", (first.enrollment_id,), True,
        )
        assert before[0][0] == terminal_state and before[0][2] is not None
        await sql(
            "UPDATE office_mfa_challenge SET expires_at=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 SECOND) "
            "WHERE token_hash=UNHEX(SHA2(%s,256))", (first.enrollment_id,),
        )
        await sql(
            "UPDATE office_mfa_credential SET pending_expires_at=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 SECOND) "
            "WHERE user_id=%s", (user_id,),
        )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            restarted = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert not isinstance(restarted, MFADenied)
        assert restarted.enrollment_id != first.enrollment_id
        assert await sql(
            "SELECT state,attempt_count,used_at FROM office_mfa_challenge "
            "WHERE token_hash=UNHEX(SHA2(%s,256))", (first.enrollment_id,), True,
        ) == before

    client.portal.call(scenario)


def test_recovery_enrollment_insert_denial_leaves_capability_and_credential_unchanged(client, monkeypatch):
    async def scenario():
        await _migrate()
        user_id, _, enrolled = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            recovery = await consume_recovery_code_in_cursor(
                cur, recovery_code=enrolled.recovery_codes[0], config=CONFIG,
                server_correlation_id=b"5" * 16,
            )
        from virtual_office import mfa
        original = mfa._insert_challenge

        async def deny_insert(*_args, **_kwargs):
            return MFADenied("challenge_exhausted", terminal=True)

        monkeypatch.setattr(mfa, "_insert_challenge", deny_insert)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            denied = await start_recovery_enrollment_in_cursor(
                cur, recovery_capability_token=recovery.token, config=CONFIG,
            )
        monkeypatch.setattr(mfa, "_insert_challenge", original)
        assert isinstance(denied, MFADenied)
        assert await sql(
            "SELECT state FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))",
            (recovery.token,), True,
        ) == (("ISSUED",),)
        assert await sql(
            "SELECT state,pending_secret_ciphertext FROM office_mfa_credential WHERE user_id=%s",
            (user_id,), True,
        ) == (("RECOVERY_REQUIRED", None),)
    client.portal.call(scenario)


def test_login_and_step_up_challenges_are_distinct_and_share_totp_replay_protection(client):
    async def scenario():
        await _migrate()
        user_id, start, _ = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            login = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            step_up = await issue_step_up_challenge_in_cursor(
                cur, actor_user_id=user_id, config=CONFIG,
            )
        assert login.token != step_up.token
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            login_result = await verify_and_consume_totp_in_cursor(
                cur, challenge_token=login.token, otp=_previous_otp(start.secret),
                server_correlation_id=b"6" * 16,
            )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            step_up_result = await verify_and_consume_totp_in_cursor(
                cur, challenge_token=step_up.token, otp=_previous_otp(start.secret),
                server_correlation_id=b"7" * 16,
            )
        assert not isinstance(login_result, MFADenied)
        assert isinstance(step_up_result, MFADenied)
        assert await sql(
            "SELECT state,attempt_count FROM office_mfa_challenge "
            "WHERE token_hash=UNHEX(SHA2(%s,256))", (step_up.token,), True,
        ) == (("INVALIDATED", 0),)
    client.portal.call(scenario)


def test_enrollment_rows_and_receipt_share_one_db_expiry(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        start = await _first_enrollment(user_id)
        rows = await sql(
            "SELECT c.expires_at,cred.pending_expires_at FROM office_mfa_challenge c "
            "JOIN office_mfa_credential cred ON cred.user_id=c.user_id "
            "WHERE c.token_hash=UNHEX(SHA2(%s,256))", (start.enrollment_id,), True,
        )
        assert rows == ((start.expires_at.replace(tzinfo=None), start.expires_at.replace(tzinfo=None)),)
    client.portal.call(scenario)


def test_recovery_pending_origin_survives_token_only_session_revocation(client):
    async def scenario():
        await _migrate()
        user_id, _, enrolled = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            recovery = await consume_recovery_code_in_cursor(
                cur, recovery_code=enrolled.recovery_codes[0], config=CONFIG,
                server_correlation_id=b"8" * 16,
            )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            pending = await start_recovery_enrollment_in_cursor(
                cur, recovery_capability_token=recovery.token, config=CONFIG,
            )
        original = await sql(
            "SELECT pending_enrollment_id,pending_secret_ciphertext FROM office_mfa_credential "
            "WHERE user_id=%s", (user_id,), True,
        )
        enrollment_count = await sql(
            "SELECT COUNT(*) FROM office_mfa_challenge WHERE user_id=%s AND purpose='ENROLLMENT'",
            (user_id,), True,
        )
        await sql("UPDATE jax_users SET token_version=token_version+1 WHERE user_id=%s", (user_id,))
        await sql(
            "UPDATE office_mfa_challenge SET state='INVALIDATED',used_at=NOW(6),"
            "expires_at=DATE_SUB(NOW(6),INTERVAL 1 SECOND) "
            "WHERE token_hash=UNHEX(SHA2(%s,256))", (pending.enrollment_id,),
        )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            denied = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert isinstance(denied, MFADenied) and denied.code == "recovery_required"
        assert await sql(
            "SELECT pending_enrollment_id,pending_secret_ciphertext FROM office_mfa_credential "
            "WHERE user_id=%s", (user_id,), True,
        ) == original
        assert await sql(
            "SELECT COUNT(*) FROM office_mfa_challenge WHERE user_id=%s AND purpose='ENROLLMENT'",
            (user_id,), True,
        ) == enrollment_count
    client.portal.call(scenario)


def test_first_pending_origin_anchor_fails_closed_when_its_subject_or_shape_is_wrong(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        first = await _first_enrollment(user_id)
        other, _ = await crear_usuario()
        await sql(
            "UPDATE office_mfa_challenge SET user_id=%s WHERE token_hash=UNHEX(SHA2(%s,256))",
            (other, first.enrollment_id),
        )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            denied = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert isinstance(denied, MFADenied)
        assert denied.code == "pending_origin_invalid"
    client.portal.call(scenario)


@pytest.mark.parametrize(
    ("column", "value"),
    (("purpose", "LOGIN"), ("expected_mfa_version", 999)),
)
def test_first_pending_origin_anchor_wrong_purpose_or_mfa_fails_closed(client, column, value):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        first = await _first_enrollment(user_id)
        await sql(
            f"UPDATE office_mfa_challenge SET {column}=%s WHERE token_hash=UNHEX(SHA2(%s,256))",
            (value, first.enrollment_id),
        )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            denied = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert isinstance(denied, MFADenied) and denied.code == "pending_origin_invalid"
    client.portal.call(scenario)


def test_first_origin_pending_restart_survives_token_only_revocation_after_its_window(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        first = await _first_enrollment(user_id)
        await sql("UPDATE jax_users SET token_version=token_version+1 WHERE user_id=%s", (user_id,))
        await sql(
            "UPDATE office_mfa_challenge SET state='EXPIRED',expires_at=DATE_SUB(NOW(6),INTERVAL 1 SECOND) "
            "WHERE token_hash=UNHEX(SHA2(%s,256))", (first.enrollment_id,),
        )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            restarted = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert not isinstance(restarted, MFADenied)
        assert restarted.enrollment_id != first.enrollment_id
    client.portal.call(scenario)


def test_pending_origin_queries_use_primary_key_and_proof_index_without_sorting(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        first = await _first_enrollment(user_id)
        anchor = await sql(
            "EXPLAIN SELECT user_id,purpose,expected_token_version,expected_mfa_version,state,expires_at,attempt_count,max_attempts "
            "FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256)) FOR UPDATE",
            (first.enrollment_id,), True,
        )
        lineage = await sql(
            "EXPLAIN SELECT event_id FROM office_mfa_security_event FORCE INDEX (idx_office_mfa_security_event_proof) "
            "WHERE subject_user_id=%s AND purpose='RECOVERY' AND token_version=0 AND mfa_version=0 "
            "AND event_kind='MFA_STATE_CHANGE' AND operation='RECOVERY_CONSUME' "
            "AND principal_kind='RECOVERY_CAPABILITY' AND result='RECORDED' LIMIT 1",
            (user_id,), True,
        )
        assert anchor[0][5] == "PRIMARY"
        assert lineage[0][5] == "idx_office_mfa_security_event_proof"
        for plan in (anchor, lineage):
            extra = str(plan[0][-1]).lower()
            assert "filesort" not in extra and "temporary" not in extra
    client.portal.call(scenario)


@pytest.mark.parametrize("cas_target", ("capability", "credential"))
def test_recovery_enrollment_cas_loss_after_real_update_rolls_back_everything(client, cas_target):
    """The post-insert CAS guards are rollback boundaries, not advisory checks."""
    async def scenario():
        await _migrate()
        user_id, _, enrolled = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            recovery = await consume_recovery_code_in_cursor(
                cur, recovery_code=enrolled.recovery_codes[0], config=CONFIG,
                server_correlation_id=b"C" * 16,
            )
        before = await sql(
            "SELECT state,secret_ciphertext,pending_secret_ciphertext,pending_enrollment_id,"
            "pending_expires_at FROM office_mfa_credential WHERE user_id=%s", (user_id,), True,
        )
        event_count = await sql(
            "SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s", (user_id,), True,
        )
        enrollment_count = await sql(
            "SELECT COUNT(*) FROM office_mfa_challenge WHERE user_id=%s AND purpose='ENROLLMENT'", (user_id,), True,
        )

        class LostRowcountAfterUpdate:
            def __init__(self, delegate): self._delegate, self._lost = delegate, False
            async def execute(self, statement, *args):
                result = await self._delegate.execute(statement, *args)
                capability = "UPDATE office_mfa_challenge SET state='CONSUMED'" in statement
                credential = "UPDATE office_mfa_credential SET state='PENDING'" in statement
                if (cas_target == "capability" and capability) or (cas_target == "credential" and credential):
                    self._lost = True
                return result
            @property
            def rowcount(self): return 0 if self._lost else self._delegate.rowcount
            def __getattr__(self, name): return getattr(self._delegate, name)

        with pytest.raises(RuntimeError, match=f"recovery {cas_target} CAS lost"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await start_recovery_enrollment_in_cursor(
                    LostRowcountAfterUpdate(cur), recovery_capability_token=recovery.token, config=CONFIG,
                )
        assert await sql(
            "SELECT state,secret_ciphertext,pending_secret_ciphertext,pending_enrollment_id,"
            "pending_expires_at FROM office_mfa_credential WHERE user_id=%s", (user_id,), True,
        ) == before == (("RECOVERY_REQUIRED", None, None, None, None),)
        assert await sql(
            "SELECT state FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))",
            (recovery.token,), True,
        ) == (("ISSUED",),)
        assert await sql(
            "SELECT COUNT(*) FROM office_mfa_challenge WHERE user_id=%s AND purpose='ENROLLMENT'", (user_id,), True,
        ) == enrollment_count
        assert await sql(
            "SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s", (user_id,), True,
        ) == event_count
    client.portal.call(scenario)


def test_password_first_and_recovery_start_race_leaves_only_recovery_authorized(client):
    async def scenario():
        await _migrate()
        user_id, _, enrolled = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            recovery = await consume_recovery_code_in_cursor(
                cur, recovery_code=enrolled.recovery_codes[0], config=CONFIG,
                server_correlation_id=b"R" * 16,
            )
        async def password_first():
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await start_first_enrollment_after_password_in_cursor(
                    cur, preauthenticated_user_id=user_id, config=CONFIG,
                )
        async def recovery_start():
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await start_recovery_enrollment_in_cursor(
                    cur, recovery_capability_token=recovery.token, config=CONFIG,
                )
        password, replacement = await asyncio.gather(password_first(), recovery_start())
        assert isinstance(password, MFADenied) and password.code == "recovery_required"
        assert not isinstance(replacement, MFADenied)
        assert await sql(
            "SELECT state FROM office_mfa_credential WHERE user_id=%s", (user_id,), True,
        ) == (("PENDING",),)
    client.portal.call(scenario)


def test_reenrollment_old_factor_authenticates_pending_and_failed_confirm_rolls_back(client, monkeypatch):
    async def scenario():
        await _migrate()
        from virtual_office import mfa
        user_id, old, enrolled = await _active_user()
        proof = await _step_up_proof(user_id, old.secret, b"O" * 16)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            pending = await start_reenrollment_after_password_in_cursor(
                cur, actor_user_id=user_id, proof_event_id=proof.event_id, config=CONFIG,
            )
        # A pending replacement does not withdraw the old active factor.
        await sql("DELETE FROM office_mfa_totp_use WHERE user_id=%s", (user_id,))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            login = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            old_proof = await verify_and_consume_totp_in_cursor(
                cur, challenge_token=login.token, otp=_otp(old.secret), server_correlation_id=b"P" * 16,
            )
        assert not isinstance(old_proof, MFADenied)
        # A successful LOGIN deliberately cuts every old-version capability,
        # including the earlier reenrollment token.  Re-create a pending
        # replacement from the post-cut durable LOGIN proof before exercising
        # the confirmation rollback below.
        assert await sql("SELECT state FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))", (pending.enrollment_id,), True) == (("INVALIDATED",),)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            pending = await start_reenrollment_after_password_in_cursor(
                cur, actor_user_id=user_id, proof_event_id=old_proof.event_id, config=CONFIG,
            )
        before = await sql(
            "SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True,
        ), await sql(
            "SELECT state,secret_ciphertext,pending_secret_ciphertext,pending_enrollment_id FROM office_mfa_credential WHERE user_id=%s", (user_id,), True,
        ), await sql("SELECT COUNT(*) FROM office_mfa_recovery_code WHERE user_id=%s", (user_id,), True), await sql(
            "SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s", (user_id,), True,
        )
        original = mfa._append_state
        async def fail_after_insert(*_args, **_kwargs): raise RuntimeError("state append injected")
        monkeypatch.setattr(mfa, "_append_state", fail_after_insert)
        with pytest.raises(RuntimeError, match="state append injected"):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await confirm_enrollment_in_cursor(
                    cur, enrollment_capability_token=pending.enrollment_id, otp=_otp(pending.secret),
                    config=CONFIG, server_correlation_id=b"Q" * 16,
                )
        monkeypatch.setattr(mfa, "_append_state", original)
        after = await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True), await sql(
            "SELECT state,secret_ciphertext,pending_secret_ciphertext,pending_enrollment_id FROM office_mfa_credential WHERE user_id=%s", (user_id,), True,
        ), await sql("SELECT COUNT(*) FROM office_mfa_recovery_code WHERE user_id=%s", (user_id,), True), await sql(
            "SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s", (user_id,), True,
        )
        assert after == before
        assert await sql("SELECT state FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))", (pending.enrollment_id,), True) == (("ISSUED",),)
    client.portal.call(scenario)


def test_replayed_otp_exhausts_exact_budget_until_its_original_window_expires(client):
    async def scenario():
        await _migrate()
        user_id, active, _ = await _active_user()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            successful = await issue_login_challenge_after_password_in_cursor(cur, preauthenticated_user_id=user_id, config=CONFIG)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert not isinstance(await verify_and_consume_totp_in_cursor(cur, challenge_token=successful.token, otp=_previous_otp(active.secret), server_correlation_id=b"S" * 16), MFADenied)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            retry = await issue_login_challenge_after_password_in_cursor(cur, preauthenticated_user_id=user_id, config=CONFIG)
        for cid in (b"T" * 16, b"U" * 16):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                denial = await verify_and_consume_totp_in_cursor(cur, challenge_token=retry.token, otp="000000", server_correlation_id=cid)
            assert isinstance(denial, MFADenied) and denial.code == "totp_invalid"
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            exhausted = await verify_and_consume_totp_in_cursor(cur, challenge_token=retry.token, otp=_previous_otp(active.secret), server_correlation_id=b"V" * 16)
        assert isinstance(exhausted, MFADenied) and exhausted.code == "challenge_exhausted"
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            blocked = await issue_login_challenge_after_password_in_cursor(cur, preauthenticated_user_id=user_id, config=CONFIG)
        assert isinstance(blocked, MFADenied) and blocked.code == "challenge_exhausted"
        await sql("UPDATE office_mfa_challenge SET expires_at=DATE_SUB(NOW(6),INTERVAL 1 SECOND) WHERE token_hash=UNHEX(SHA2(%s,256))", (retry.token,))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            reissued = await issue_login_challenge_after_password_in_cursor(cur, preauthenticated_user_id=user_id, config=CONFIG)
        assert not isinstance(reissued, MFADenied) and reissued.token != retry.token
    client.portal.call(scenario)


def test_first_origin_version_invalidation_keeps_its_partial_attempt_window(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        first = await _first_enrollment(user_id)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            invalid = await confirm_enrollment_in_cursor(
                cur, enrollment_capability_token=first.enrollment_id, otp="000000",
                config=CONFIG, server_correlation_id=b"W" * 16,
            )
        assert isinstance(invalid, MFADenied) and invalid.code == "totp_invalid"
        # This is the production session-cut shape: it changes token_version
        # only.  The next genuine capability use must invalidate its own row.
        await sql("UPDATE jax_users SET token_version=token_version+1 WHERE user_id=%s", (user_id,))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            versioned = await confirm_enrollment_in_cursor(
                cur, enrollment_capability_token=first.enrollment_id, otp=_otp(first.secret),
                config=CONFIG, server_correlation_id=b"X" * 16,
            )
        assert isinstance(versioned, MFADenied) and versioned.code == "enrollment_version"
        assert await sql(
            "SELECT state,attempt_count FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))",
            (first.enrollment_id,), True,
        ) == (("INVALIDATED", 1),)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            restart = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert isinstance(restart, MFADenied)
        assert restart.code == "challenge_live" and restart.attempts_remaining == 2
    client.portal.call(scenario)


def test_replacing_expired_historical_first_origin_expires_its_exact_hash(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        first = await _first_enrollment(user_id)
        # Leave the old row ISSUED but naturally past its own window.  A
        # session cut makes its versions historical, so current-version SQL
        # cannot identify the row that must be marked EXPIRED.
        await sql(
            "UPDATE office_mfa_challenge SET expires_at=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 SECOND) "
            "WHERE token_hash=UNHEX(SHA2(%s,256))", (first.enrollment_id,),
        )
        await sql("UPDATE jax_users SET token_version=token_version+1 WHERE user_id=%s", (user_id,))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            replacement = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        assert not isinstance(replacement, MFADenied)
        assert replacement.enrollment_id != first.enrollment_id
        assert await sql(
            "SELECT state FROM office_mfa_challenge WHERE token_hash=UNHEX(SHA2(%s,256))",
            (first.enrollment_id,), True,
        ) == (("EXPIRED",),)
    client.portal.call(scenario)


@pytest.mark.parametrize("zone", ("-06:00", "+05:30"))
def test_mfa_uses_utc_under_non_utc_session_timezones(client, zone):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        async with _session_timezone_transaction(zone) as cur:
            await cur.execute("SELECT TIMESTAMPDIFF(SECOND,NOW(6),UTC_TIMESTAMP(6))")
            assert (await cur.fetchone())[0] == {"-06:00": 21600, "+05:30": -19800}[zone]
            await cur.execute("SELECT UTC_TIMESTAMP(6)")
            utc_before = (await cur.fetchone())[0]
            start = await start_first_enrollment_after_password_in_cursor(
                cur, preauthenticated_user_id=user_id, config=CONFIG,
            )
        pending_start = await sql(
            "SELECT pending_expires_at FROM office_mfa_credential WHERE user_id=%s", (user_id,), True,
        )
        assert pending_start == ((start.expires_at.replace(tzinfo=None),),)
        async with _session_timezone_transaction(zone) as cur:
            confirmed = await confirm_enrollment_in_cursor(
                cur, enrollment_capability_token=start.enrollment_id, otp=pyotp.TOTP(start.secret).now(),
                config=CONFIG, server_correlation_id=b"Y" * 16,
            )
        # Enrollment consumes the current counter at MFA version 1.  A separate
        # active factor proves that a real *current* pyotp.now() is accepted
        # under the hostile session zone, rather than relying on the grace slot.
        login_user, login_start, _ = await _active_user()
        await sql("DELETE FROM office_mfa_totp_use WHERE user_id=%s", (login_user,))
        async with _session_timezone_transaction(zone) as cur:
            login = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=login_user, config=CONFIG,
            )
        async with _session_timezone_transaction(zone) as cur:
            proof = await verify_and_consume_totp_in_cursor(
                cur, challenge_token=login.token, otp=pyotp.TOTP(login_start.secret).now(),
                server_correlation_id=b"Z" * 16,
            )
        assert not isinstance(proof, MFADenied)
        assert proof.occurred_at.tzinfo is dt.timezone.utc
        stored_proof = await sql(
            "SELECT occurred_at FROM office_mfa_security_event WHERE event_id=%s", (proof.event_id,), True,
        )
        assert stored_proof == ((proof.occurred_at.replace(tzinfo=None),),)
        assert confirmed.mfa_version == 1
        async with _session_timezone_transaction(zone) as cur:
            current = await reload_current_self_mfa_proof_in_cursor(
                cur, actor_user_id=login_user, proof_event_id=proof.event_id, config=CONFIG,
            )
        assert not isinstance(current, MFADenied) and current.occurred_at.tzinfo is dt.timezone.utc
        before_replay = await sql(
            "SELECT COUNT(*) FROM office_mfa_totp_use WHERE user_id=%s", (login_user,), True,
        ), await sql(
            "SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s", (login_user,), True,
        )
        async with _session_timezone_transaction(zone) as cur:
            replay = await verify_and_consume_totp_in_cursor(
                cur, challenge_token=login.token, otp=pyotp.TOTP(login_start.secret).now(),
                server_correlation_id=b"0" * 16,
            )
        assert isinstance(replay, MFADenied) and replay.code == "challenge_unavailable"
        assert before_replay == (
            await sql("SELECT COUNT(*) FROM office_mfa_totp_use WHERE user_id=%s", (login_user,), True),
            await sql("SELECT COUNT(*) FROM office_mfa_security_event WHERE subject_user_id=%s", (login_user,), True),
        )
        async with _session_timezone_transaction(zone) as cur:
            for stamp, cid in (("DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 301 SECOND)", b"1" * 16), ("DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 SECOND)", b"2" * 16)):
                await cur.execute(
                    "INSERT INTO office_mfa_security_event "
                    "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
                    f"VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',1,1,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,{stamp})",
                    (login_user, login_user, cid),
                )
            await cur.execute("SELECT event_id FROM office_mfa_security_event WHERE subject_user_id=%s AND server_correlation_id IN (%s,%s) ORDER BY event_id", (login_user, b"1" * 16, b"2" * 16))
            stale_id, future_id = (int(row[0]) for row in await cur.fetchall())
        async with _session_timezone_transaction(zone) as cur:
            stale = await reload_current_self_mfa_proof_in_cursor(cur, actor_user_id=login_user, proof_event_id=stale_id, config=CONFIG)
        async with _session_timezone_transaction(zone) as cur:
            future = await reload_current_self_mfa_proof_in_cursor(cur, actor_user_id=login_user, proof_event_id=future_id, config=CONFIG)
        assert isinstance(stale, MFADenied) and stale.code == "proof_stale"
        assert isinstance(future, MFADenied) and future.code == "proof_stale"
        async with _session_timezone_transaction(zone) as cur:
            recovery = await consume_recovery_code_in_cursor(
                cur, recovery_code=confirmed.recovery_codes[0], config=CONFIG,
                server_correlation_id=b"8" * 16,
            )
        async with _session_timezone_transaction(zone) as cur:
            replacement = await start_recovery_enrollment_in_cursor(
                cur, recovery_capability_token=recovery.token, config=CONFIG,
            )
        async with _session_timezone_transaction(zone) as cur:
            replacement_confirmed = await confirm_enrollment_in_cursor(
                cur, enrollment_capability_token=replacement.enrollment_id,
                otp=pyotp.TOTP(replacement.secret).now(), config=CONFIG,
                server_correlation_id=b"9" * 16,
            )
            await cur.execute("SELECT UTC_TIMESTAMP(6)")
            utc_after = (await cur.fetchone())[0]
        assert replacement_confirmed.mfa_version == 3
        rows = await sql(
            "SELECT created_at,updated_at,pending_expires_at,confirmed_at FROM office_mfa_credential WHERE user_id=%s",
            (user_id,), True,
        )
        challenge_times = await sql(
            "SELECT purpose,created_at,expires_at,used_at FROM office_mfa_challenge WHERE user_id=%s", (user_id,), True,
        )
        event_times = await sql(
            "SELECT occurred_at FROM office_mfa_security_event WHERE subject_user_id=%s", (user_id,), True,
        )
        counter_times = await sql(
            "SELECT used_at FROM office_mfa_totp_use WHERE user_id=%s", (user_id,), True,
        )
        recovery_times = await sql(
            "SELECT created_at,used_at FROM office_mfa_recovery_code WHERE user_id=%s", (user_id,), True,
        )
        assert all(
            abs(((row[2] - row[1]).total_seconds()) - (CONFIG.enrollment_lifetime_seconds if row[0] == "ENROLLMENT" else CONFIG.challenge_lifetime_seconds)) < 1
            for row in challenge_times
        )
        values = [
            *[value for row in rows for value in (row[0], row[1], row[3]) if value is not None],
            *[value for row in challenge_times for value in (row[1], row[3]) if value is not None],
            *[row[0] for row in event_times],
            *[row[0] for row in counter_times if row[0] is not None],
            *[value for row in recovery_times for value in row if value is not None],
        ]
        assert values and all(utc_before <= value <= utc_after for value in values)
    client.portal.call(scenario)


@pytest.mark.parametrize("zone", ("-06:00", "+05:30"))
def test_mfa_terminal_timestamp_paths_are_utc_under_non_utc_sessions(client, zone):
    async def scenario():
        await _migrate()
        exhausted_user, exhausted_start, _ = await _active_user()
        async with _session_timezone_transaction(zone) as cur:
            exhausted = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=exhausted_user, config=CONFIG,
            )
        for cid in (b"3" * 16, b"4" * 16, b"5" * 16):
            async with _session_timezone_transaction(zone) as cur:
                denial = await verify_and_consume_totp_in_cursor(
                    cur, challenge_token=exhausted.token, otp="000000", server_correlation_id=cid,
                )
        assert isinstance(denial, MFADenied) and denial.code == "challenge_exhausted" and denial.terminal
        expired_user, expired_start, _ = await _active_user()
        async with _session_timezone_transaction(zone) as cur:
            expiring = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=expired_user, config=CONFIG,
            )
            await cur.execute("UPDATE office_mfa_challenge SET expires_at=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 SECOND) WHERE token_hash=UNHEX(SHA2(%s,256))", (expiring.token,))
        async with _session_timezone_transaction(zone) as cur:
            expired = await verify_and_consume_totp_in_cursor(
                cur, challenge_token=expiring.token, otp=_previous_otp(expired_start.secret), server_correlation_id=b"6" * 16,
            )
        assert isinstance(expired, MFADenied) and expired.code == "challenge_expired"
        versioned_user, versioned_start, _ = await _active_user()
        async with _session_timezone_transaction(zone) as cur:
            versioned = await issue_login_challenge_after_password_in_cursor(
                cur, preauthenticated_user_id=versioned_user, config=CONFIG,
            )
            await cur.execute("UPDATE jax_users SET token_version=token_version+1 WHERE user_id=%s", (versioned_user,))
        async with _session_timezone_transaction(zone) as cur:
            mismatch = await verify_and_consume_totp_in_cursor(
                cur, challenge_token=versioned.token, otp=_previous_otp(versioned_start.secret), server_correlation_id=b"7" * 16,
            )
            await cur.execute("SELECT UTC_TIMESTAMP(6)")
            db_now = (await cur.fetchone())[0]
        assert isinstance(mismatch, MFADenied) and mismatch.code == "challenge_version"
        terminal_rows = await sql(
            "SELECT state,used_at FROM office_mfa_challenge WHERE token_hash IN (UNHEX(SHA2(%s,256)),UNHEX(SHA2(%s,256)),UNHEX(SHA2(%s,256))) ORDER BY state",
            (exhausted.token, expiring.token, versioned.token), True,
        )
        assert {row[0] for row in terminal_rows} == {"EXHAUSTED", "EXPIRED", "INVALIDATED"}
        assert all(row[1] is not None and abs((db_now - row[1]).total_seconds()) < 5 for row in terminal_rows)
    client.portal.call(scenario)


def test_mfa_source_has_no_session_local_timestamp_expression():
    from pathlib import Path
    source = Path(__file__).parents[1] / "virtual_office" / "mfa.py"
    migrations = Path(__file__).parents[1] / "virtual_office" / "migrations.py"
    text = source.read_text(encoding="utf-8")
    # MFA DATETIME values use UTC_TIMESTAMP.  LOGIN's legacy TIMESTAMP
    # `last_login` is deliberately the one exception: MariaDB converts NOW()
    # according to the session contract for that column.
    assert text.count("NOW(") == 1
    assert "last_login=NOW()" in text
    assert "CURRENT_TIMESTAMP" not in text
    assert "'office-mfa-fence-v1',UTC_TIMESTAMP(6)" in migrations.read_text(encoding="utf-8")
    proof = text.split("async def _append_proof", 1)[1].split("async def _append_state", 1)[0]
    state = text.split("async def _append_state", 1)[1].split("async def _invalidate", 1)[0]
    assert "occurred_at" in proof and "UTC_TIMESTAMP(6)" in proof
    assert "occurred_at" in state and "UTC_TIMESTAMP(6)" in state
