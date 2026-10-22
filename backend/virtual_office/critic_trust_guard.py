"""Private Unix-socket guard for independently signed critic evidence.

This process is intentionally not imported by the web application.  It has a
dedicated, read-only MariaDB principal and returns only verified evidence; it
does not write a ledger, create a rights verdict, or make a release decision.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import socket
import struct
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import aiomysql

from db_connect_config import db_connect_timeout_seconds
from .module_critic_receipt import CriticReceiptRejected, VerifiedCriticReceipt, verify_critic_receipt
from .module_critic_trust import assert_critic_trust_anchor_metadata, resolve_trusted_critic_key


PROTOCOL = "office-critic-trust-guard/v1"
MAX_FRAME_BYTES = 20 * 1024
SOCKET_PATH = "/run/jax/office-critic-trust-guard/guard.sock"
_NONCE_BYTES = 32
_REQUEST_KEYS = frozenset({"protocol", "operation", "nonce", "receipt"})
_SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)
_PRINCIPAL = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


class GuardConfigurationError(RuntimeError):
    """The guard cannot establish its own authority boundary."""


class GuardRejected(ValueError):
    """An untrusted request or evidence was rejected without details."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise GuardRejected("duplicate JSON member")
        value[key] = item
    return value


def _b64url32(value: object) -> str:
    if not isinstance(value, str) or len(value) != 43 or "=" in value:
        raise GuardRejected("invalid nonce")
    try:
        decoded = base64.urlsafe_b64decode(value + "=")
    except (ValueError, UnicodeEncodeError) as error:
        raise GuardRejected("invalid nonce") from error
    if len(decoded) != _NONCE_BYTES or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise GuardRejected("invalid nonce")
    return value


def parse_request(payload: bytes) -> tuple[str, dict[str, object]]:
    """Parse the exact single-request protocol; no extension fields exist."""
    if not 1 <= len(payload) <= MAX_FRAME_BYTES:
        raise GuardRejected("invalid frame")
    try:
        parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, GuardRejected) as error:
        raise GuardRejected("invalid request") from error
    if not isinstance(parsed, dict) or set(parsed) != _REQUEST_KEYS:
        raise GuardRejected("invalid request")
    if parsed["protocol"] != PROTOCOL or parsed["operation"] != "verify_evidence":
        raise GuardRejected("invalid request")
    nonce = _b64url32(parsed["nonce"])
    if not isinstance(parsed["receipt"], dict):
        raise GuardRejected("invalid request")
    return nonce, parsed["receipt"]


def encode_frame(value: dict[str, object]) -> bytes:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise GuardRejected("response cannot be encoded") from error
    if len(payload) > MAX_FRAME_BYTES:
        raise GuardRejected("response exceeds frame limit")
    return struct.pack("!I", len(payload)) + payload


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    header = await reader.readexactly(4)
    length = struct.unpack("!I", header)[0]
    if not 1 <= length <= MAX_FRAME_BYTES:
        raise GuardRejected("invalid frame")
    return await reader.readexactly(length)


def configured_principal(env: dict[str, str] | None = None) -> str:
    value = (env or os.environ).get("OFFICE_CRITIC_TRUST_GUARD_PRINCIPAL")
    if not isinstance(value, str) or _PRINCIPAL.fullmatch(value) is None:
        raise GuardConfigurationError("OFFICE_CRITIC_TRUST_GUARD_PRINCIPAL is required")
    return value


def allowed_peer_uids(env: dict[str, str] | None = None) -> frozenset[int]:
    raw = (env or os.environ).get("OFFICE_CRITIC_TRUST_GUARD_ALLOWED_UIDS")
    if not raw:
        raise GuardConfigurationError("OFFICE_CRITIC_TRUST_GUARD_ALLOWED_UIDS is required")
    values = raw.split(",")
    if not values or any(not item.isascii() or not item.isdecimal() for item in values):
        raise GuardConfigurationError("OFFICE_CRITIC_TRUST_GUARD_ALLOWED_UIDS is invalid")
    result = frozenset(int(item) for item in values)
    if not result:
        raise GuardConfigurationError("OFFICE_CRITIC_TRUST_GUARD_ALLOWED_UIDS is invalid")
    return result


def configured_timeout_seconds(env: dict[str, str] | None = None) -> float:
    raw = (env or os.environ).get("OFFICE_CRITIC_TRUST_GUARD_TIMEOUT_SECONDS")
    try:
        value = float(raw) if raw is not None else 0.0
    except ValueError:
        value = 0.0
    if not 0 < value <= 30:
        raise GuardConfigurationError("OFFICE_CRITIC_TRUST_GUARD_TIMEOUT_SECONDS is invalid")
    return value


def peer_uid(sock: socket.socket) -> int:
    try:
        value = sock.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", value)
    except (OSError, struct.error) as error:
        raise GuardRejected("peer credentials unavailable") from error
    if pid <= 0 or uid < 0 or gid < 0:
        raise GuardRejected("peer credentials unavailable")
    return uid


def _utc_database(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise GuardRejected("database clock unavailable")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def _connect_readonly() -> aiomysql.Connection:
    required = ("JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_USER", "JAX_DB_PASSWORD", "JAX_DB_NAME")
    if any(not os.environ.get(name) for name in required):
        raise GuardConfigurationError("guard database configuration is incomplete")
    return await aiomysql.connect(
        host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
        user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=os.environ["JAX_DB_NAME"],
        charset="utf8mb4", autocommit=False, connect_timeout=db_connect_timeout_seconds(),
    )


def _evidence(verified: VerifiedCriticReceipt) -> dict[str, object]:
    """Only candidate bindings, never the public key or original envelope."""
    return {
        "invocation_ref": verified.invocation_ref,
        "candidate_id": verified.candidate_id,
        "candidate_revision_id": verified.candidate_revision_id,
        "manifest_sha256": "sha256:" + verified.manifest_sha256.hex(),
        "artifact_set_digest": "sha256:" + verified.artifact_set_digest.hex(),
        "decision": verified.decision,
        "key_id": verified.key_id,
        "critic_principal": verified.critic_principal,
        "canonical_sha256": "sha256:" + verified.canonical_sha256.hex(),
    }


async def verify_evidence_in_transaction(
    receipt: object, *, principal: str, connect: Callable[[], Awaitable[Any]] = _connect_readonly,
) -> dict[str, object]:
    """Attest schema and verify once under a read-only, MDL-held transaction."""
    conn = await connect()
    try:
        async with conn.cursor() as cur:
            await cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            await cur.execute("START TRANSACTION READ ONLY")
            try:
                # These two table opens retain MariaDB MDL_SHARED_READ through
                # the transaction.  The row locks make the intended key and
                # tombstone lookup explicit without allowing a write.
                await cur.execute("SELECT key_id FROM office_trusted_critic_key WHERE key_id='' LOCK IN SHARE MODE")
                await cur.execute("SELECT key_id FROM office_trusted_critic_key_revocation WHERE key_id='' LOCK IN SHARE MODE")
                await assert_critic_trust_anchor_metadata(cur)
                await cur.execute("SELECT UTC_TIMESTAMP(6)")
                row = await cur.fetchone()
                now = _utc_database((tuple(row.values()) if isinstance(row, dict) else tuple(row))[0])
                requested_key_id = receipt.get("key_id") if isinstance(receipt, dict) else ""
                trusted_key = await resolve_trusted_critic_key(cur, requested_key_id)
                verified = verify_critic_receipt(
                    receipt,
                    lambda key_id: trusted_key if trusted_key is not None and key_id == trusted_key.key_id else None,
                    expected_principal=principal,
                    now=now,
                )
                # READ COMMITTED gives this a fresh statement snapshot.  A
                # tombstone committed during verification therefore rejects
                # the evidence before the transaction releases its MDL.
                await cur.execute(
                    "SELECT revoked_at FROM office_trusted_critic_key_revocation WHERE key_id=%s",
                    (trusted_key.key_id,),
                )
                if await cur.fetchone() is not None:
                    raise GuardRejected("key revoked")
            except Exception:
                await conn.rollback()
                raise
            await conn.rollback()
    finally:
        conn.close()
    return _evidence(verified)


class GuardServer:
    """One bounded request per Unix connection, authenticated by SO_PEERCRED."""

    def __init__(
        self, *, principal: str | None = None, allowed_uids: frozenset[int] | None = None,
        timeout_seconds: float | None = None,
        verifier: Callable[[object], Awaitable[dict[str, object]]] | None = None,
    ) -> None:
        self._principal = principal if principal is not None else configured_principal()
        self._allowed_uids = allowed_uids if allowed_uids is not None else allowed_peer_uids()
        timeout_seconds = configured_timeout_seconds() if timeout_seconds is None else timeout_seconds
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 30:
            raise GuardConfigurationError("guard timeout is invalid")
        self._timeout_seconds = float(timeout_seconds)
        self._verifier = verifier or (lambda receipt: verify_evidence_in_transaction(receipt, principal=self._principal))

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonce: str | None = None
        try:
            raw_socket = writer.get_extra_info("socket")
            if raw_socket is None or peer_uid(raw_socket) not in self._allowed_uids:
                raise GuardRejected("untrusted peer")
            payload = await asyncio.wait_for(read_frame(reader), timeout=self._timeout_seconds)
            nonce, receipt = parse_request(payload)
            evidence = await asyncio.wait_for(self._verifier(receipt), timeout=self._timeout_seconds)
            response: dict[str, object] = {"protocol": PROTOCOL, "nonce": nonce, "status": "VERIFIED_EVIDENCE", "evidence": evidence}
        except (GuardRejected, CriticReceiptRejected):
            response = {"protocol": PROTOCOL, "nonce": nonce, "status": "REJECTED"}
        except (asyncio.TimeoutError, GuardConfigurationError, OSError, RuntimeError, ValueError):
            response = {"protocol": PROTOCOL, "nonce": nonce, "status": "UNAVAILABLE"}
        try:
            writer.write(encode_frame(response))
            await asyncio.wait_for(writer.drain(), timeout=self._timeout_seconds)
        except (OSError, asyncio.TimeoutError, GuardRejected):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def run_guard(*, path: str = SOCKET_PATH, server: GuardServer | None = None) -> None:
    """Bind the fixed private socket. Deployment owns the RuntimeDirectory."""
    if path != SOCKET_PATH:
        raise GuardConfigurationError("guard socket path is fixed")
    guard = server or GuardServer()
    async with await asyncio.start_unix_server(guard.handle, path=path) as listener:
        await listener.serve_forever()


def main() -> None:
    asyncio.run(run_guard())


if __name__ == "__main__":
    main()
