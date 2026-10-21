"""Persisted MFA primitives run against the disposable MariaDB fixture."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from tests.identidades import crear_usuario, sql
from virtual_office.mfa import (
    MFAConfig,
    MFAError,
    begin_enrollment,
    confirm_enrollment,
    consume_recovery_code,
    consume_totp_challenge,
    issue_challenge,
)
from virtual_office.migrations import apply_office_migrations


NOW = dt.datetime(2026, 10, 20, 12, 0, tzinfo=dt.timezone.utc)
CONFIG = MFAConfig(
    challenge_lifetime_seconds=120,
    challenge_max_attempts=3,
    enrollment_lifetime_seconds=300,
    recency_seconds=300,
    recovery_code_count=3,
)


async def _migrate():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await apply_office_migrations(cur)


def _otp(secret: str, at: dt.datetime = NOW) -> str:
    import pyotp
    return pyotp.TOTP(secret, digits=6, interval=30).at(at.timestamp())


async def _active_user():
    await _migrate()
    user_id, _ = await crear_usuario()
    enrollment = await begin_enrollment(user_id, config=CONFIG, now=NOW)
    confirmed = await confirm_enrollment(
        user_id, enrollment.enrollment_id, _otp(enrollment.secret), config=CONFIG, now=NOW
    )
    return user_id, enrollment, confirmed


def test_enrollment_encrypts_secret_and_returns_recovery_codes_once(client):
    async def scenario():
        user_id, enrollment, confirmed = await _active_user()
        row = await sql(
            "SELECT state,secret_ciphertext,pending_secret_ciphertext FROM office_mfa_credential WHERE user_id=%s",
            (user_id,), True,
        )
        assert row[0][0] == "ACTIVE"
        assert row[0][1] and row[0][1] != enrollment.secret
        assert row[0][2] is None
        assert len(confirmed.recovery_codes) == CONFIG.recovery_code_count
        assert all(len(code) >= 20 for code in confirmed.recovery_codes)
        stored = await sql("SELECT code_hash FROM office_mfa_recovery_code WHERE user_id=%s", (user_id,), True)
        assert len(stored) == CONFIG.recovery_code_count
        assert all(len(bytes(row[0])) == 32 for row in stored)
    client.portal.call(scenario)


def test_totp_challenges_allow_exactly_one_counter_use_across_concurrent_challenges(client):
    async def scenario():
        user_id, enrollment, _ = await _active_user()
        next_window = NOW + dt.timedelta(seconds=30)
        one = await issue_challenge(user_id, "LOGIN", config=CONFIG, now=next_window)
        two = await issue_challenge(user_id, "LOGIN", config=CONFIG, now=next_window)
        otp = _otp(enrollment.secret, next_window)
        outcomes = await asyncio.gather(
            consume_totp_challenge(user_id, one.token, "LOGIN", otp, config=CONFIG, now=next_window),
            consume_totp_challenge(user_id, two.token, "LOGIN", otp, config=CONFIG, now=next_window),
            return_exceptions=True,
        )
        assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
        assert sum(isinstance(value, MFAError) and value.code == "totp_replayed" for value in outcomes) == 1
        uses = await sql("SELECT COUNT(*) FROM office_mfa_totp_use WHERE user_id=%s AND purpose='LOGIN'", (user_id,), True)
        assert uses == ((1,),)
    client.portal.call(scenario)


def test_bad_expired_and_cross_purpose_challenges_fail_closed(client):
    async def scenario():
        user_id, enrollment, _ = await _active_user()
        challenge = await issue_challenge(user_id, "LOGIN", config=CONFIG, now=NOW)
        with pytest.raises(MFAError, match="challenge_purpose"):
            await consume_totp_challenge(user_id, challenge.token, "STEP_UP", _otp(enrollment.secret), config=CONFIG, now=NOW)
        with pytest.raises(MFAError, match="totp_invalid"):
            await consume_totp_challenge(user_id, challenge.token, "LOGIN", "000000", config=CONFIG, now=NOW)
        expired = await issue_challenge(user_id, "LOGIN", config=CONFIG, now=NOW)
        with pytest.raises(MFAError, match="challenge_expired"):
            await consume_totp_challenge(user_id, expired.token, "LOGIN", _otp(enrollment.secret), config=CONFIG, now=NOW + dt.timedelta(seconds=121))
    client.portal.call(scenario)


def test_challenge_attempt_limit_cross_user_and_version_change_fail_closed(client):
    async def scenario():
        user_id, enrollment, _ = await _active_user()
        other, _ = await crear_usuario()
        challenge = await issue_challenge(user_id, "LOGIN", config=CONFIG, now=NOW)
        with pytest.raises(MFAError, match="challenge_invalid"):
            await consume_totp_challenge(other, challenge.token, "LOGIN", _otp(enrollment.secret), config=CONFIG, now=NOW)
        for _ in range(CONFIG.challenge_max_attempts - 1):
            with pytest.raises(MFAError, match="totp_invalid"):
                await consume_totp_challenge(user_id, challenge.token, "LOGIN", "000000", config=CONFIG, now=NOW)
        with pytest.raises(MFAError, match="challenge_exhausted"):
            await consume_totp_challenge(user_id, challenge.token, "LOGIN", "000000", config=CONFIG, now=NOW)
        fresh = await issue_challenge(user_id, "LOGIN", config=CONFIG, now=NOW)
        await sql("UPDATE jax_users SET mfa_version=mfa_version+1 WHERE user_id=%s", (user_id,))
        with pytest.raises(MFAError, match="challenge_version"):
            await consume_totp_challenge(user_id, fresh.token, "LOGIN", _otp(enrollment.secret), config=CONFIG, now=NOW)
    client.portal.call(scenario)


def test_recovery_code_has_one_concurrent_winner_and_returns_only_capability(client):
    async def scenario():
        user_id, _, confirmed = await _active_user()
        code = confirmed.recovery_codes[0]
        results = await asyncio.gather(
            consume_recovery_code(user_id, code, config=CONFIG, now=NOW),
            consume_recovery_code(user_id, code, config=CONFIG, now=NOW),
            return_exceptions=True,
        )
        assert sum(not isinstance(value, BaseException) for value in results) == 1
        assert sum(isinstance(value, MFAError) and value.code in {"recovery_used", "recovery_invalid"} for value in results) == 1
        capability = next(value for value in results if not isinstance(value, BaseException))
        assert capability.purpose == "RECOVERY"
        row = await sql("SELECT state,secret_ciphertext FROM office_mfa_credential WHERE user_id=%s", (user_id,), True)
        assert row == (("RECOVERY_REQUIRED", None),)
        versions = await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True)
        assert versions == ((2, 2),)
        stored = await sql("SELECT token_hash FROM office_mfa_challenge WHERE user_id=%s AND purpose='RECOVERY'", (user_id,), True)
        assert len(stored) == 1 and bytes(stored[0][0]) != capability.token.encode()
    client.portal.call(scenario)


def test_recovery_code_is_bound_to_its_subject_even_when_versions_match(client):
    async def scenario():
        owner, _, confirmed = await _active_user()
        other, _, _ = await _active_user()
        with pytest.raises(MFAError, match="recovery_invalid"):
            await consume_recovery_code(other, confirmed.recovery_codes[0], config=CONFIG, now=NOW)
        assert await sql("SELECT mfa_version FROM jax_users WHERE user_id=%s", (owner,), True) == ((1,),)
        assert await sql("SELECT mfa_version FROM jax_users WHERE user_id=%s", (other,), True) == ((1,),)
    client.portal.call(scenario)


def test_corrupt_or_missing_ciphertext_never_accepts_an_otp(client, monkeypatch):
    async def scenario():
        from virtual_office import mfa
        user_id, enrollment, _ = await _active_user()
        challenge = await issue_challenge(user_id, "LOGIN", config=CONFIG, now=NOW)
        await sql("UPDATE office_mfa_credential SET secret_ciphertext=%s WHERE user_id=%s", ("not-a-fernet-token", user_id))
        with pytest.raises(MFAError, match="totp_invalid"):
            await consume_totp_challenge(user_id, challenge.token, "LOGIN", _otp(enrollment.secret), config=CONFIG, now=NOW)
        await sql("UPDATE office_mfa_credential SET secret_ciphertext=%s WHERE user_id=%s", (mfa.encrypt_secret(enrollment.secret), user_id))
        monkeypatch.setattr(mfa, "decrypt_db_secret", lambda _value: "")
        missing = await issue_challenge(user_id, "LOGIN", config=CONFIG, now=NOW)
        with pytest.raises(MFAError, match="totp_invalid"):
            await consume_totp_challenge(user_id, missing.token, "LOGIN", _otp(enrollment.secret), config=CONFIG, now=NOW)
    client.portal.call(scenario)


def test_failed_event_write_rolls_back_enrollment_versions_and_credential(client, monkeypatch):
    async def scenario():
        from virtual_office import mfa
        user_id, _ = await crear_usuario()
        enrollment = await begin_enrollment(user_id, config=CONFIG, now=NOW)
        async def fail_record(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")
        monkeypatch.setattr(mfa, "_record", fail_record)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await confirm_enrollment(user_id, enrollment.enrollment_id, _otp(enrollment.secret), config=CONFIG, now=NOW)
        row = await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True)
        assert row == ((0, 0),)
        credential = await sql("SELECT state,secret_ciphertext,pending_secret_ciphertext FROM office_mfa_credential WHERE user_id=%s", (user_id,), True)
        assert credential[0][0] == "PENDING" and credential[0][1] is None and credential[0][2]
        assert await sql("SELECT COUNT(*) FROM office_mfa_recovery_code WHERE user_id=%s", (user_id,), True) == ((0,),)
    client.portal.call(scenario)


def test_mfa_lookup_indexes_have_real_no_filesort_no_temporary_explain(client):
    async def scenario():
        user_id, _, confirmed = await _active_user()
        challenge = await issue_challenge(user_id, "LOGIN", config=CONFIG, now=NOW)
        pool_rows = await sql("EXPLAIN SELECT user_id,purpose FROM office_mfa_challenge WHERE token_hash=%s", (__import__('hashlib').sha256(challenge.token.encode('ascii')).digest(),), True)
        recovery_rows = await sql("EXPLAIN SELECT recovery_code_id FROM office_mfa_recovery_code WHERE code_hash=%s", (__import__('hashlib').sha256(confirmed.recovery_codes[0].encode('ascii')).digest(),), True)
        for rows in (pool_rows, recovery_rows):
            assert rows and rows[0][3] in {"const", "ref"}
            extra = str(rows[0][-1] or "")
            assert "Using filesort" not in extra and "Using temporary" not in extra
    client.portal.call(scenario)


def test_reenrollment_invalidates_prior_pending_and_only_current_version_can_confirm(client):
    async def scenario():
        await _migrate()
        user_id, _ = await crear_usuario()
        first, second = await asyncio.gather(
            begin_enrollment(user_id, config=CONFIG, now=NOW),
            begin_enrollment(user_id, config=CONFIG, now=NOW),
        )
        row = await sql("SELECT pending_enrollment_id FROM office_mfa_credential WHERE user_id=%s", (user_id,), True)
        current = next(item for item in (first, second) if __import__('hashlib').sha256(item.enrollment_id.encode('ascii')).digest() == bytes(row[0][0]))
        previous = second if current is first else first
        with pytest.raises(MFAError, match="enrollment_invalid"):
            await confirm_enrollment(user_id, previous.enrollment_id, _otp(previous.secret), config=CONFIG, now=NOW)
        confirmed = await confirm_enrollment(user_id, current.enrollment_id, _otp(current.secret), config=CONFIG, now=NOW)
        assert confirmed.mfa_version == 1
        assert await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True) == ((1, 1),)
    client.portal.call(scenario)
