"""Descriptor-bound, inert activation-contract reader for the critic guard.

This module establishes only the filesystem and JSON boundary for a future
activation.  It deliberately neither changes ``GUARD_READY`` nor starts the
guard.  A later, separately approved activation must consume a successfully
revalidated :class:`ActivationContract`.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path


ACTIVATION_CONTRACT_PATH = Path("/etc/jax/office-critic-trust-activation-v1.json")
MAX_CONTRACT_BYTES = 8 * 1024
DOMAIN = "axioma.office.critic-trust-activation"
SCHEMA_VERSION = 1
OFFICE_RUNTIME_GENERATION = 14
GUARD_PROTOCOL = "office-critic-trust-guard/v1"
RECEIPT_DOMAIN = "axioma.office.critic-invocation-receipt"
RECEIPT_SCHEMA_VERSION = 1
RECEIPT_OPERATION = "rights.review"
RECEIPT_ALGORITHM = "Ed25519"
SOCKET_PATH = "/run/jax/office-critic-trust-guard/guard.sock"
MAX_FRAME_BYTES = 20 * 1024
_USER = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_SQL_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_VERSION = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\Z")
_MODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_PRINCIPAL = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_TOP_KEYS = frozenset({"database", "domain", "guard_protocol", "limits", "office_runtime_generation", "process", "receipt", "schema_version"})
_DATABASE_KEYS = frozenset({"charset", "collation", "guard_account", "schema", "server_version", "sql_mode", "trigger_definer"})
_ACCOUNT_KEYS = frozenset({"host", "user"})
_PROCESS_KEYS = frozenset({"allowed_client_uids", "gid", "socket_path", "uid"})
_RECEIPT_KEYS = frozenset({"algorithm", "critic_principal", "domain", "operation", "schema_version"})
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
class DatabaseAccount:
    user: str
    host: str


@dataclass(frozen=True)
class ActivationContract:
    critic_principal: str
    guard_uid: int
    guard_gid: int
    allowed_client_uids: tuple[int, ...]
    socket_path: str
    guard_account: DatabaseAccount
    trigger_definer: DatabaseAccount
    database_schema: str
    database_server_version: str
    database_sql_mode: str
    database_charset: str
    database_collation: str
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
    if type(value) is not int or not 1 <= value <= maximum:
        _reject(f"activation contract {name} is invalid")
    return value


def _text(value: object, name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _reject(f"activation contract {name} is invalid")
    return value


def _account(value: object, name: str) -> DatabaseAccount:
    if not isinstance(value, dict) or set(value) != _ACCOUNT_KEYS:
        _reject(f"activation contract {name} is invalid")
    user = _text(value["user"], f"{name}.user", _USER)
    host = _database_host(value["host"], f"{name}.host")
    return DatabaseAccount(user, host)


def _database_host(value: object, name: str) -> str:
    """Only a literal localhost, IP address, or DNS name names a DB account."""
    if not isinstance(value, str) or not value.isascii() or not 1 <= len(value) <= 253 or "%" in value or "_" in value:
        _reject(f"activation contract {name} is invalid")
    if value == "localhost":
        return value
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if value.endswith(".") or any(_DNS_LABEL.fullmatch(label) is None for label in value.split(".")):
            _reject(f"activation contract {name} is invalid")
    return value


def _database_metadata(value: object, name: str, pattern: re.Pattern[str]) -> str:
    return _text(value, name, pattern)


def _sql_mode(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        _reject("activation contract database.sql_mode is invalid")
    parts = value.split(",")
    if len(set(parts)) != len(parts) or any(_MODE.fullmatch(part) is None for part in parts):
        _reject("activation contract database.sql_mode is invalid")
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
    if not isinstance(value, dict) or set(value) != _TOP_KEYS or raw != _canonical(value):
        _reject("activation contract shape or canonical bytes are invalid")
    if value["domain"] != DOMAIN or type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        _reject("activation contract domain or schema version is invalid")
    if type(value["office_runtime_generation"]) is not int or value["office_runtime_generation"] != OFFICE_RUNTIME_GENERATION:
        _reject("activation contract runtime generation is invalid")
    if value["guard_protocol"] != GUARD_PROTOCOL:
        _reject("activation contract guard protocol is invalid")
    receipt = value["receipt"]
    if not isinstance(receipt, dict) or set(receipt) != _RECEIPT_KEYS:
        _reject("activation contract receipt is invalid")
    if (receipt["domain"], receipt["schema_version"], receipt["operation"], receipt["algorithm"]) != (RECEIPT_DOMAIN, RECEIPT_SCHEMA_VERSION, RECEIPT_OPERATION, RECEIPT_ALGORITHM) or type(receipt["schema_version"]) is not int:
        _reject("activation contract receipt constants are invalid")
    critic_principal = _text(receipt["critic_principal"], "receipt.critic_principal", _PRINCIPAL)
    database = value["database"]
    if not isinstance(database, dict) or set(database) != _DATABASE_KEYS:
        _reject("activation contract database is invalid")
    database_schema = _database_metadata(database["schema"], "database.schema", _SQL_IDENTIFIER)
    database_server_version = _database_metadata(database["server_version"], "database.server_version", _VERSION)
    database_sql_mode = _sql_mode(database["sql_mode"])
    database_charset = _database_metadata(database["charset"], "database.charset", _SQL_IDENTIFIER)
    database_collation = _database_metadata(database["collation"], "database.collation", _SQL_IDENTIFIER)
    guard_account = _account(database["guard_account"], "database.guard_account")
    trigger_definer = _account(database["trigger_definer"], "database.trigger_definer")
    if guard_account == trigger_definer:
        _reject("activation contract guard account and trigger definer must differ")
    process = value["process"]
    if not isinstance(process, dict) or set(process) != _PROCESS_KEYS:
        _reject("activation contract process is invalid")
    guard_uid = _positive_int(process["uid"], "process.uid", 2**31 - 1)
    guard_gid = _positive_int(process["gid"], "process.gid", 2**31 - 1)
    if process["socket_path"] != SOCKET_PATH:
        _reject("activation contract socket path is invalid")
    raw_clients = process["allowed_client_uids"]
    if not isinstance(raw_clients, list) or not 1 <= len(raw_clients) <= 16:
        _reject("activation contract allowed client UIDs are invalid")
    clients = tuple(_positive_int(item, "process.allowed_client_uids", 2**31 - 1) for item in raw_clients)
    if clients != tuple(sorted(clients)) or len(set(clients)) != len(clients):
        _reject("activation contract allowed client UIDs are not canonical")
    limits = value["limits"]
    if not isinstance(limits, dict) or set(limits) != _LIMIT_KEYS:
        _reject("activation contract limits are invalid")
    timeout = _positive_int(limits["timeout_seconds"], "limits.timeout_seconds", 30)
    max_inflight = _positive_int(limits["max_inflight"], "limits.max_inflight", 32)
    if type(limits["max_frame_bytes"]) is not int or limits["max_frame_bytes"] != MAX_FRAME_BYTES:
        _reject("activation contract max_frame_bytes is invalid")
    # ``path`` and ``fingerprint`` are bound only after descriptor-safe I/O.
    return ActivationContract(critic_principal, guard_uid, guard_gid, clients, SOCKET_PATH, guard_account, trigger_definer, database_schema, database_server_version, database_sql_mode, database_charset, database_collation, timeout, max_inflight, Path(), ContractFingerprint(0, 0, 0, 0, ""))


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
            except OSError as error:
                raise ActivationContractError("activation contract descriptor cannot be closed") from error


def load_activation_contract(path: Path = ACTIVATION_CONTRACT_PATH) -> ActivationContract:
    """Read the fixed root-owned contract, without activating any capability."""
    locator = Path(path)
    raw, fingerprint = _read_bound_bytes(locator)
    parsed = _parse_contract(raw)
    return ActivationContract(
        parsed.critic_principal, parsed.guard_uid, parsed.guard_gid, parsed.allowed_client_uids,
        parsed.socket_path, parsed.guard_account, parsed.trigger_definer, parsed.database_schema,
        parsed.database_server_version, parsed.database_sql_mode, parsed.database_charset,
        parsed.database_collation, parsed.timeout_seconds, parsed.max_inflight, locator, fingerprint,
    )
