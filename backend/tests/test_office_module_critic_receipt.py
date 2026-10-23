import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from virtual_office.module_critic_receipt import CriticReceiptRejected, verify_critic_receipt


def _receipt(private, *, lifetime=300):
    issued = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    unsigned = {"domain": "axioma.office.critic-invocation-receipt", "schema_version": 1, "operation": "rights.review", "invocation_ref": "critic:case-1", "candidate": {"candidate_id": 7, "candidate_revision_id": 9, "manifest_sha256": "sha256:" + "11" * 32, "artifact_set_digest": "sha256:" + "22" * 32}, "decision": "PASS", "issued_at": issued.isoformat().replace("+00:00", "Z"), "expires_at": (issued + timedelta(seconds=lifetime)).isoformat().replace("+00:00", "Z"), "key_id": "critic-test-v1"}
    signed = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {**unsigned, "signature": base64.b64encode(private.sign(signed)).decode()}, issued


def _resolver(private):
    return lambda key_id: private.public_key().public_bytes_raw() if key_id == "critic-test-v1" else None


def test_valid_receipt_is_exactly_bound_and_canonical() -> None:
    private = Ed25519PrivateKey.generate(); receipt, now = _receipt(private)
    verified = verify_critic_receipt(receipt, _resolver(private), now=now)
    assert (verified.candidate_id, verified.candidate_revision_id, verified.decision) == (7, 9, "PASS")
    assert verified.manifest_sha256 == bytes.fromhex("11" * 32)
    assert verified.canonical_bytes.startswith(b'{"candidate"')


@pytest.mark.parametrize("change", ["bad_signature", "unknown_key", "changed_root", "wrong_operation", "bool_version", "too_long", "expired", "future", "ttl", "decision_list", "decision_object"])
def test_negative_envelopes_fail_closed(change: str) -> None:
    private = Ed25519PrivateKey.generate(); receipt, now = _receipt(private)
    resolver = _resolver(private)
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
    with pytest.raises(CriticReceiptRejected): verify_critic_receipt(receipt, resolver, now=now)


def test_unavailable_resolver_and_non_utc_clock_fail_closed() -> None:
    private = Ed25519PrivateKey.generate(); receipt, now = _receipt(private)
    with pytest.raises(CriticReceiptRejected): verify_critic_receipt(receipt, lambda _: (_ for _ in ()).throw(RuntimeError()), now=now)
    with pytest.raises(CriticReceiptRejected): verify_critic_receipt(receipt, _resolver(private), now=datetime(2026, 10, 22, 12, 0))
