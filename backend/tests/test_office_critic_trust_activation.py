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
        "algorithm": "Ed25519", "allowed_peer_uids": [1001], "database_user": "jax_critic_guard",
        "domain": "axioma.office", "guard_gid": 1001, "guard_protocol": activation.GUARD_PROTOCOL,
        "guard_uid": 1001, "limits": {"max_frame_bytes": 20480, "max_inflight": 8, "timeout_seconds": 3},
        "purpose": "rights.review", "trust_definer_user": "jax_critic_trust_owner", "version": 1,
    }


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _root_stat(info: os.stat_result, *, directory: bool = False, mode: int | None = None) -> os.stat_result:
    rendered_mode = (stat.S_IFDIR if directory else stat.S_IFREG) | (0o755 if directory else 0o644)
    if mode is not None:
        rendered_mode = (stat.S_IFDIR if directory else stat.S_IFREG) | mode
    return os.stat_result((rendered_mode, info.st_ino, info.st_dev, 1, 0, 0, info.st_size, info.st_atime, info.st_mtime, info.st_ctime))


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
    assert parsed.database_user == "jax_critic_guard"
    assert parsed.trust_definer_user == "jax_critic_trust_owner"
    for mutation in (
        lambda value: value.update(extra=True),
        lambda value: value.update(database_user="jax_critic_trust_owner"),
        lambda value: value.update(allowed_peer_uids=[1002, 1001]),
        lambda value: value["limits"].update(timeout_seconds=1.5),  # type: ignore[index]
        lambda value: value.update(domain="other"),
    ):
        value = _value(); mutation(value)
        with pytest.raises(activation.ActivationContractError):
            activation._parse_contract(_canonical(value))
    with pytest.raises(activation.ActivationContractError):
        activation._parse_contract(b'{"domain":"axioma.office","domain":"axioma.office"}\n')
    with pytest.raises(activation.ActivationContractError):
        activation._parse_contract(_canonical(_value()).replace(b"8", b"8.0", 1))
    with pytest.raises(activation.ActivationContractError):
        activation._parse_contract(b"\xef\xbb\xbf" + _canonical(_value()))


def test_root_file_contract_binds_descriptor_and_detects_content_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "office-critic-trust-activation-v1.json"
    path.write_bytes(_canonical(_value()))
    _root_owned_file(monkeypatch, path)
    contract = activation.load_activation_contract(path)
    assert contract.fingerprint.sha256 and contract.allowed_peer_uids == (1001,)
    path.write_bytes(_canonical({**_value(), "guard_uid": 1002}))
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
