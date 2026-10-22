"""The cursor-only MFA boundary persists durable evidence before returning it."""
from __future__ import annotations

import asyncio
import datetime as dt
import secrets

import pyotp
import pytest

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
        assert isinstance(future, MFADenied) and future.code == "proof_stale"
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
        start = await _first_enrollment(user_id)
        rows = await sql(
            "EXPLAIN SELECT attempt_count,max_attempts,state FROM office_mfa_challenge "
            "WHERE user_id=%s AND purpose='ENROLLMENT' AND expected_token_version=0 "
            "AND expected_mfa_version=0 AND state IN ('ISSUED','EXHAUSTED') "
            "AND expires_at >= NOW(6) ORDER BY created_at DESC LIMIT 1",
            (user_id,), True,
        )
        assert rows and rows[0][5] == "idx_office_mfa_challenge_user"
        print("MFA_BUDGET_EXPLAIN", rows[0])
        assert start.enrollment_id
    client.portal.call(scenario)
