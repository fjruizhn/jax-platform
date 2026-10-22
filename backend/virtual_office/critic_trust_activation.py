"""Descriptor-bound, inert activation-contract reader for the critic guard.

This module establishes only the filesystem and JSON boundary for a future
activation.  It deliberately neither changes ``GUARD_READY`` nor starts the
guard.  A later, separately approved activation must consume a successfully
revalidated :class:`ActivationContract`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ACTIVATION_CONTRACT_PATH = Path("/etc/jax/office-critic-trust-activation-v1.json")
MAX_CONTRACT_BYTES = 8 * 1024
DOMAIN = "axioma.office"
VERSION = 1
GUARD_PROTOCOL = "office-critic-trust-guard/v1"
PURPOSE = "rights.review"
ALGORITHM = "Ed25519"
MAX_FRAME_BYTES = 20 * 1024
_USER = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_TOP_KEYS = frozenset({
    "algorithm", "allowed_peer_uids", "database_user", "domain", "guard_gid",
    "guard_protocol", "guard_uid", "limits", "purpose", "trust_definer_user", "version",
})
_LIMIT_KEYS = frozenset({"max_frame_bytes", "max_inflight", "timeout_seconds"})


class ActivationContractError(RuntimeError):
    """The root-owned activation input does not establish the required boundary."""


class ActivationContractChanged(ActivationContractError):
    """The file named by an already validated contract no longer has its identity."""


@dataclass(frozen=True)
class ContractFingerprint:
    device: int
    inode: int
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class ActivationContract:
    guard_uid: int
    guard_gid: int
    allowed_peer_uids: tuple[int, ...]
    database_user: str
    trust_definer_user: str
    timeout_seconds: int
    max_inflight: int
    path: Path
    fingerprint: ContractFingerprint

    def assert_unchanged(self) -> None:
        """Invalidate this object when locator metadata or bytes have changed."""
        replacement = load_activation_contract(self.path)
        if replacement.fingerprint != self.fingerprint:
            raise ActivationContractChanged("critic-trust activation contract changed after validation")


def _reject(message: str) -> None:
    raise ActivationContractError(message)


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _reject("activation contract has duplicate JSON member")
        result[key] = value
    return result


def _no_float(_: str) -> object:
    _reject("activation contract does not permit floating-point numbers")


def _no_constant(_: str) -> object:
    _reject("activation contract does not permit JSON constants")


def _positive_int(value: object, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        _reject(f"activation contract {name} is invalid")
    return value


def _user(value: object, name: str) -> str:
    if not isinstance(value, str) or _USER.fullmatch(value) is None:
        _reject(f"activation contract {name} is invalid")
    return value


def _canonical(value: dict[str, object]) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ActivationContractError("activation contract cannot be canonicalized") from error


def _parse_contract(raw: bytes) -> ActivationContract:
    if not raw or len(raw) > MAX_CONTRACT_BYTES or raw.startswith(b"\xef\xbb\xbf"):
        _reject("activation contract bytes are invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_strict_object,
            parse_float=_no_float, parse_constant=_no_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ActivationContractError) as error:
        raise ActivationContractError("activation contract JSON is invalid") from error
    if not isinstance(value, dict) or set(value) != _TOP_KEYS:
        _reject("activation contract keys are invalid")
    if raw != _canonical(value):
        _reject("activation contract is not canonical JSON")
    if value["domain"] != DOMAIN or value["version"] != VERSION:
        _reject("activation contract domain or version is invalid")
    if value["guard_protocol"] != GUARD_PROTOCOL or value["purpose"] != PURPOSE or value["algorithm"] != ALGORITHM:
        _reject("activation contract protocol constants are invalid")
    guard_uid = _positive_int(value["guard_uid"], "guard_uid", 2**31 - 1)
    guard_gid = _positive_int(value["guard_gid"], "guard_gid", 2**31 - 1)
    database_user = _user(value["database_user"], "database_user")
    trust_definer_user = _user(value["trust_definer_user"], "trust_definer_user")
    if database_user == trust_definer_user:
        _reject("activation contract database user and definer must differ")
    peer_uids = value["allowed_peer_uids"]
    if not isinstance(peer_uids, list) or not 1 <= len(peer_uids) <= 16:
        _reject("activation contract allowed_peer_uids is invalid")
    typed_uids = tuple(_positive_int(item, "allowed_peer_uids", 2**31 - 1) for item in peer_uids)
    if tuple(sorted(typed_uids)) != typed_uids or len(set(typed_uids)) != len(typed_uids):
        _reject("activation contract allowed_peer_uids is not canonical")
    limits = value["limits"]
    if not isinstance(limits, dict) or set(limits) != _LIMIT_KEYS:
        _reject("activation contract limits are invalid")
    timeout = _positive_int(limits["timeout_seconds"], "timeout_seconds", 30)
    max_inflight = _positive_int(limits["max_inflight"], "max_inflight", 32)
    if limits["max_frame_bytes"] != MAX_FRAME_BYTES:
        _reject("activation contract max_frame_bytes is invalid")
    # ``path`` and ``fingerprint`` are bound only after descriptor-safe I/O.
    return ActivationContract(guard_uid, guard_gid, typed_uids, database_user, trust_definer_user, timeout, max_inflight, Path(), ContractFingerprint(0, 0, 0, 0, ""))


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _assert_file_metadata(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _reject("activation contract must be a single regular file")
    if info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o644:
        _reject("activation contract must be root:root mode 0644")
    if not 1 <= info.st_size <= MAX_CONTRACT_BYTES:
        _reject("activation contract size is invalid")


def _assert_parent_chain(path: Path) -> None:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        _reject("activation contract path is invalid")
    current = path.parent
    while True:
        try:
            info = os.lstat(current)
        except OSError as error:
            raise ActivationContractError("activation contract parent cannot be inspected") from error
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            _reject("activation contract parent is not a real directory")
        if info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            _reject("activation contract parent is not root-owned private administration")
        if current == current.parent:
            return
        current = current.parent


def _read_bound_bytes(path: Path) -> tuple[bytes, ContractFingerprint]:
    _assert_parent_chain(path)
    fd: int | None = None
    try:
        before = os.lstat(path)
        _assert_file_metadata(before)
        fd = os.open(path, _FILE_FLAGS)
        opened = os.fstat(fd)
        _assert_file_metadata(opened)
        if _file_identity(before) != _file_identity(opened):
            _reject("activation contract changed before read")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                _reject("activation contract changed during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        named = os.lstat(path)
        if _file_identity(opened) != _file_identity(after) or _file_identity(opened) != _file_identity(named):
            _reject("activation contract changed during read")
        return raw, ContractFingerprint(opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, hashlib.sha256(raw).hexdigest())
    except OSError as error:
        raise ActivationContractError("activation contract cannot be opened safely") from error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def load_activation_contract(path: Path = ACTIVATION_CONTRACT_PATH) -> ActivationContract:
    """Read the fixed root-owned contract, without activating any capability."""
    locator = Path(path)
    raw, fingerprint = _read_bound_bytes(locator)
    parsed = _parse_contract(raw)
    return ActivationContract(
        parsed.guard_uid, parsed.guard_gid, parsed.allowed_peer_uids, parsed.database_user,
        parsed.trust_definer_user, parsed.timeout_seconds, parsed.max_inflight, locator, fingerprint,
    )
