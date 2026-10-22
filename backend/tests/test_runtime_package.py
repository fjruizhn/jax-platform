import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


MODULE = Path(__file__).resolve().parents[2] / "ops/migration/runtime_package.py"
spec = importlib.util.spec_from_file_location("runtime_package", MODULE)
rp = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(rp)


def package(tmp_path, payload):
    bundle = tmp_path / "bundle"
    bundle.mkdir(parents=True)
    bundle.chmod(0o755)
    supplied = {path for path, _, _, _ in payload}
    for path, (destination, mode) in rp._HELPERS.items():
        if path not in supplied:
            payload.append((path, "helper", path.encode(), mode))
    for path in rp._UNITS:
        if path not in supplied:
            payload.append((path, "unit", path.encode(), "0644"))
    if rp._MANIFEST not in supplied:
        payload.append((rp._MANIFEST, "manifest", b"runtime receipt", "0644"))
    records = []
    for path, role, contents, mode in payload:
        item = bundle / path
        item.parent.mkdir(parents=True, exist_ok=True)
        item.parent.chmod(0o755)
        item.write_bytes(contents)
        item.chmod(int(mode, 8))
        records.append({"path": path, "role": role, "sha256": hashlib.sha256(contents).hexdigest(), "size": len(contents), "mode": mode, "owner": "root:root"})
    data = {"schema": rp.SCHEMA, "payload": sorted(records, key=lambda value: value["path"])}
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    (bundle / "manifest.json").write_bytes(raw)
    (bundle / "manifest.json").chmod(0o644)
    return bundle, hashlib.sha256(raw).hexdigest()


def test_rejects_bad_digest_extra_and_non_closed_role(tmp_path):
    bundle, digest = package(tmp_path, [("helpers/jax-platform-pair-exec", "helper", b"x", "0755")])
    with pytest.raises(rp.PackageError):
        rp.validate(bundle, "0" * 64, owner_uid=os.getuid(), owner_gid=os.getgid())
    (bundle / "extra").write_text("x")
    with pytest.raises(rp.PackageError):
        rp.validate(bundle, digest, owner_uid=os.getuid(), owner_gid=os.getgid())


def test_rejects_partial_package_even_when_its_digest_is_valid(tmp_path):
    bundle, digest = package(tmp_path, [("helpers/jax-platform-pair-exec", "helper", b"x", "0755")])
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["payload"] = [manifest["payload"][0]]
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    (bundle / "manifest.json").write_bytes(raw)
    with pytest.raises(rp.PackageError, match="incomplete"):
        rp.validate(bundle, hashlib.sha256(raw).hexdigest(), owner_uid=os.getuid(), owner_gid=os.getgid())
    bundle, digest = package(tmp_path / "two", [("helpers/nope", "helper", b"x", "0755")])
    with pytest.raises(rp.PackageError):
        rp.validate(bundle, digest, owner_uid=os.getuid(), owner_gid=os.getgid())


def test_rejects_unsealed_symlink_and_mode(tmp_path):
    bundle, digest = package(tmp_path, [("helpers/jax-platform-pair-exec", "helper", b"x", "0755")])
    (bundle / "helpers/jax-platform-pair-exec").chmod(0o777)
    with pytest.raises(rp.PackageError):
        rp.validate(bundle, digest, owner_uid=os.getuid(), owner_gid=os.getgid())
    bundle, digest = package(tmp_path / "two", [("helpers/jax-platform-pair-exec", "helper", b"x", "0755")])
    os.unlink(bundle / "helpers/jax-platform-pair-exec")
    os.symlink("/etc/passwd", bundle / "helpers/jax-platform-pair-exec")
    with pytest.raises(rp.PackageError):
        rp.validate(bundle, digest)


def test_import_removes_stale_dropin_and_commits_only_after_effective_check(tmp_path):
    bundle, digest = package(tmp_path, [
        ("helpers/jax-platform-pair-exec", "helper", b"new", "0755"),
        ("units/jax-platform.service", "unit", b"unit", "0644"),
    ])
    root = tmp_path / "destination"
    stale = root / "etc/systemd/system/jax-platform.service.d/old.conf"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    calls = []
    rp.import_package(bundle, digest, root, tmp_path / "backup", lambda: calls.append("effective"), package_owner_uid=os.getuid(), package_owner_gid=os.getgid(), install_owner_uid=os.getuid(), install_owner_gid=os.getgid())
    assert calls == ["effective"]
    assert (root / "usr/local/libexec/jax-platform-pair-exec").read_bytes() == b"new"
    assert not stale.exists()


def test_import_rolls_back_bytes_modes_and_absence_after_failed_effective_check(tmp_path):
    bundle, digest = package(tmp_path, [
        ("helpers/jax-platform-pair-exec", "helper", b"new", "0755"),
        ("units/jax-platform.service", "unit", b"unit", "0644"),
    ])
    root = tmp_path / "destination"
    helper = root / "usr/local/libexec/jax-platform-pair-exec"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"old")
    helper.chmod(0o700)
    with pytest.raises(RuntimeError):
        rp.import_package(bundle, digest, root, tmp_path / "backup", lambda: (_ for _ in ()).throw(RuntimeError("dbus mismatch")), package_owner_uid=os.getuid(), package_owner_gid=os.getgid(), install_owner_uid=os.getuid(), install_owner_gid=os.getgid())
    assert helper.read_bytes() == b"old"
    assert helper.stat().st_mode & 0o777 == 0o700
    assert not (root / "etc/systemd/system/jax-platform.service").exists()


def test_failed_rollback_is_recovery_required(tmp_path, monkeypatch):
    bundle, digest = package(tmp_path, [("helpers/jax-platform-pair-exec", "helper", b"new", "0755")])
    monkeypatch.setattr(rp, "restore", lambda *_: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(rp.PackageError, match="RECOVERY_REQUIRED"):
        rp.import_package(bundle, digest, tmp_path / "destination", tmp_path / "backup", lambda: (_ for _ in ()).throw(RuntimeError("verify")), package_owner_uid=os.getuid(), package_owner_gid=os.getgid(), install_owner_uid=os.getuid(), install_owner_gid=os.getgid())


def test_import_uses_bytes_sealed_before_path_replacement(tmp_path, monkeypatch):
    bundle, digest = package(tmp_path, [("helpers/jax-platform-pair-exec", "helper", b"sealed", "0755")])
    original = rp._destinations
    def replace_after_load(data, root):
        (bundle / "helpers/jax-platform-pair-exec").write_bytes(b"replacement")
        return original(data, root)
    monkeypatch.setattr(rp, "_destinations", replace_after_load)
    root = tmp_path / "destination"
    rp.import_package(bundle, digest, root, tmp_path / "backup", lambda: None, package_owner_uid=os.getuid(), package_owner_gid=os.getgid(), install_owner_uid=os.getuid(), install_owner_gid=os.getgid())
    assert (root / "usr/local/libexec/jax-platform-pair-exec").read_bytes() == b"sealed"


def test_root_owned_load_rejects_writable_ancestor(tmp_path, monkeypatch):
    bundle, digest = package(tmp_path, [("helpers/jax-platform-pair-exec", "helper", b"sealed", "0755")])
    with pytest.raises(rp.PackageError, match="ownership"):
        rp._trusted_ancestors(bundle)


def test_runtime_import_lock_is_exclusive(tmp_path):
    lock = tmp_path / "runtime.lock"
    with rp._exclusive_runtime_lock(lock):
        with pytest.raises(rp.PackageError, match="already"):
            with rp._exclusive_runtime_lock(lock):
                pass


def test_active_service_rejects_import(monkeypatch):
    class Result:
        stdout = "active\n"
    monkeypatch.setattr(rp.subprocess, "run", lambda *_, **__: Result())
    with pytest.raises(rp.PackageError, match="inactive"):
        rp._require_units_inactive()


def test_import_leaves_durable_verified_journal(tmp_path):
    bundle, digest = package(tmp_path, [("helpers/jax-platform-pair-exec", "helper", b"new", "0755")])
    backup = tmp_path / "backup"
    rp.import_package(bundle, digest, tmp_path / "destination", backup, lambda: None, package_owner_uid=os.getuid(), package_owner_gid=os.getgid(), install_owner_uid=os.getuid(), install_owner_gid=os.getgid())
    assert json.loads((backup / "journal.json").read_text())["phase"] == "VERIFIED"


def test_incomplete_journal_restores_snapshot(tmp_path):
    target = tmp_path / "target"
    target.write_text("old")
    backup = tmp_path / "backup"
    rp.snapshot({"unit": target}, backup)
    rp._journal(backup, "MUTATING", {"unit": target})
    target.write_text("candidate")
    rp.recover_incomplete(backup)
    assert target.read_text() == "old"
    assert json.loads((backup / "journal.json").read_text())["phase"] == "ROLLED_BACK"
