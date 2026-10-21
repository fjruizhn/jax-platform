"""Transactional MFA persistence primitives.  HTTP and JWT wiring belongs to Task 3B."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Literal

import pyotp

from crypto_secrets import decrypt_db_secret, encrypt_secret
from db.transaccion import AISLAMIENTO_ADMIN, transaccion

ChallengePurpose = Literal["LOGIN", "STEP_UP", "ENROLLMENT", "RECOVERY"]
_PURPOSES = frozenset({"LOGIN", "STEP_UP", "ENROLLMENT", "RECOVERY"})


class MFAError(RuntimeError):
    """Deliberately opaque service error; callers map it to their own ingress response."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class MFAConfig:
    """Technical controls are supplied explicitly by the application configuration."""

    challenge_lifetime_seconds: int
    challenge_max_attempts: int
    enrollment_lifetime_seconds: int
    recency_seconds: int
    recovery_code_count: int

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class EnrollmentStart:
    enrollment_id: str
    secret: str
    expires_at: dt.datetime


@dataclass(frozen=True)
class EnrollmentConfirmation:
    recovery_codes: tuple[str, ...]
    mfa_version: int


@dataclass(frozen=True)
class MFAChallenge:
    token: str
    purpose: ChallengePurpose
    expires_at: dt.datetime


@dataclass(frozen=True)
class MFAProof:
    """3B may construct this only after independently verifying its caller contract."""

    subject_user_id: int
    token_version: int
    mfa_version: int
    mfa_at: dt.datetime
    kind: Literal["SELF_RECENT", "ROOT_RECENT"]


def _now(value: dt.datetime | None) -> dt.datetime:
    value = value or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        raise ValueError("MFA time must be timezone aware")
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _hash(value: str) -> bytes:
    return hashlib.sha256(value.encode("ascii")).digest()


def _opaque(bits: int = 256) -> str:
    return secrets.token_urlsafe(bits // 8)


def _recovery_code() -> str:
    # URL-safe alphabet has six bits per character; 22 chars exceeds 128 bits.
    return secrets.token_urlsafe(24)


def _valid_otp(otp: str) -> bool:
    return isinstance(otp, str) and len(otp) == 6 and otp.isascii() and otp.isdecimal()


def _counter_for(secret: str, otp: str, now: dt.datetime) -> int | None:
    if not _valid_otp(otp):
        return None
    totp = pyotp.TOTP(secret, digits=6, interval=30, digest=hashlib.sha1)
    # MariaDB DATETIME is deliberately UTC-naive; restore that meaning before
    # deriving the RFC 6238 counter so host locale cannot shift a valid code.
    current = int(now.replace(tzinfo=dt.timezone.utc).timestamp() // 30)
    for counter in (current - 1, current, current + 1):
        if hmac.compare_digest(totp.at(counter * 30), otp):
            return counter
    return None


async def _locked_user(cur, user_id: int) -> tuple[int, int, str] | None:
    await cur.execute(
        "SELECT token_version,mfa_version,status FROM jax_users WHERE user_id=%s FOR UPDATE", (user_id,)
    )
    row = await cur.fetchone()
    if row is None or row[2] != "active":
        return None
    return int(row[0]), int(row[1]), str(row[2])


async def _record(cur, user_id: int, operation: str, reason: str) -> None:
    # Reuse the proven append-only Office event stream.  No token, OTP, secret,
    # recovery code, ciphertext, or input-derived correlation is written.
    await cur.execute(
        "INSERT INTO office_authorization_event "
        "(actor_user_id,actor_principal,event_kind,fence_key,operation,requested_project_id,project_id,tenant_id,result,reason_code,correlation_id,occurred_at) "
        "VALUES (%s,NULL,'AUTHORIZATION',NULL,%s,NULL,NULL,NULL,'ALLOWED',%s,NULL,NOW(6))",
        (user_id, operation, reason),
    )


async def _insert_recovery_codes(cur, user_id: int, mfa_version: int, count: int) -> tuple[str, ...]:
    codes = tuple(_recovery_code() for _ in range(count))
    await cur.executemany(
        "INSERT INTO office_mfa_recovery_code (user_id,mfa_version,code_hash,created_at) VALUES (%s,%s,%s,NOW(6))",
        [(user_id, mfa_version, _hash(code)) for code in codes],
    )
    return codes


async def begin_enrollment(user_id: int, *, config: MFAConfig, now: dt.datetime | None = None) -> EnrollmentStart:
    now = _now(now)
    enrollment_id, secret = _opaque(), pyotp.random_base32(length=32)
    expiry = now + dt.timedelta(seconds=config.enrollment_lifetime_seconds)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        if await _locked_user(cur, user_id) is None:
            raise MFAError("mfa_subject_invalid")
        await cur.execute("SELECT user_id FROM office_mfa_credential WHERE user_id=%s FOR UPDATE", (user_id,))
        ciphertext = encrypt_secret(secret)
        if await cur.fetchone() is None:
            await cur.execute(
                "INSERT INTO office_mfa_credential "
                "(user_id,state,secret_ciphertext,pending_secret_ciphertext,pending_enrollment_id,pending_expires_at,created_at,updated_at) "
                "VALUES (%s,'PENDING',NULL,%s,%s,%s,NOW(6),NOW(6))",
                (user_id, ciphertext, _hash(enrollment_id), expiry),
            )
        else:
            await cur.execute(
                "UPDATE office_mfa_credential SET state='PENDING',secret_ciphertext=NULL,pending_secret_ciphertext=%s,"
                "pending_enrollment_id=%s,pending_expires_at=%s,confirmed_at=NULL,updated_at=NOW(6) WHERE user_id=%s",
                (ciphertext, _hash(enrollment_id), expiry, user_id),
            )
        await _record(cur, user_id, "MFA_ENROLL_START", "mfa_enrollment_pending")
    return EnrollmentStart(enrollment_id, secret, expiry.replace(tzinfo=dt.timezone.utc))


async def confirm_enrollment(user_id: int, enrollment_id: str, otp: str, *, config: MFAConfig, now: dt.datetime | None = None) -> EnrollmentConfirmation:
    now = _now(now)
    result: EnrollmentConfirmation | None = None
    error: str | None = None
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        user = await _locked_user(cur, user_id)
        if user is None:
            error = "mfa_subject_invalid"
        else:
            await cur.execute(
                "SELECT state,pending_secret_ciphertext,pending_enrollment_id,pending_expires_at FROM office_mfa_credential WHERE user_id=%s FOR UPDATE",
                (user_id,),
            )
            credential = await cur.fetchone()
            if credential is None or credential[0] != "PENDING" or not hmac.compare_digest(bytes(credential[2] or b""), _hash(enrollment_id)):
                error = "enrollment_invalid"
            elif credential[3] is None or credential[3] < now:
                error = "enrollment_expired"
            else:
                secret = decrypt_db_secret(str(credential[1] or ""))
                counter = _counter_for(secret, otp, now) if secret else None
                if counter is None:
                    error = "totp_invalid"
                else:
                    next_version = user[1] + 1
                    await cur.execute(
                        "UPDATE jax_users SET token_version=token_version+1,mfa_version=mfa_version+1 WHERE user_id=%s", (user_id,)
                    )
                    await cur.execute(
                        "UPDATE office_mfa_credential SET state='ACTIVE',secret_ciphertext=pending_secret_ciphertext,"
                        "pending_secret_ciphertext=NULL,pending_enrollment_id=NULL,pending_expires_at=NULL,confirmed_at=NOW(6),updated_at=NOW(6) WHERE user_id=%s",
                        (user_id,),
                    )
                    await cur.execute(
                        "INSERT INTO office_mfa_totp_use (user_id,mfa_version,time_counter,purpose,used_at) VALUES (%s,%s,%s,'ENROLLMENT',NOW(6))",
                        (user_id, next_version, counter),
                    )
                    await cur.execute("DELETE FROM office_mfa_recovery_code WHERE user_id=%s", (user_id,))
                    codes = await _insert_recovery_codes(cur, user_id, next_version, config.recovery_code_count)
                    await _record(cur, user_id, "MFA_ENROLL_CONFIRM", "mfa_enrollment_confirmed")
                    result = EnrollmentConfirmation(codes, next_version)
    if error:
        raise MFAError(error)
    assert result is not None
    # The canonical stream cut is explicitly after the successful transaction.
    from auth.conexiones import _cortar_conexiones
    await _cortar_conexiones(user_id)
    return result


async def issue_challenge(user_id: int, purpose: ChallengePurpose, *, config: MFAConfig, now: dt.datetime | None = None) -> MFAChallenge:
    if purpose not in _PURPOSES:
        raise MFAError("challenge_purpose")
    now = _now(now)
    token, expiry = _opaque(), now + dt.timedelta(seconds=config.challenge_lifetime_seconds)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        user = await _locked_user(cur, user_id)
        if user is None:
            raise MFAError("mfa_subject_invalid")
        await cur.execute(
            "INSERT INTO office_mfa_challenge "
            "(token_hash,user_id,purpose,expected_token_version,expected_mfa_version,attempt_count,max_attempts,expires_at,state,created_at) "
            "VALUES (%s,%s,%s,%s,%s,0,%s,%s,'ISSUED',NOW(6))",
            (_hash(token), user_id, purpose, user[0], user[1], config.challenge_max_attempts, expiry),
        )
    return MFAChallenge(token, purpose, expiry.replace(tzinfo=dt.timezone.utc))


async def consume_totp_challenge(user_id: int, token: str, purpose: ChallengePurpose, otp: str, *, config: MFAConfig, now: dt.datetime | None = None) -> int:
    if purpose not in _PURPOSES:
        raise MFAError("challenge_purpose")
    now = _now(now)
    error: str | None = None
    version: int | None = None
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await cur.execute(
            "SELECT user_id,purpose,expected_token_version,expected_mfa_version,attempt_count,max_attempts,expires_at,state "
            "FROM office_mfa_challenge WHERE token_hash=%s FOR UPDATE", (_hash(token),)
        )
        challenge = await cur.fetchone()
        if challenge is None or int(challenge[0]) != user_id:
            error = "challenge_invalid"
        elif challenge[1] != purpose:
            error = "challenge_purpose"
        elif challenge[7] != "ISSUED":
            error = "challenge_used"
        elif challenge[6] < now:
            await cur.execute("UPDATE office_mfa_challenge SET state='EXPIRED',used_at=NOW(6) WHERE token_hash=%s", (_hash(token),))
            error = "challenge_expired"
        else:
            user = await _locked_user(cur, user_id)
            if user is None or user[0] != int(challenge[2]) or user[1] != int(challenge[3]):
                error = "challenge_version"
            else:
                await cur.execute("SELECT state,secret_ciphertext FROM office_mfa_credential WHERE user_id=%s FOR UPDATE", (user_id,))
                credential = await cur.fetchone()
                secret = "" if credential is None or credential[0] != "ACTIVE" else decrypt_db_secret(str(credential[1] or ""))
                counter = _counter_for(secret, otp, now) if secret else None
                if counter is None:
                    attempts = int(challenge[4]) + 1
                    state = "EXHAUSTED" if attempts >= int(challenge[5]) else "ISSUED"
                    await cur.execute("UPDATE office_mfa_challenge SET attempt_count=%s,state=%s,used_at=IF(%s='EXHAUSTED',NOW(6),NULL) WHERE token_hash=%s", (attempts, state, state, _hash(token)))
                    error = "challenge_exhausted" if state == "EXHAUSTED" else "totp_invalid"
                else:
                    try:
                        await cur.execute(
                            "INSERT INTO office_mfa_totp_use (user_id,mfa_version,time_counter,purpose,used_at) VALUES (%s,%s,%s,%s,NOW(6))",
                            (user_id, user[1], counter, purpose),
                        )
                    except Exception as exc:
                        if getattr(exc, "args", (None,))[0] != 1062:
                            raise
                        error = "totp_replayed"
                    else:
                        await cur.execute("UPDATE office_mfa_challenge SET state='CONSUMED',used_at=NOW(6) WHERE token_hash=%s", (_hash(token),))
                        await _record(cur, user_id, "MFA_CHALLENGE_OK", "mfa_totp_verified")
                        version = user[1]
    if error:
        raise MFAError(error)
    assert version is not None
    return version


async def consume_recovery_code(user_id: int, code: str, *, config: MFAConfig, now: dt.datetime | None = None) -> MFAChallenge:
    """Consume one code and return only an opaque re-enrollment capability."""
    now = _now(now)
    capability, expiry = _opaque(), now + dt.timedelta(seconds=config.challenge_lifetime_seconds)
    error: str | None = None
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        user = await _locked_user(cur, user_id)
        if user is None:
            error = "mfa_subject_invalid"
        else:
            await cur.execute(
                "SELECT recovery_code_id,mfa_version,used_at FROM office_mfa_recovery_code WHERE user_id=%s AND code_hash=%s FOR UPDATE",
                (user_id, _hash(code)),
            )
            row = await cur.fetchone()
            if row is None or int(row[1]) != user[1]:
                error = "recovery_invalid"
            elif row[2] is not None:
                error = "recovery_used"
            else:
                await cur.execute("UPDATE office_mfa_recovery_code SET used_at=NOW(6) WHERE recovery_code_id=%s AND used_at IS NULL", (row[0],))
                if cur.rowcount != 1:
                    error = "recovery_used"
                else:
                    next_version = user[1] + 1
                    await cur.execute("UPDATE jax_users SET token_version=token_version+1,mfa_version=mfa_version+1 WHERE user_id=%s", (user_id,))
                    await cur.execute(
                        "UPDATE office_mfa_credential SET state='RECOVERY_REQUIRED',secret_ciphertext=NULL,pending_secret_ciphertext=NULL,"
                        "pending_enrollment_id=NULL,pending_expires_at=NULL,confirmed_at=NULL,updated_at=NOW(6) WHERE user_id=%s", (user_id,)
                    )
                    await cur.execute(
                        "INSERT INTO office_mfa_challenge (token_hash,user_id,purpose,expected_token_version,expected_mfa_version,attempt_count,max_attempts,expires_at,state,created_at) "
                        "VALUES (%s,%s,'RECOVERY',%s,%s,0,%s,%s,'ISSUED',NOW(6))",
                        (_hash(capability), user_id, user[0] + 1, next_version, config.challenge_max_attempts, expiry),
                    )
                    await _record(cur, user_id, "MFA_RECOVERY_USED", "mfa_recovery_required")
    if error:
        raise MFAError(error)
    from auth.conexiones import _cortar_conexiones
    await _cortar_conexiones(user_id)
    return MFAChallenge(capability, "RECOVERY", expiry.replace(tzinfo=dt.timezone.utc))


async def regenerate_recovery_codes(user_id: int, password: str, proof: MFAProof | None, *, config: MFAConfig, now: dt.datetime | None = None) -> tuple[str, ...]:
    """Caller supplies a previously verified, recent self-proof; missing proof denies."""
    if proof is None or proof.kind != "SELF_RECENT" or proof.subject_user_id != user_id:
        raise MFAError("proof_required")
    now = _now(now)
    if proof.mfa_at.tzinfo is None or now - proof.mfa_at.astimezone(dt.timezone.utc).replace(tzinfo=None) > dt.timedelta(seconds=config.recency_seconds):
        raise MFAError("proof_stale")
    from db.seed import verify_password
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        user = await _locked_user(cur, user_id)
        if user is None or user[0] != proof.token_version or user[1] != proof.mfa_version:
            raise MFAError("proof_invalid")
        await cur.execute("SELECT password_hash FROM jax_users WHERE user_id=%s", (user_id,))
        row = await cur.fetchone()
        # This is async and uses asyncio.to_thread in the canonical helper.
        if row is None or not await verify_password(password, row[0]):
            raise MFAError("password_invalid")
        await cur.execute("SELECT state FROM office_mfa_credential WHERE user_id=%s FOR UPDATE", (user_id,))
        credential = await cur.fetchone()
        if credential is None or credential[0] != "ACTIVE":
            raise MFAError("mfa_not_active")
        next_version = user[1] + 1
        await cur.execute("UPDATE jax_users SET token_version=token_version+1,mfa_version=mfa_version+1 WHERE user_id=%s", (user_id,))
        await cur.execute("DELETE FROM office_mfa_recovery_code WHERE user_id=%s", (user_id,))
        codes = await _insert_recovery_codes(cur, user_id, next_version, config.recovery_code_count)
        await _record(cur, user_id, "MFA_RECOVERY_REGEN", "mfa_recovery_regenerated")
    from auth.conexiones import _cortar_conexiones
    await _cortar_conexiones(user_id)
    return codes


async def disable_or_reset_mfa(user_id: int, proof: MFAProof | None, *, config: MFAConfig, now: dt.datetime | None = None) -> None:
    """No unguarded reset path: only a current, recent root proof may target another subject."""
    if proof is None or proof.kind != "ROOT_RECENT" or proof.subject_user_id == user_id:
        raise MFAError("proof_required")
    now = _now(now)
    if proof.mfa_at.tzinfo is None or now - proof.mfa_at.astimezone(dt.timezone.utc).replace(tzinfo=None) > dt.timedelta(seconds=config.recency_seconds):
        raise MFAError("proof_stale")
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        target = await _locked_user(cur, user_id)
        actor = await _locked_user(cur, proof.subject_user_id)
        if target is None or actor is None or actor[0] != proof.token_version or actor[1] != proof.mfa_version:
            raise MFAError("proof_invalid")
        await cur.execute("UPDATE jax_users SET token_version=token_version+1,mfa_version=mfa_version+1 WHERE user_id=%s", (user_id,))
        await cur.execute("UPDATE office_mfa_credential SET state='DISABLED',secret_ciphertext=NULL,pending_secret_ciphertext=NULL,pending_enrollment_id=NULL,pending_expires_at=NULL,confirmed_at=NULL,updated_at=NOW(6) WHERE user_id=%s", (user_id,))
        await cur.execute("DELETE FROM office_mfa_recovery_code WHERE user_id=%s", (user_id,))
        await _record(cur, user_id, "MFA_RESET", "mfa_reset_by_root")
    from auth.conexiones import _cortar_conexiones
    await _cortar_conexiones(user_id)
