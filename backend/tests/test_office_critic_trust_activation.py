"""Inert v14 activation-contract parser and descriptor-bound file tests."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from virtual_office import critic_trust_activation as activation


def _value() -> dict[str, object]:
    return {
        "database": {
            "charset": activation.DATABASE_CHARSET, "collation": activation.DATABASE_COLLATION,
            "guard_account": {"host": "localhost", "user": "jax_critic_guard"},
            "schema": activation.DATABASE_SCHEMA, "server_version": activation.DATABASE_SERVER_VERSION,
            "sql_mode": activation.DATABASE_SQL_MODE,
            "trigger_definer": {"host": "localhost", "user": "jax_critic_trust_owner"},
        },
        "domain": activation.DOMAIN, "guard_protocol": activation.GUARD_PROTOCOL,
        "limits": {"max_frame_bytes": 20480, "max_inflight": 8, "timeout_seconds": 3},
        "office_runtime_generation": activation.OFFICE_RUNTIME_GENERATION,
        "process": {"allowed_client_uids": [1001], "gid": 1001, "socket_path": activation.SOCKET_PATH, "uid": 1001},
        "receipt": {"algorithm": activation.RECEIPT_ALGORITHM, "critic_principal": "agent:critic", "domain": activation.RECEIPT_DOMAIN, "operation": activation.RECEIPT_OPERATION, "schema_version": activation.RECEIPT_SCHEMA_VERSION},
        "schema_version": activation.SCHEMA_VERSION,
    }


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _root_stat(info: os.stat_result, *, directory: bool = False, mode: int | None = None, inode: int | None = None, size: int | None = None) -> os.stat_result:
    rendered_mode = (stat.S_IFDIR if directory else stat.S_IFREG) | (0o755 if directory else 0o644)
    if mode is not None:
        rendered_mode = (stat.S_IFDIR if directory else stat.S_IFREG) | mode
    return os.stat_result((rendered_mode, info.st_ino if inode is None else inode, info.st_dev, info.st_nlink, 0, 0, info.st_size if size is None else size, info.st_atime, info.st_mtime, info.st_ctime))


def _root_owned_file(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    actual_lstat = os.lstat
    actual_fstat = os.fstat

    def lstat(name: os.PathLike[str] | str) -> os.stat_result:
        observed = actual_lstat(name)
        rendered = Path(name)
        if rendered == path:
            return _root_stat(observed)
        if rendered in path.parents:
            return _root_stat(observed, directory=True)
        return observed

    monkeypatch.setattr(activation.os, "lstat", lstat)
    monkeypatch.setattr(activation.os, "fstat", lambda fd: _root_stat(actual_fstat(fd)))


def test_parser_requires_exact_canonical_schema_and_separate_db_definer() -> None:
    parsed = activation._parse_contract(_canonical(_value()))
    assert parsed.critic_principal == "agent:critic"
    assert parsed.guard_account.user == "jax_critic_guard"
    assert parsed.trigger_definer.user == "jax_critic_trust_owner"
    for mutation in (
        lambda value: value.update(extra=True),
        lambda value: value["database"].update(guard_account={"host": "localhost", "user": "jax_critic_trust_owner"}),  # type: ignore[index]
        lambda value: value["process"].update(allowed_client_uids=[1002, 1001]),  # type: ignore[index]
        lambda value: value["limits"].update(timeout_seconds=1.5),  # type: ignore[index]
        lambda value: value.update(domain="other"),
    ):
        value = _value(); mutation(value)
        with pytest.raises(activation.ActivationContractError):
            activation._parse_contract(_canonical(value))
    with pytest.raises(activation.ActivationContractError):
        activation._parse_contract(b'{"domain":"axioma.office.critic-trust-activation","domain":"axioma.office.critic-trust-activation"}\n')
    with pytest.raises(activation.ActivationContractError):
        activation._parse_contract(_canonical(_value()).replace(b"8", b"8.0", 1))
    with pytest.raises(activation.ActivationContractError):
        activation._parse_contract(b"\xef\xbb\xbf" + _canonical(_value()))
    value = _value(); value["schema_version"] = True
    with pytest.raises(activation.ActivationContractError):
        activation._parse_contract(_canonical(value))


def test_root_file_contract_binds_descriptor_and_detects_content_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "office-critic-trust-activation-v1.json"
    path.write_bytes(_canonical(_value()))
    _root_owned_file(monkeypatch, path)
    contract = activation.load_activation_contract(path)
    assert contract.fingerprint.sha256 and contract.allowed_client_uids == (1001,)
    changed = _value(); changed["process"]["uid"] = 1002  # type: ignore[index]
    path.write_bytes(_canonical(changed))
    with pytest.raises(activation.ActivationContractChanged):
        contract.assert_unchanged()


def test_rejects_non_root_permissions_and_unsafe_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "office-critic-trust-activation-v1.json"
    path.write_bytes(_canonical(_value()))
    actual_lstat = os.lstat
    monkeypatch.setattr(activation.os, "lstat", lambda name: _root_stat(actual_lstat(name), directory=Path(name) != path, mode=0o664 if Path(name) == path else None))
    with pytest.raises(activation.ActivationContractError, match="0644"):
        activation.load_activation_contract(path)

    _root_owned_file(monkeypatch, path)
    parent = path.parent
    prior = activation.os.lstat
    monkeypatch.setattr(activation.os, "lstat", lambda name: _root_stat(prior(name), directory=True, mode=0o775) if Path(name) == parent else prior(name))
    with pytest.raises(activation.ActivationContractError, match="parent"):
        activation.load_activation_contract(path)


def test_rejects_symlink_and_hardlink_before_descriptor_open(tmp_path: Path) -> None:
    source = tmp_path / "source.json"; source.write_bytes(_canonical(_value()))
    symlink = tmp_path / "activation.json"; symlink.symlink_to(source)
    with pytest.raises(activation.ActivationContractError, match="regular file"):
        activation._assert_file_metadata(os.lstat(symlink))
    hardlink = tmp_path / "activation-hardlink.json"; os.link(source, hardlink)
    with pytest.raises(activation.ActivationContractError, match="single regular"):
        activation._assert_file_metadata(os.lstat(hardlink))


def test_rejects_lstat_open_mismatch_truncate_growth_rename_and_close_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "activation.json"; path.write_bytes(_canonical(_value()))
    _root_owned_file(monkeypatch, path)
    prior_fstat = activation.os.fstat
    monkeypatch.setattr(activation.os, "fstat", lambda fd: _root_stat(prior_fstat(fd), inode=prior_fstat(fd).st_ino + 1))
    with pytest.raises(activation.ActivationContractError, match="changed before read"):
        activation.load_activation_contract(path)
    monkeypatch.undo(); _root_owned_file(monkeypatch, path)
    monkeypatch.setattr(activation.os, "read", lambda _fd, _remaining: b"")
    with pytest.raises(activation.ActivationContractError, match="changed during read"):
        activation.load_activation_contract(path)
    monkeypatch.undo(); _root_owned_file(monkeypatch, path)
    prior_fstat = activation.os.fstat
    fstat_calls = 0

    def fstat_with_growth(fd: int) -> os.stat_result:
        nonlocal fstat_calls
        fstat_calls += 1
        observed = prior_fstat(fd)
        return observed if fstat_calls == 1 else _root_stat(observed, size=observed.st_size + 1)

    monkeypatch.setattr(activation.os, "fstat", fstat_with_growth)
    with pytest.raises(activation.ActivationContractError, match="changed during read"):
        activation.load_activation_contract(path)
    monkeypatch.undo(); _root_owned_file(monkeypatch, path)
    original_read = activation.os.read
    replacement = tmp_path / "replacement.json"; replacement.write_bytes(_canonical(_value()))
    moved = False

    def read_and_rename(fd: int, remaining: int) -> bytes:
        nonlocal moved
        if not moved:
            os.replace(replacement, path); moved = True
        return original_read(fd, remaining)

    monkeypatch.setattr(activation.os, "read", read_and_rename)
    with pytest.raises(activation.ActivationContractError, match="changed during read"):
        activation.load_activation_contract(path)
    monkeypatch.undo(); path.write_bytes(_canonical(_value())); _root_owned_file(monkeypatch, path)
    monkeypatch.setattr(activation.os, "close", lambda _fd: (_ for _ in ()).throw(OSError("close")))
    with pytest.raises(activation.ActivationContractError, match="cannot be closed"):
        activation.load_activation_contract(path)
