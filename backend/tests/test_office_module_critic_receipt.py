import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from virtual_office.module_critic_receipt import CriticReceiptRejected, verify_critic_receipt
from virtual_office.module_critic_trust import TrustedCriticKey


def _receipt(private, *, lifetime=300):
    issued = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    unsigned = {"domain": "axioma.office.critic-invocation-receipt", "schema_version": 1, "operation": "rights.review", "invocation_ref": "critic:case-1", "candidate": {"candidate_id": 7, "candidate_revision_id": 9, "manifest_sha256": "sha256:" + "11" * 32, "artifact_set_digest": "sha256:" + "22" * 32}, "decision": "PASS", "issued_at": issued.isoformat().replace("+00:00", "Z"), "expires_at": (issued + timedelta(seconds=lifetime)).isoformat().replace("+00:00", "Z"), "key_id": "critic-test-v1"}
    signed = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {**unsigned, "signature": base64.b64encode(private.sign(signed)).decode()}, issued


def _resolver(private):
    return lambda key_id: private.public_key().public_bytes_raw() if key_id == "critic-test-v1" else None


def _trusted_resolver(private, *, principal="agent:critic", purpose="rights.review", algorithm="Ed25519",
                      revoked_at=None, not_before=None, not_after=None, nonexpiring=False):
    key = TrustedCriticKey(
        key_id="critic-test-v1", principal_ref=principal, purpose=purpose, algorithm=algorithm,
        public_key=private.public_key().public_bytes_raw(),
        not_before=not_before or datetime(2026, 10, 22, 11, 55, tzinfo=timezone.utc),
        not_after=None if nonexpiring else (not_after or datetime(2026, 10, 22, 12, 10, tzinfo=timezone.utc)),
        revoked_at=revoked_at,
    )
    return lambda requested_key_id: key if requested_key_id == key.key_id else None


def test_valid_receipt_is_exactly_bound_and_canonical() -> None:
    private = Ed25519PrivateKey.generate(); receipt, now = _receipt(private)
    verified = verify_critic_receipt(receipt, _trusted_resolver(private), expected_principal="agent:critic", now=now)
    assert (verified.candidate_id, verified.candidate_revision_id, verified.decision) == (7, 9, "PASS")
    assert verified.manifest_sha256 == bytes.fromhex("11" * 32)
    assert verified.canonical_bytes.startswith(b'{"candidate"')


def test_trusted_receipt_requires_expected_principal_and_records_it() -> None:
    private = Ed25519PrivateKey.generate(); receipt, now = _receipt(private)
    verified = verify_critic_receipt(
        receipt, _trusted_resolver(private), expected_principal="agent:critic", now=now,
    )
    assert verified.critic_principal == "agent:critic"


def test_trusted_receipt_can_use_a_nonexpiring_anchor() -> None:
    private = Ed25519PrivateKey.generate(); receipt, now = _receipt(private)
    verified = verify_critic_receipt(
        receipt, _trusted_resolver(private, nonexpiring=True), expected_principal="agent:critic", now=now,
    )
    assert verified.critic_principal == "agent:critic"


@pytest.mark.parametrize("change", ["wrong_principal", "wrong_purpose", "wrong_algorithm", "revoked", "issued_before_key", "expires_after_key"])
def test_trust_anchor_rejects_identity_lifetime_and_revocation_mismatches(change: str) -> None:
    private = Ed25519PrivateKey.generate(); receipt, now = _receipt(private)
    kwargs = {}
    if change == "wrong_principal":
        kwargs["principal"] = "agent:other"
    elif change == "wrong_purpose":
        kwargs["purpose"] = "release.publish"
    elif change == "wrong_algorithm":
        kwargs["algorithm"] = "RSA"
    elif change == "revoked":
        kwargs["revoked_at"] = now + timedelta(days=1)
    elif change == "issued_before_key":
        kwargs["not_before"] = now + timedelta(seconds=1)
    else:
        kwargs["not_after"] = now + timedelta(seconds=1)
    with pytest.raises(CriticReceiptRejected):
        verify_critic_receipt(receipt, _trusted_resolver(private, **kwargs), expected_principal="agent:critic", now=now)


@pytest.mark.parametrize("change", ["bad_signature", "unknown_key", "changed_root", "wrong_operation", "bool_version", "too_long", "expired", "future", "ttl", "decision_list", "decision_object"])
def test_negative_envelopes_fail_closed(change: str) -> None:
    private = Ed25519PrivateKey.generate(); receipt, now = _receipt(private)
    resolver = _trusted_resolver(private)
    if change == "bad_signature": receipt["signature"] = base64.b64encode(b"x" * 64).decode()
    elif change == "unknown_key": receipt["key_id"] = "missing"
    elif change == "changed_root": receipt["candidate"]["artifact_set_digest"] = "sha256:" + "33" * 32
    elif change == "wrong_operation": receipt["operation"] = "release.publish"
    elif change == "bool_version": receipt["schema_version"] = True
    elif change == "too_long": receipt["signature"] = "A" * 129
    elif change == "expired": now += timedelta(seconds=300)
    elif change == "future": now -= timedelta(seconds=1)
    elif change == "decision_list": receipt["decision"] = []
    elif change == "decision_object": receipt["decision"] = {}
    else:
        receipt, now = _receipt(private, lifetime=301)
    with pytest.raises(CriticReceiptRejected): verify_critic_receipt(receipt, resolver, expected_principal="agent:critic", now=now)


def test_unavailable_resolver_and_non_utc_clock_fail_closed() -> None:
    private = Ed25519PrivateKey.generate(); receipt, now = _receipt(private)
    with pytest.raises(CriticReceiptRejected): verify_critic_receipt(receipt, lambda _: (_ for _ in ()).throw(RuntimeError()), expected_principal="agent:critic", now=now)
    with pytest.raises(CriticReceiptRejected): verify_critic_receipt(receipt, _trusted_resolver(private), expected_principal="agent:critic", now=datetime(2026, 10, 22, 12, 0))
