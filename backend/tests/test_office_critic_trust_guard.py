"""Protocol tests for the private critic-trust guard; no MariaDB is contacted."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from virtual_office import critic_trust_guard as guard
from virtual_office.critic_trust_guard import (
    GuardConfigurationError,
    GuardRejected,
    GuardServer,
    GUARD_READY,
    MAX_FRAME_BYTES,
    PROTOCOL,
    allowed_peer_uids,
    configured_timeout_seconds,
    encode_frame,
    parse_request,
)
from virtual_office.module_critic_receipt import CriticReceiptRejected
from virtual_office.module_critic_trust import TrustedCriticKey


def _nonce() -> str:
    return base64.urlsafe_b64encode(b"n" * 32).decode("ascii").rstrip("=")


def _request(**overrides):
    value = {"protocol": PROTOCOL, "operation": "verify_evidence", "nonce": _nonce(), "receipt": {"opaque": "evidence"}}
    value.update(overrides)
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def test_protocol_is_exact_bounded_and_rejects_duplicates() -> None:
    nonce, receipt = parse_request(_request())
    assert nonce == _nonce() and receipt == {"opaque": "evidence"}
    for payload in (
        _request(extra=True),
        b'{"protocol":"' + PROTOCOL.encode() + b'","protocol":"' + PROTOCOL.encode() + b'"}',
        _request(nonce="A" * 42),
        b"x" * (MAX_FRAME_BYTES + 1),
    ):
        with pytest.raises(GuardRejected):
            parse_request(payload)


def test_frame_encoding_is_bounded_and_network_ordered() -> None:
    framed = encode_frame({"status": "REJECTED"})
    assert struct.unpack("!I", framed[:4])[0] == len(framed) - 4
    with pytest.raises(GuardRejected):
        encode_frame({"x": "x" * (MAX_FRAME_BYTES + 1)})


def test_allowed_peer_uid_configuration_is_strict() -> None:
    assert allowed_peer_uids({"OFFICE_CRITIC_TRUST_GUARD_ALLOWED_UIDS": "1,42"}) == frozenset({1, 42})
    for raw in ("", "1,", "1,-2", " 1", "١"):
        with pytest.raises(GuardConfigurationError):
            allowed_peer_uids({"OFFICE_CRITIC_TRUST_GUARD_ALLOWED_UIDS": raw})


def test_guard_deadline_is_explicit_and_bounded() -> None:
    assert configured_timeout_seconds({"OFFICE_CRITIC_TRUST_GUARD_TIMEOUT_SECONDS": "2.5"}) == 2.5
    for raw in (None, "0", "-1", "31", "nan", "forever"):
        with pytest.raises(GuardConfigurationError):
            configured_timeout_seconds({} if raw is None else {"OFFICE_CRITIC_TRUST_GUARD_TIMEOUT_SECONDS": raw})


def test_guard_is_explicitly_inert_pending_the_activation_contract() -> None:
    assert GUARD_READY is False
    assert guard.guard_readiness() == {"status": "INERT_CONTRACT_PENDING"}
    with pytest.raises(GuardConfigurationError, match="inert"):
        asyncio.run(guard.run_guard())


def test_inert_systemd_contract_cannot_be_seen_by_the_restore_unit_glob() -> None:
    root = Path(__file__).resolve().parents[2]
    assert not (root / "ops/migration/systemd-units/office-critic-trust-guard.service").exists()
    assert (root / "ops/virtualoffice/contracts/office-critic-trust-guard.systemd-contract.txt").is_file()


def test_private_socket_uses_peer_credentials_and_never_exposes_keys(tmp_path) -> None:
    async def scenario() -> None:
        path = str(tmp_path / "guard.sock")

        async def verifier(receipt):
            assert receipt == {"opaque": "evidence"}
            return {"candidate_id": 7, "key_id": "kid", "canonical_sha256": "sha256:" + "a" * 64}

        guard = GuardServer(principal="agent:critic", allowed_uids=frozenset({os.getuid()}), timeout_seconds=1, verifier=verifier)
        listener = await asyncio.start_unix_server(guard.handle, path=path)
        try:
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(struct.pack("!I", len(_request())) + _request())
            await writer.drain()
            length = struct.unpack("!I", await reader.readexactly(4))[0]
            result = json.loads(await reader.readexactly(length))
            assert result["status"] == "VERIFIED_EVIDENCE"
            assert result["nonce"] == _nonce()
            assert "public_key" not in json.dumps(result)
            writer.close(); await writer.wait_closed()
        finally:
            listener.close(); await listener.wait_closed()

    asyncio.run(scenario())


def test_rejected_receipt_has_no_error_or_evidence(tmp_path) -> None:
    async def scenario() -> None:
        path = str(tmp_path / "guard.sock")

        async def verifier(receipt):
            raise CriticReceiptRejected("signature invalid")

        guard = GuardServer(principal="agent:critic", allowed_uids=frozenset({os.getuid()}), timeout_seconds=1, verifier=verifier)
        listener = await asyncio.start_unix_server(guard.handle, path=path)
        try:
            reader, writer = await asyncio.open_unix_connection(path)
            request = _request()
            writer.write(struct.pack("!I", len(request)) + request); await writer.drain()
            length = struct.unpack("!I", await reader.readexactly(4))[0]
            result = json.loads(await reader.readexactly(length))
            assert result == {"nonce": _nonce(), "protocol": PROTOCOL, "status": "REJECTED"}
            writer.close(); await writer.wait_closed()
        finally:
            listener.close(); await listener.wait_closed()

    asyncio.run(scenario())


def test_internal_verifier_failure_is_unavailable(tmp_path) -> None:
    async def scenario() -> None:
        path = str(tmp_path / "guard.sock")

        async def verifier(receipt):
            raise ConnectionError("database gone")

        guard = GuardServer(principal="agent:critic", allowed_uids=frozenset({os.getuid()}), timeout_seconds=1, verifier=verifier)
        listener = await asyncio.start_unix_server(guard.handle, path=path)
        try:
            reader, writer = await asyncio.open_unix_connection(path)
            request = _request()
            writer.write(struct.pack("!I", len(request)) + request); await writer.drain()
            length = struct.unpack("!I", await reader.readexactly(4))[0]
            assert json.loads(await reader.readexactly(length)) == {"nonce": _nonce(), "protocol": PROTOCOL, "status": "UNAVAILABLE"}
            writer.close(); await writer.wait_closed()
        finally:
            listener.close(); await listener.wait_closed()

    asyncio.run(scenario())


def test_db_guard_holds_transaction_attests_then_rechecks_tombstone(monkeypatch) -> None:
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    private = Ed25519PrivateKey.generate()
    unsigned = {
        "domain": "axioma.office.critic-invocation-receipt", "schema_version": 1,
        "operation": "rights.review", "invocation_ref": "critic:case-1",
        "candidate": {"candidate_id": 7, "candidate_revision_id": 9, "manifest_sha256": "sha256:" + "11" * 32, "artifact_set_digest": "sha256:" + "22" * 32},
        "decision": "PASS", "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(seconds=60)).isoformat().replace("+00:00", "Z"), "key_id": "kid",
    }
    receipt = {**unsigned, "signature": base64.b64encode(private.sign(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode())).decode()}
    key = TrustedCriticKey("kid", "agent:critic", "rights.review", "Ed25519", private.public_key().public_bytes_raw(), now - timedelta(seconds=1), now + timedelta(days=1), None)
    events: list[str] = []

    class Cursor:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def execute(self, sql, params=None): events.append(sql)
        async def fetchone(self):
            if events[-1] == "SELECT UTC_TIMESTAMP(6)": return (now.replace(tzinfo=None),)
            if "revocation" in events[-1]: return None
            return None

    class Conn:
        def cursor(self): return Cursor()
        async def rollback(self): events.append("ROLLBACK")
        def close(self): events.append("CLOSE")

    async def attest(cur): events.append("ATTEST")
    async def resolve(cur, key_id): events.append("RESOLVE"); return key if key_id == "kid" else None
    async def connect(): return Conn()
    monkeypatch.setattr(guard, "assert_critic_trust_anchor_metadata", attest)
    monkeypatch.setattr(guard, "_load_trusted_key_for_guard", resolve)
    evidence = asyncio.run(guard.verify_evidence_in_transaction(receipt, principal="agent:critic", connect=connect))
    assert evidence["candidate_id"] == 7
    assert events.index("ATTEST") > 3
    assert events.index("RESOLVE") > events.index("ATTEST")
    assert "LOCK IN SHARE MODE" in events[2] and "LOCK IN SHARE MODE" in events[3]
    assert "FROM office_trusted_critic_key_revocation" in events[-3]
    assert events[-2:] == ["ROLLBACK", "CLOSE"]
