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
        ) == (("ISSUED", 1),)
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
