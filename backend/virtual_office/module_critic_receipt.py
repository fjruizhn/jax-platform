"""Pure verifier for an independently signed ``rights.review`` envelope.

This module deliberately has no database or network side effect.  A verified
envelope is input evidence; a later Office authority contract decides whether
and how it may become durable or effective.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .module_critic_trust import TrustedCriticKey

_DOMAIN = "axioma.office.critic-invocation-receipt"
_KEY = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_INVOCATION = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_MAX_CANONICAL_BYTES = 16 * 1024
_MAX_SIGNATURE_BASE64_BYTES = 128
_MAX_VALIDITY = timedelta(seconds=300)


class CriticReceiptRejected(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedCriticReceipt:
    invocation_ref: str
    candidate_id: int
    candidate_revision_id: int
    manifest_sha256: bytes
    artifact_set_digest: bytes
    decision: str
    key_id: str
    critic_principal: str
    issued_at: datetime
    expires_at: datetime
    canonical_bytes: bytes
    signature: bytes
    canonical_sha256: bytes


def _reject(message: str) -> None:
    raise CriticReceiptRejected(message)


def _canonical(value: dict[str, Any]) -> bytes:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise CriticReceiptRejected("critic receipt cannot be canonicalized") from error
    if len(encoded) > _MAX_CANONICAL_BYTES:
        _reject("critic receipt exceeds canonical size limit")
    return encoded


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _reject(f"critic receipt {field} is not UTC RFC3339")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise CriticReceiptRejected(f"critic receipt {field} is invalid") from error
    if parsed.tzinfo != timezone.utc or parsed.isoformat().replace("+00:00", "Z") != value:
        _reject(f"critic receipt {field} is not canonical UTC")
    return parsed


def _digest(value: object, field: str) -> bytes:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _reject(f"critic receipt {field} is invalid")
    return bytes.fromhex(value[7:])


def verify_critic_receipt(receipt: object, resolve_trusted_key: Callable[[str], TrustedCriticKey | None], *, expected_principal: str, now: datetime) -> VerifiedCriticReceipt:
    """Verify a domain-separated critic envelope using an injected resolver.

    No fallback key source exists.  The caller names the critic principal it
    expects; a key for another principal never becomes interchangeable.
    """
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        _reject("verification clock is not UTC")
    if not isinstance(expected_principal, str) or _KEY.fullmatch(expected_principal) is None:
        _reject("expected critic principal is invalid")
    required = {"domain", "schema_version", "operation", "invocation_ref", "candidate", "decision", "issued_at", "expires_at", "key_id", "signature"}
    if not isinstance(receipt, dict) or set(receipt) != required:
        _reject("critic receipt shape is invalid")
    unsigned = {key: value for key, value in receipt.items() if key != "signature"}
    if unsigned["domain"] != _DOMAIN or type(unsigned["schema_version"]) is not int or unsigned["schema_version"] != 1 or unsigned["operation"] != "rights.review":
        _reject("critic receipt contract is invalid")
    invocation_ref, key_id = unsigned["invocation_ref"], unsigned["key_id"]
    if not isinstance(invocation_ref, str) or _INVOCATION.fullmatch(invocation_ref) is None or not isinstance(key_id, str) or _KEY.fullmatch(key_id) is None:
        _reject("critic receipt identity is invalid")
    candidate = unsigned["candidate"]
    if not isinstance(candidate, dict) or set(candidate) != {"candidate_id", "candidate_revision_id", "manifest_sha256", "artifact_set_digest"}:
        _reject("critic receipt candidate binding is invalid")
    if any(type(candidate[name]) is not int or candidate[name] < 1 for name in ("candidate_id", "candidate_revision_id")):
        _reject("critic receipt candidate identifiers are invalid")
    manifest_sha256 = _digest(candidate["manifest_sha256"], "manifest digest")
    artifact_set_digest = _digest(candidate["artifact_set_digest"], "artifact digest")
    if not isinstance(unsigned["decision"], str) or unsigned["decision"] not in {"PASS", "REVIEW_REQUIRED", "FAIL"}:
        _reject("critic receipt decision is invalid")
    issued_at, expires_at = _utc(unsigned["issued_at"], "issued_at"), _utc(unsigned["expires_at"], "expires_at")
    if expires_at <= issued_at or expires_at - issued_at > _MAX_VALIDITY or now < issued_at or now >= expires_at:
        _reject("critic receipt is outside its validity window")
    encoded_signature = receipt["signature"]
    if not isinstance(encoded_signature, str) or len(encoded_signature) > _MAX_SIGNATURE_BASE64_BYTES:
        _reject("critic receipt signature encoding is invalid")
    try:
        signature = base64.b64decode(encoded_signature, validate=True)
    except ValueError as error:
        raise CriticReceiptRejected("critic receipt signature encoding is invalid") from error
    if len(signature) != 64:
        _reject("critic receipt signature length is invalid")
    try:
        trusted_key = resolve_trusted_key(key_id)
    except Exception as error:
        raise CriticReceiptRejected("critic receipt signing key is unavailable") from error
    if not isinstance(trusted_key, TrustedCriticKey):
        _reject("critic receipt signing key is untrusted")
    if (
        trusted_key.key_id != key_id
        or trusted_key.principal_ref != expected_principal
        or trusted_key.purpose != "rights.review"
        or trusted_key.algorithm != "Ed25519"
        or not isinstance(trusted_key.public_key, bytes) or len(trusted_key.public_key) != 32
        or trusted_key.revoked_at is not None
        or not isinstance(trusted_key.not_before, datetime)
        or trusted_key.not_before.tzinfo is None or trusted_key.not_before.utcoffset() != timedelta(0)
        or (trusted_key.not_after is not None and (
            not isinstance(trusted_key.not_after, datetime)
            or trusted_key.not_after.tzinfo is None or trusted_key.not_after.utcoffset() != timedelta(0)
            or trusted_key.not_before >= trusted_key.not_after or expires_at > trusted_key.not_after
        ))
        or issued_at < trusted_key.not_before
    ):
        _reject("critic receipt signing key is untrusted")
    canonical = _canonical(unsigned)
    try:
        Ed25519PublicKey.from_public_bytes(trusted_key.public_key).verify(signature, canonical)
    except (ValueError, InvalidSignature) as error:
        raise CriticReceiptRejected("critic receipt signature is invalid") from error
    return VerifiedCriticReceipt(invocation_ref, candidate["candidate_id"], candidate["candidate_revision_id"], manifest_sha256, artifact_set_digest, unsigned["decision"], key_id, trusted_key.principal_ref, issued_at, expires_at, canonical, signature, hashlib.sha256(canonical).digest())
