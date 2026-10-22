"""MFA persistence operations.  Every public operation receives its caller's cursor."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Literal

import pyotp

from crypto_secrets import decrypt_db_secret, encrypt_secret

ChallengePurpose = Literal["LOGIN", "STEP_UP", "ENROLLMENT", "RECOVERY"]


class MFAError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class MFADenied:
    code: str
    attempts_remaining: int = 0
    terminal: bool = False


@dataclass(frozen=True)
class MFAConfig:
    challenge_lifetime_seconds: int
    challenge_max_attempts: int
    enrollment_lifetime_seconds: int
    recency_seconds: int
    recovery_code_count: int
    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in self.__dict__.values()):
            raise ValueError("MFAConfig values must be positive integers")


@dataclass(frozen=True)
class MFAChallenge:
    token: str
    purpose: ChallengePurpose
    expires_at: dt.datetime


@dataclass(frozen=True)
class EnrollmentStart:
    enrollment_id: str
    secret: str
    expires_at: dt.datetime


@dataclass(frozen=True)
class MFAProofReceipt:
    event_id: int
    subject_user_id: int
    purpose: ChallengePurpose
    token_version: int
    mfa_version: int
    occurred_at: dt.datetime


@dataclass(frozen=True)
class RecoveryCapability:
    token: str
    expires_at: dt.datetime


@dataclass(frozen=True)
class EnrollmentConfirmation:
    recovery_codes: tuple[str, ...]
    token_version: int
    mfa_version: int
    state_event_id: int


@dataclass(frozen=True)
class MFAStateReceipt:
    event_id: int
    subject_user_id: int
    token_version: int
    mfa_version: int


def _hash(value: str) -> bytes: return hashlib.sha256(value.encode("ascii")).digest()
def _opaque() -> str: return secrets.token_urlsafe(32)
def _code() -> str: return secrets.token_urlsafe(24)
def _cid(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) != 16:
        raise ValueError("server_correlation_id must be 16 bytes")
    return value
def _utc(value: dt.datetime) -> dt.datetime:
    """Expose every MFA timestamp as an aware UTC instant.

    MariaDB DATETIME values have no zone.  MFA writes and reads them through
    UTC_TIMESTAMP(6), so a naive driver value denotes UTC; a future aware
    driver value must be converted rather than relabelled.
    """
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)
def _otp_counter(secret: str, otp: str, now: dt.datetime) -> int | None:
    if not isinstance(otp, str) or len(otp) != 6 or not otp.isascii() or not otp.isdecimal():
        return None
    totp = pyotp.TOTP(secret, digits=6, interval=30, digest=hashlib.sha1)
    current = int(_utc(now).timestamp() // 30)
    for counter in (current - 1, current, current + 1):
        if hmac.compare_digest(totp.at(counter * 30), otp):
            return counter
    return None


async def _db_now(cur) -> dt.datetime:
    await cur.execute("SELECT UTC_TIMESTAMP(6)")
    return (await cur.fetchone())[0]


async def _user(cur, user_id: int):
    await cur.execute("SELECT token_version,mfa_version,status FROM jax_users WHERE user_id=%s FOR UPDATE", (user_id,))
    row = await cur.fetchone()
    return None if row is None or row[2] != "active" else (int(row[0]), int(row[1]))


async def _challenge_id(cur, token_hash: bytes) -> int:
    await cur.execute("SELECT challenge_id FROM office_mfa_challenge WHERE token_hash=%s", (token_hash,))
    return int((await cur.fetchone())[0])


async def _append_proof(cur, *, subject: int, purpose: str, token_version: int, mfa_version: int, principal: str, actor: int | None, challenge_id: int | None, cid: bytes) -> MFAProofReceipt:
    await cur.execute("INSERT INTO office_mfa_security_event (event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) VALUES ('MFA_PROOF',%s,%s,%s,%s,'MFA_PROOF',%s,%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,UTC_TIMESTAMP(6))", (actor, principal, challenge_id, subject, purpose, token_version, mfa_version, cid))
    event_id = cur.lastrowid
    await cur.execute("SELECT occurred_at FROM office_mfa_security_event WHERE event_id=%s", (event_id,))
    return MFAProofReceipt(int(event_id), subject, purpose, token_version, mfa_version, _utc((await cur.fetchone())[0]))


async def _append_state(cur, *, subject: int, operation: str, purpose: str, principal: str, actor: int | None, challenge_id: int | None, previous: tuple[int,int], current: tuple[int,int], proof_id: int, cid: bytes) -> int:
    await cur.execute("INSERT INTO office_mfa_security_event (event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) VALUES ('MFA_STATE_CHANGE',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,'RECORDED',%s,NULL,UTC_TIMESTAMP(6))", (actor, principal, challenge_id, subject, operation, purpose, current[0], current[1], previous[0], previous[1], proof_id, cid))
    return int(cur.lastrowid)


async def _invalidate(cur, user: int) -> None:
    await cur.execute("UPDATE office_mfa_challenge SET state='INVALIDATED',used_at=UTC_TIMESTAMP(6) WHERE user_id=%s AND state IN ('ISSUED','EXHAUSTED')", (user,))


async def _deny_attempt(cur, token_hash: bytes, count: int, maximum: int) -> MFADenied:
    """Persist an OTP denial; callers must return normally so it commits."""
    next_count = count + 1
    terminal = next_count >= maximum
    await cur.execute(
        "UPDATE office_mfa_challenge SET attempt_count=%s,state=%s,"
        "used_at=IF(%s,UTC_TIMESTAMP(6),NULL) WHERE token_hash=%s AND state='ISSUED'",
        (next_count, "EXHAUSTED" if terminal else "ISSUED", terminal, token_hash),
    )
    if cur.rowcount != 1:
        return MFADenied("challenge_unavailable", terminal=True)
    return MFADenied("challenge_exhausted" if terminal else "totp_invalid", maximum - next_count, terminal)


async def _insert_challenge(
    cur, *, user: int, purpose: ChallengePurpose, versions: tuple[int, int],
    token: str, ttl: int, attempts: int, now: dt.datetime | None = None,
    expiry: dt.datetime | None = None,
):
    now = now or await _db_now(cur)
    expiry = expiry or now + dt.timedelta(seconds=ttl)
    scope = (user, purpose, versions[0], versions[1], now)
    # Exhaustion takes priority over a still-issued row.  These two index-only
    # existence lookups avoid an ORDER BY filesort and cannot hide exhaustion.
    await cur.execute(
        "SELECT 1 FROM office_mfa_challenge WHERE user_id=%s AND purpose=%s "
        "AND expected_token_version=%s AND expected_mfa_version=%s "
        "AND state='EXHAUSTED' AND expires_at >= %s LIMIT 1 FOR UPDATE",
        scope,
    )
    if await cur.fetchone():
        return MFADenied("challenge_exhausted", 0, True)
    await cur.execute(
        "SELECT attempt_count,max_attempts FROM office_mfa_challenge "
        "WHERE user_id=%s AND purpose=%s AND expected_token_version=%s "
        "AND expected_mfa_version=%s AND state='ISSUED' AND expires_at >= %s "
        "LIMIT 1 FOR UPDATE",
        scope,
    )
    previous = await cur.fetchone()
    if previous:
        return MFADenied("challenge_live", int(previous[1]) - int(previous[0]))
    await cur.execute("INSERT INTO office_mfa_challenge (token_hash,user_id,purpose,expected_token_version,expected_mfa_version,attempt_count,max_attempts,expires_at,state,created_at) VALUES (%s,%s,%s,%s,%s,0,%s,%s,'ISSUED',UTC_TIMESTAMP(6))", (_hash(token), user, purpose, versions[0], versions[1], attempts, expiry))
    return MFAChallenge(token, purpose, _utc(expiry))


async def _active_credential(cur, user: int):
    await cur.execute("SELECT state,secret_ciphertext FROM office_mfa_credential WHERE user_id=%s FOR UPDATE", (user,))
    row = await cur.fetchone()
    return row if row and row[0] == "ACTIVE" else None


def _decrypted_secret_or_raise(ciphertext: object) -> str:
    secret = decrypt_db_secret(str(ciphertext or ""))
    if not secret:
        raise MFAError("mfa_secret_unavailable")
    return secret


async def _has_current_recovery_lineage(cur, *, user: int, versions: tuple[int, int]) -> bool:
    await cur.execute(
        "SELECT event_id FROM office_mfa_security_event "
        "FORCE INDEX (idx_office_mfa_security_event_proof) "
        "WHERE subject_user_id=%s AND purpose='RECOVERY' AND token_version=%s "
        "AND mfa_version=%s AND event_kind='MFA_STATE_CHANGE' "
        "AND operation='RECOVERY_CONSUME' AND principal_kind='RECOVERY_CAPABILITY' "
        "AND result='RECORDED' LIMIT 1",
        (user, versions[0], versions[1]),
    )
    return await cur.fetchone() is not None


async def _pending_enrollment_anchor(
    cur, *, user: int, current_mfa_version: int, pending_enrollment_id: object,
):
    """Lock and validate the immutable challenge that owns a pending secret."""
    if pending_enrollment_id is None:
        return MFADenied("pending_origin_invalid", terminal=True)
    await cur.execute(
        "SELECT user_id,purpose,expected_token_version,expected_mfa_version,state,"
        "expires_at,attempt_count,max_attempts FROM office_mfa_challenge "
        "WHERE token_hash=%s FOR UPDATE", (pending_enrollment_id,),
    )
    anchor = await cur.fetchone()
    if (
        not anchor or int(anchor[0]) != user or anchor[1] != "ENROLLMENT"
        or int(anchor[3]) != current_mfa_version
    ):
        return MFADenied("pending_origin_invalid", terminal=True)
    return anchor


async def issue_login_challenge_after_password_in_cursor(cur, *, preauthenticated_user_id: int, config: MFAConfig):
    versions = await _user(cur, preauthenticated_user_id)
    if versions is None or not await _active_credential(cur, preauthenticated_user_id): return MFADenied("mfa_subject_invalid", terminal=True)
    return await _insert_challenge(cur, user=preauthenticated_user_id, purpose="LOGIN", versions=versions, token=_opaque(), ttl=config.challenge_lifetime_seconds, attempts=config.challenge_max_attempts)


async def issue_step_up_challenge_in_cursor(cur, *, actor_user_id: int, config: MFAConfig):
    versions = await _user(cur, actor_user_id)
    if versions is None or not await _active_credential(cur, actor_user_id): return MFADenied("mfa_subject_invalid", terminal=True)
    return await _insert_challenge(cur, user=actor_user_id, purpose="STEP_UP", versions=versions, token=_opaque(), ttl=config.challenge_lifetime_seconds, attempts=config.challenge_max_attempts)


async def verify_and_consume_totp_in_cursor(cur, *, challenge_token: str, otp: str, server_correlation_id: bytes):
    cid = _cid(server_correlation_id)
    hashed = _hash(challenge_token)
    await cur.execute("SELECT user_id FROM office_mfa_challenge WHERE token_hash=%s", (hashed,))
    found = await cur.fetchone()
    if not found:
        return MFADenied("challenge_invalid", terminal=True)
    user_id = int(found[0])
    versions = await _user(cur, user_id)
    await cur.execute(
        "SELECT challenge_id,purpose,expected_token_version,expected_mfa_version,"
        "attempt_count,max_attempts,expires_at,state FROM office_mfa_challenge "
        "WHERE token_hash=%s FOR UPDATE", (hashed,),
    )
    row = await cur.fetchone()
    if row is None or versions is None:
        return MFADenied("challenge_invalid", terminal=True)
    challenge_id, purpose, tv, mv, count, maximum, expiry, state = row
    now = await _db_now(cur)
    if purpose not in ("LOGIN", "STEP_UP") or state != "ISSUED":
        return MFADenied("challenge_unavailable", terminal=True)
    if expiry < now:
        await cur.execute(
            "UPDATE office_mfa_challenge SET state='EXPIRED',used_at=UTC_TIMESTAMP(6) "
            "WHERE token_hash=%s", (hashed,),
        )
        return MFADenied("challenge_expired", terminal=True)
    if versions != (int(tv), int(mv)):
        await cur.execute("UPDATE office_mfa_challenge SET state='INVALIDATED',used_at=UTC_TIMESTAMP(6) WHERE token_hash=%s AND state='ISSUED'", (hashed,))
        return MFADenied("challenge_version", terminal=True)
    credential = await _active_credential(cur, user_id)
    if credential is None:
        return MFADenied("mfa_subject_invalid", terminal=True)
    secret = _decrypted_secret_or_raise(credential[1])
    counter = _otp_counter(secret, otp, now)
    if counter is None:
        return await _deny_attempt(cur, hashed, int(count), int(maximum))
    try:
        await cur.execute("INSERT INTO office_mfa_totp_use (user_id,mfa_version,time_counter,purpose,used_at) VALUES (%s,%s,%s,%s,UTC_TIMESTAMP(6))", (user_id, versions[1], counter, purpose))
    except Exception as exc:
        if getattr(exc, "args", [None])[0] != 1062:
            raise
        return await _deny_attempt(cur, hashed, int(count), int(maximum))
    await cur.execute("UPDATE office_mfa_challenge SET state='CONSUMED',used_at=UTC_TIMESTAMP(6) WHERE token_hash=%s AND state='ISSUED'", (hashed,))
    if cur.rowcount != 1:
        raise RuntimeError("challenge CAS lost after TOTP insert")
    return await _append_proof(cur, subject=user_id, purpose=purpose, token_version=versions[0], mfa_version=versions[1], principal="PASSWORD_PREAUTH" if purpose == "LOGIN" else "USER_SESSION", actor=None if purpose == "LOGIN" else user_id, challenge_id=int(challenge_id) if purpose == "LOGIN" else None, cid=cid)


async def start_first_enrollment_after_password_in_cursor(cur, *, preauthenticated_user_id: int, config: MFAConfig):
    versions = await _user(cur, preauthenticated_user_id)
    if versions is None: return MFADenied("mfa_subject_invalid", terminal=True)
    await cur.execute(
        "SELECT state,pending_enrollment_id FROM office_mfa_credential "
        "WHERE user_id=%s FOR UPDATE", (preauthenticated_user_id,),
    )
    existing = await cur.fetchone()
    if existing and existing[0] == "ACTIVE":
        return MFADenied("mfa_already_active", terminal=True)
    if existing and existing[0] == "RECOVERY_REQUIRED":
        return MFADenied("recovery_required", terminal=True)
    now = await _db_now(cur)
    if existing and existing[0] == "PENDING":
        anchor = await _pending_enrollment_anchor(
            cur, user=preauthenticated_user_id, current_mfa_version=versions[1],
            pending_enrollment_id=existing[1],
        )
        if isinstance(anchor, MFADenied):
            return anchor
        historical_versions = (int(anchor[2]), int(anchor[3]))
        if await _has_current_recovery_lineage(
            cur, user=preauthenticated_user_id, versions=historical_versions,
        ):
            return MFADenied("recovery_required", terminal=True)
        # A version cut can invalidate the capability, but it must not erase
        # the original pending-first-enrollment window or its OTP budget.
        # The immutable anchor is authoritative until its own UTC expiry.
        if anchor[5] >= now:
            if anchor[4] == "EXHAUSTED" or int(anchor[6]) >= int(anchor[7]):
                return MFADenied("challenge_exhausted", 0, True)
            return MFADenied("challenge_live", int(anchor[7]) - int(anchor[6]))
    token = _opaque()
    secret = pyotp.random_base32(length=32)
    expiry = now + dt.timedelta(seconds=config.enrollment_lifetime_seconds)
    challenge = await _insert_challenge(
        cur, user=preauthenticated_user_id, purpose="ENROLLMENT", versions=versions,
        token=token, ttl=config.enrollment_lifetime_seconds, attempts=config.challenge_max_attempts,
        now=now, expiry=expiry,
    )
    if isinstance(challenge, MFADenied):
        return challenge
    if existing:
        # Expire the exact historical owner before changing the credential's
        # pointer.  It may carry an older token version after a session cut.
        await cur.execute(
            "UPDATE office_mfa_challenge SET state='EXPIRED',used_at=UTC_TIMESTAMP(6) "
            "WHERE token_hash=%s AND state='ISSUED' "
            "AND expires_at < %s",
            (existing[1], now),
        )
    cipher = encrypt_secret(secret)
    if existing:
        await cur.execute("UPDATE office_mfa_credential SET state='PENDING',secret_ciphertext=NULL,pending_secret_ciphertext=%s,pending_enrollment_id=%s,pending_expires_at=%s,updated_at=UTC_TIMESTAMP(6) WHERE user_id=%s", (cipher, _hash(token), expiry, preauthenticated_user_id))
    else:
        await cur.execute("INSERT INTO office_mfa_credential (user_id,state,secret_ciphertext,pending_secret_ciphertext,pending_enrollment_id,pending_expires_at,created_at,updated_at) VALUES (%s,'PENDING',NULL,%s,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))", (preauthenticated_user_id, cipher, _hash(token), expiry))
    return EnrollmentStart(token, secret, _utc(expiry))


async def reload_current_self_mfa_proof_in_cursor(cur, *, actor_user_id: int, proof_event_id: int, config: MFAConfig):
    versions = await _user(cur, actor_user_id)
    if versions is None: return MFADenied("proof_invalid", terminal=True)
    await cur.execute("SELECT subject_user_id,purpose,token_version,mfa_version,occurred_at,result,principal_kind,actor_user_id FROM office_mfa_security_event WHERE event_id=%s FOR UPDATE", (proof_event_id,)); row = await cur.fetchone()
    if not row: return MFADenied("proof_invalid", terminal=True)
    subject,purpose,tv,mv,when,result,principal,actor = row; now = await _db_now(cur)
    okay = subject == actor_user_id and result == "VERIFIED" and (purpose == "LOGIN" and principal == "PASSWORD_PREAUTH" and actor is None or purpose == "STEP_UP" and principal == "USER_SESSION" and actor == actor_user_id)
    if not okay or versions != (int(tv),int(mv)) or when > now or now - when > dt.timedelta(seconds=config.recency_seconds): return MFADenied("proof_stale", terminal=True)
    return MFAProofReceipt(proof_event_id, actor_user_id, purpose, int(tv), int(mv), _utc(when))


async def start_reenrollment_after_password_in_cursor(cur, *, actor_user_id: int, proof_event_id: int, config: MFAConfig):
    proof = await reload_current_self_mfa_proof_in_cursor(cur, actor_user_id=actor_user_id, proof_event_id=proof_event_id, config=config)
    if isinstance(proof, MFADenied): return proof
    credential = await _active_credential(cur, actor_user_id)
    if not credential: return MFADenied("mfa_not_active", terminal=True)
    token = _opaque()
    secret = pyotp.random_base32(length=32)
    now = await _db_now(cur)
    expiry = now + dt.timedelta(seconds=config.enrollment_lifetime_seconds)
    challenge = await _insert_challenge(
        cur, user=actor_user_id, purpose="ENROLLMENT",
        versions=(proof.token_version, proof.mfa_version), token=token,
        ttl=config.enrollment_lifetime_seconds, attempts=config.challenge_max_attempts,
        now=now, expiry=expiry,
    )
    if isinstance(challenge, MFADenied):
        return challenge
    await cur.execute("UPDATE office_mfa_credential SET pending_secret_ciphertext=%s,pending_enrollment_id=%s,pending_expires_at=%s,updated_at=UTC_TIMESTAMP(6) WHERE user_id=%s", (encrypt_secret(secret),_hash(token),expiry,actor_user_id))
    return EnrollmentStart(token,secret,_utc(expiry))


async def consume_recovery_code_in_cursor(cur, *, recovery_code: str, config: MFAConfig, server_correlation_id: bytes):
    cid = _cid(server_correlation_id)
    hashed = _hash(recovery_code)
    await cur.execute("SELECT user_id FROM office_mfa_recovery_code WHERE code_hash=%s", (hashed,))
    found = await cur.fetchone()
    if not found:
        return MFADenied("recovery_invalid", terminal=True)
    user_id = int(found[0])
    versions = await _user(cur, user_id)
    await cur.execute(
        "SELECT recovery_code_id,mfa_version,used_at FROM office_mfa_recovery_code "
        "WHERE code_hash=%s FOR UPDATE", (hashed,),
    )
    row = await cur.fetchone()
    if not row or versions is None or row[2] is not None or int(row[1]) != versions[1]:
        return MFADenied("recovery_invalid", terminal=True)
    await cur.execute(
        "UPDATE office_mfa_recovery_code SET used_at=UTC_TIMESTAMP(6) "
        "WHERE recovery_code_id=%s AND used_at IS NULL", (row[0],),
    )
    if cur.rowcount != 1:
        return MFADenied("recovery_used", terminal=True)
    token = _opaque()
    current = (versions[0] + 1, versions[1] + 1)
    await cur.execute(
        "UPDATE jax_users SET token_version=token_version+1,mfa_version=mfa_version+1 "
        "WHERE user_id=%s", (user_id,),
    )
    await cur.execute(
        "UPDATE office_mfa_credential SET state='RECOVERY_REQUIRED',secret_ciphertext=NULL,"
        "pending_secret_ciphertext=NULL,pending_enrollment_id=NULL,pending_expires_at=NULL,"
        "confirmed_at=NULL,updated_at=UTC_TIMESTAMP(6) WHERE user_id=%s", (user_id,),
    )
    await cur.execute(
        "UPDATE office_mfa_recovery_code SET used_at=COALESCE(used_at,UTC_TIMESTAMP(6)) WHERE user_id=%s",
        (user_id,),
    )
    await _invalidate(cur, user_id)
    challenge = await _insert_challenge(
        cur, user=user_id, purpose="RECOVERY", versions=current, token=token,
        ttl=config.challenge_lifetime_seconds, attempts=config.challenge_max_attempts,
    )
    if isinstance(challenge, MFADenied):
        raise RuntimeError("recovery challenge unexpectedly denied")
    challenge_id = await _challenge_id(cur, _hash(token))
    proof = await _append_proof(
        cur, subject=user_id, purpose="RECOVERY", token_version=current[0],
        mfa_version=current[1], principal="RECOVERY_CAPABILITY", actor=None,
        challenge_id=challenge_id, cid=cid,
    )
    await _append_state(
        cur, subject=user_id, operation="RECOVERY_CONSUME", purpose="RECOVERY",
        principal="RECOVERY_CAPABILITY", actor=None, challenge_id=challenge_id,
        previous=versions, current=current, proof_id=proof.event_id, cid=cid,
    )
    return RecoveryCapability(challenge.token,challenge.expires_at)


async def start_recovery_enrollment_in_cursor(cur, *, recovery_capability_token: str, config: MFAConfig):
    hashed = _hash(recovery_capability_token)
    await cur.execute("SELECT user_id FROM office_mfa_challenge WHERE token_hash=%s", (hashed,))
    found = await cur.fetchone()
    if not found:
        return MFADenied("recovery_invalid", terminal=True)
    user_id = int(found[0])
    versions = await _user(cur, user_id)
    await cur.execute(
        "SELECT purpose,expected_token_version,expected_mfa_version,expires_at,state "
        "FROM office_mfa_challenge WHERE token_hash=%s FOR UPDATE", (hashed,),
    )
    recovery = await cur.fetchone()
    now = await _db_now(cur)
    await cur.execute(
        "SELECT state,pending_secret_ciphertext,pending_enrollment_id,pending_expires_at "
        "FROM office_mfa_credential WHERE user_id=%s FOR UPDATE", (user_id,),
    )
    credential = await cur.fetchone()
    if (
        not recovery or versions is None or recovery[0] != "RECOVERY"
        or recovery[4] != "ISSUED" or recovery[3] < now
        or versions != (int(recovery[1]), int(recovery[2]))
        or not credential or credential[0] != "RECOVERY_REQUIRED"
        or any(value is not None for value in credential[1:])
    ):
        return MFADenied("recovery_invalid", terminal=True)
    token = _opaque()
    secret = pyotp.random_base32(length=32)
    expiry = now + dt.timedelta(seconds=config.enrollment_lifetime_seconds)
    enrollment = await _insert_challenge(
        cur, user=user_id, purpose="ENROLLMENT", versions=versions, token=token,
        ttl=config.enrollment_lifetime_seconds, attempts=config.challenge_max_attempts,
        now=now, expiry=expiry,
    )
    if isinstance(enrollment, MFADenied):
        return enrollment
    await cur.execute(
        "UPDATE office_mfa_challenge SET state='CONSUMED',used_at=UTC_TIMESTAMP(6) "
        "WHERE token_hash=%s AND state='ISSUED'", (hashed,),
    )
    if cur.rowcount != 1:
        raise RuntimeError("recovery capability CAS lost")
    await cur.execute(
        "UPDATE office_mfa_credential SET state='PENDING',pending_secret_ciphertext=%s,"
        "pending_enrollment_id=%s,pending_expires_at=%s,updated_at=UTC_TIMESTAMP(6) "
        "WHERE user_id=%s AND state='RECOVERY_REQUIRED'",
        (encrypt_secret(secret), _hash(token), expiry, user_id),
    )
    if cur.rowcount != 1:
        raise RuntimeError("recovery credential CAS lost")
    return EnrollmentStart(token, secret, _utc(expiry))


async def confirm_enrollment_in_cursor(cur, *, enrollment_capability_token: str, otp: str, config: MFAConfig, server_correlation_id: bytes):
    cid = _cid(server_correlation_id)
    hashed = _hash(enrollment_capability_token)
    await cur.execute("SELECT user_id FROM office_mfa_challenge WHERE token_hash=%s", (hashed,))
    found = await cur.fetchone()
    if not found:
        return MFADenied("enrollment_invalid", terminal=True)
    user_id = int(found[0])
    versions = await _user(cur, user_id)
    await cur.execute(
        "SELECT challenge_id,purpose,expected_token_version,expected_mfa_version,"
        "attempt_count,max_attempts,expires_at,state FROM office_mfa_challenge "
        "WHERE token_hash=%s FOR UPDATE", (hashed,),
    )
    challenge = await cur.fetchone()
    now = await _db_now(cur)
    await cur.execute(
        "SELECT state,pending_secret_ciphertext,pending_enrollment_id,pending_expires_at "
        "FROM office_mfa_credential WHERE user_id=%s FOR UPDATE", (user_id,),
    )
    credential = await cur.fetchone()
    if (
        not challenge or not credential or versions is None
        or challenge[1] != "ENROLLMENT" or challenge[7] != "ISSUED"
    ):
        return MFADenied("enrollment_invalid", terminal=True)
    if challenge[6] < now:
        await cur.execute("UPDATE office_mfa_challenge SET state='EXPIRED',used_at=UTC_TIMESTAMP(6) WHERE token_hash=%s AND state='ISSUED'", (hashed,))
        return MFADenied("enrollment_expired", terminal=True)
    if versions != (int(challenge[2]), int(challenge[3])):
        await cur.execute("UPDATE office_mfa_challenge SET state='INVALIDATED',used_at=UTC_TIMESTAMP(6) WHERE token_hash=%s AND state='ISSUED'", (hashed,))
        return MFADenied("enrollment_version", terminal=True)
    if (
        not hmac.compare_digest(bytes(credential[2] or b""), hashed)
        or credential[3] is None or credential[3] < now
    ):
        return MFADenied("enrollment_invalid", terminal=True)
    secret = _decrypted_secret_or_raise(credential[1])
    counter = _otp_counter(secret, otp, now)
    if counter is None:
        return await _deny_attempt(cur, hashed, int(challenge[4]), int(challenge[5]))
    current = (versions[0] + 1, versions[1] + 1)
    try:
        await cur.execute(
            "INSERT INTO office_mfa_totp_use (user_id,mfa_version,time_counter,purpose,used_at) "
            "VALUES (%s,%s,%s,'ENROLLMENT',UTC_TIMESTAMP(6))",
            (user_id, current[1], counter),
        )
    except Exception as exc:
        if getattr(exc, "args", [None])[0] != 1062:
            raise
        return await _deny_attempt(cur, hashed, int(challenge[4]), int(challenge[5]))
    await cur.execute(
        "UPDATE office_mfa_challenge SET state='CONSUMED',used_at=UTC_TIMESTAMP(6) "
        "WHERE token_hash=%s AND state='ISSUED'", (hashed,),
    )
    if cur.rowcount != 1:
        raise RuntimeError("enrollment challenge CAS lost")
    await cur.execute(
        "UPDATE jax_users SET token_version=token_version+1,mfa_version=mfa_version+1 "
        "WHERE user_id=%s", (user_id,),
    )
    await cur.execute(
        "UPDATE office_mfa_credential SET state='ACTIVE',secret_ciphertext=pending_secret_ciphertext,"
        "pending_secret_ciphertext=NULL,pending_enrollment_id=NULL,pending_expires_at=NULL,"
        "confirmed_at=UTC_TIMESTAMP(6),updated_at=UTC_TIMESTAMP(6) WHERE user_id=%s", (user_id,),
    )
    await cur.execute("DELETE FROM office_mfa_recovery_code WHERE user_id=%s", (user_id,))
    codes = tuple(_code() for _ in range(config.recovery_code_count))
    await cur.executemany(
        "INSERT INTO office_mfa_recovery_code (user_id,mfa_version,code_hash,created_at) "
        "VALUES (%s,%s,%s,UTC_TIMESTAMP(6))",
        [(user_id, current[1], _hash(code)) for code in codes],
    )
    await _invalidate(cur, user_id)
    proof = await _append_proof(
        cur, subject=user_id, purpose="ENROLLMENT", token_version=versions[0],
        mfa_version=versions[1], principal="ENROLLMENT_CAPABILITY", actor=None,
        challenge_id=int(challenge[0]), cid=cid,
    )
    state = await _append_state(
        cur, subject=user_id, operation="ENROLLMENT_CONFIRM", purpose="ENROLLMENT",
        principal="ENROLLMENT_CAPABILITY", actor=None, challenge_id=int(challenge[0]),
        previous=versions, current=current, proof_id=proof.event_id, cid=cid,
    )
    return EnrollmentConfirmation(codes, current[0], current[1], state)


async def regenerate_recovery_codes_after_password_in_cursor(cur, *, actor_user_id: int, proof_event_id: int, config: MFAConfig, server_correlation_id: bytes):
    proof=await reload_current_self_mfa_proof_in_cursor(cur,actor_user_id=actor_user_id,proof_event_id=proof_event_id,config=config)
    if isinstance(proof,MFADenied):return proof
    if not await _active_credential(cur,actor_user_id):return MFADenied("mfa_not_active",terminal=True)
    current=(proof.token_version+1,proof.mfa_version+1)
    await cur.execute("UPDATE jax_users SET token_version=token_version+1,mfa_version=mfa_version+1 WHERE user_id=%s",(actor_user_id,))
    await cur.execute("UPDATE office_mfa_credential SET pending_secret_ciphertext=NULL,pending_enrollment_id=NULL,pending_expires_at=NULL,updated_at=UTC_TIMESTAMP(6) WHERE user_id=%s", (actor_user_id,))
    await cur.execute("DELETE FROM office_mfa_recovery_code WHERE user_id=%s",(actor_user_id,))
    await _invalidate(cur, actor_user_id)
    codes=tuple(_code() for _ in range(config.recovery_code_count))
    await cur.executemany("INSERT INTO office_mfa_recovery_code (user_id,mfa_version,code_hash,created_at) VALUES (%s,%s,%s,UTC_TIMESTAMP(6))",[(actor_user_id,current[1],_hash(c)) for c in codes])
    event=await _append_state(cur,subject=actor_user_id,operation="RECOVERY_REGENERATE",purpose="RECOVERY",principal="USER_SESSION",actor=actor_user_id,challenge_id=None,previous=(proof.token_version,proof.mfa_version),current=current,proof_id=proof.event_id,cid=_cid(server_correlation_id))
    return MFAStateReceipt(event,actor_user_id,*current)


async def disable_mfa_after_password_in_cursor(cur, *, actor_user_id: int, proof_event_id: int, config: MFAConfig, server_correlation_id: bytes):
    proof = await reload_current_self_mfa_proof_in_cursor(
        cur, actor_user_id=actor_user_id, proof_event_id=proof_event_id, config=config,
    )
    if isinstance(proof, MFADenied):
        return proof
    if not await _active_credential(cur, actor_user_id):
        return MFADenied("mfa_not_active", terminal=True)
    current = (proof.token_version + 1, proof.mfa_version + 1)
    await cur.execute(
        "UPDATE jax_users SET token_version=token_version+1,mfa_version=mfa_version+1 "
        "WHERE user_id=%s", (actor_user_id,),
    )
    await cur.execute(
        "UPDATE office_mfa_credential SET state='DISABLED',secret_ciphertext=NULL,"
        "pending_secret_ciphertext=NULL,pending_enrollment_id=NULL,pending_expires_at=NULL,"
        "confirmed_at=NULL,updated_at=UTC_TIMESTAMP(6) WHERE user_id=%s", (actor_user_id,),
    )
    await cur.execute("DELETE FROM office_mfa_recovery_code WHERE user_id=%s", (actor_user_id,))
    await _invalidate(cur, actor_user_id)
    event = await _append_state(
        cur, subject=actor_user_id, operation="MFA_DISABLE", purpose="STEP_UP",
        principal="USER_SESSION", actor=actor_user_id, challenge_id=None,
        previous=(proof.token_version, proof.mfa_version), current=current,
        proof_id=proof.event_id, cid=_cid(server_correlation_id),
    )
    return MFAStateReceipt(event, actor_user_id, *current)
