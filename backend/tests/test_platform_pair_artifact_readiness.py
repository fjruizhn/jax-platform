"""The platform may start only an immutable, complete JAX/JXP pair."""
from __future__ import annotations

import json
import os
import importlib.util
from pathlib import Path

import pytest

_MODULE = Path(__file__).resolve().parents[2] / "ops" / "migration" / "pair_artifact_readiness.py"
_SPEC = importlib.util.spec_from_file_location("pair_artifact_readiness", _MODULE)
assert _SPEC and _SPEC.loader
readiness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(readiness)


def _pair(tmp_path: Path) -> tuple[Path, int, int]:
    root = tmp_path / "opt" / "jax-platform-pair" / "releases" / ("a" * 40 + "-" + "b" * 40)
    (root / "jax-platform" / "backend" / ".venv" / "bin").mkdir(parents=True)
    (root / "jax" / "jax").mkdir(parents=True)
    for path, content, mode in (
        (root / "jax-platform" / "backend" / ".venv" / "bin" / "python", "python", 0o755),
        (root / "jax-platform" / "backend" / ".venv" / "bin" / "uvicorn", "uvicorn", 0o755),
        (root / "jax" / "jax" / "memory.py", "memory", 0o644),
    ):
        path.write_text(content, encoding="utf-8"); path.chmod(mode)
    uid, gid = os.getuid(), os.getgid()
    for path in (root, root / "jax-platform", root / "jax-platform" / "backend",
                 root / "jax-platform" / "backend" / ".venv", root / "jax-platform" / "backend" / ".venv" / "bin",
                 root / "jax", root / "jax" / "jax", root.parent, root.parent.parent):
        path.chmod(0o755)
    attestation = {"schema": "axioma.pair-build/v2", "platform_commit": "a" * 40,
                   "platform_tree": "1" * 40, "platform_links": [], "jax_commit": "b" * 40,
                   "jax_tree": "2" * 40, "jax_links": [], "wheelhouse_sha256": "d" * 64,
                   "lock_sha256": "c" * 64, "python": "/usr/bin/python3", "python_sha256": "3" * 64,
                   "python_version": "cpython-test", "freeze_sha256": "4" * 64}
    (root / "build-attestation.json").write_text(json.dumps(attestation), encoding="utf-8")
    (root / "build-attestation.json").chmod(0o644)
    manifest = {"schema": readiness.SCHEMA, "jax_platform_revision": "a" * 40,
                "jax_revision": "b" * 40, "dependency_lock_sha256": "c" * 64,
                "wheelhouse_manifest_sha256": "d" * 64,
                "jax_links": [],
                "files": readiness._inventory(root, owner_uid=uid, owner_gid=gid)}
    (root / readiness.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    (root / readiness.MANIFEST).chmod(0o644)
    return root, uid, gid


def _verify(root: Path, uid: int, gid: int) -> None:
    readiness.verify(root, expected_jxp="a" * 40, expected_jax="b" * 40, owner_uid=uid, owner_gid=gid,
                     ancestor_stop=root.parent.parent)


def test_accepts_complete_pair_and_rejects_each_authoritative_mutation(tmp_path: Path) -> None:
    root, uid, gid = _pair(tmp_path)
    _verify(root, uid, gid)
    for relative, value in (("jax-platform/backend/main.py", "changed"), ("jax/jax/memory.py", "changed"),
                            ("jax-platform/backend/.venv/bin/python", "changed")):
        fresh, fresh_uid, fresh_gid = _pair(tmp_path / relative.replace("/", "_"))
        path = fresh / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        path.chmod(0o644 if not path.name in {"python", "uvicorn"} else 0o755)
        with pytest.raises(readiness.PairReadinessError, match="inventory mismatch"):
            _verify(fresh, fresh_uid, fresh_gid)


def test_rejects_extra_missing_symlink_and_writable_nodes(tmp_path: Path) -> None:
    for kind in ("extra", "missing", "symlink", "writable"):
        root, uid, gid = _pair(tmp_path / kind)
        target = root / "jax-platform" / "backend" / ".venv" / "bin" / "python"
        if kind == "extra":
            (root / "jax-platform" / "backend" / "unexpected.py").write_text("x", encoding="utf-8")
        elif kind == "missing":
            target.unlink()
        elif kind == "symlink":
            target.unlink(); target.symlink_to("uvicorn")
        else:
            target.chmod(0o775)
        with pytest.raises(readiness.PairReadinessError):
            _verify(root, uid, gid)


def test_allows_only_sealed_internal_jax_symlink_targets(tmp_path: Path) -> None:
    root, uid, gid = _pair(tmp_path)
    target = root / "jax" / "jax" / "memory.py"
    link = root / "jax" / "las_manos_memory.py"
    link.symlink_to("jax/memory.py")
    link_attestation = {"path": "jax/las_manos_memory.py", "mode": "120000", "blob_oid": "a" * 40,
                        "target": "jax/memory.py", "target_path": "jax/jax/memory.py", "target_mode": "100644", "target_oid": "b" * 40}
    attestation = json.loads((root / "build-attestation.json").read_text(encoding="utf-8")); attestation["jax_links"] = [link_attestation]
    (root / "build-attestation.json").write_text(json.dumps(attestation), encoding="utf-8")
    manifest = json.loads((root / readiness.MANIFEST).read_text(encoding="utf-8"))
    manifest["jax_links"] = [link_attestation]
    manifest["files"] = readiness._inventory(root, owner_uid=uid, owner_gid=gid)
    (root / readiness.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    _verify(root, uid, gid)
    link.unlink(); link.symlink_to("/etc/passwd")
    with pytest.raises(readiness.PairReadinessError):
        _verify(root, uid, gid)


@pytest.mark.parametrize("field,value", [("target_oid", "c" * 40), ("target_mode", "100755")])
def test_rejects_pair_manifest_link_attestation_mutation(tmp_path: Path, field: str, value: str) -> None:
    root, uid, gid = _pair(tmp_path)
    link_attestation = {"path": "jax/link", "mode": "120000", "blob_oid": "a" * 40,
                        "target": "jax/memory.py", "target_path": "jax/jax/memory.py", "target_mode": "100644", "target_oid": "b" * 40}
    (root / "jax" / "link").symlink_to("jax/memory.py")
    attestation = json.loads((root / "build-attestation.json").read_text(encoding="utf-8")); attestation["jax_links"] = [link_attestation]
    (root / "build-attestation.json").write_text(json.dumps(attestation), encoding="utf-8")
    manifest = json.loads((root / readiness.MANIFEST).read_text(encoding="utf-8")); manifest["jax_links"] = [dict(link_attestation)]
    manifest["jax_links"][0][field] = value
    manifest["files"] = readiness._inventory(root, owner_uid=uid, owner_gid=gid)
    (root / readiness.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(readiness.PairReadinessError, match="jax link attestation mismatch"):
        _verify(root, uid, gid)


def test_rejects_manifest_revision_downgrade_or_wrong_pair(tmp_path: Path) -> None:
    root, uid, gid = _pair(tmp_path)
    manifest = json.loads((root / readiness.MANIFEST).read_text(encoding="utf-8"))
    manifest["jax_revision"] = "c" * 40
    (root / readiness.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    (root / readiness.MANIFEST).chmod(0o644)
    with pytest.raises(readiness.PairReadinessError, match="unexpected jax_revision"):
        _verify(root, uid, gid)


def test_platform_units_bind_both_processes_to_the_same_pair() -> None:
    root = Path(__file__).resolve().parents[2]
    for name, mode in (("jax-platform.service", "web"), ("jax-platform-migrate.service", "migrate")):
        unit = (root / "ops" / "migration" / "systemd-units" / name).read_text(encoding="utf-8")
        assert "WorkingDirectory=/" in unit
        credential = "runtime.env" if mode == "web" else "migrate.env"
        assert f"LoadCredential={credential}:/etc/jax/{credential}" in unit
        assert f"ExecStart=/usr/bin/python3 -I -S /usr/local/libexec/jax-platform-pair-exec.py {mode} ${{CREDENTIALS_DIRECTORY}}/{credential}" in unit


def test_runtime_uses_only_root_selection_and_never_inherits_checkout_paths() -> None:
    root = Path(__file__).resolve().parents[2]
    launcher = (root / "ops" / "migration" / "jax-platform-pair-exec").read_text(encoding="utf-8")
    activator = (root / "ops" / "migration" / "activate-platform-pair-release.sh").read_text(encoding="utf-8")
    installer = (root / "ops" / "migration" / "install-platform-pair-release.sh").read_text(encoding="utf-8")
    runtime_installer = (root / "ops" / "migration" / "install-platform-pair-runtime.sh").read_text(encoding="utf-8")

    assert "selection=/etc/jax/platform-pair-selection.json" in launcher
    assert "unset JAX_REPO_PATH PYTHONPATH PYTHONHOME VIRTUAL_ENV" in launcher
    assert 'export JAX_REPO_PATH="$release/jax"' in launcher
    assert "/usr/bin/flock -s 9" in launcher
    assert "/usr/bin/flock -x -w" in activator
    assert "--expected-manifest-sha256" in activator
    assert "os.replace(temp,path)" in activator
    assert "os.fsync(d)" in activator
    assert "$SYSTEMCTL start jax-platform.service" in activator
    assert "Requires=jax-platform-migrate.service" in (root / "ops" / "migration" / "systemd-units" / "jax-platform.service").read_text(encoding="utf-8")
    assert "git -C" not in activator
    assert "git -C" not in installer
    assert "is disabled" in installer
    assert "sealed jaxbuild builder and root adopter handoff" in installer
    assert "--expected-package-digest" in runtime_installer
    assert "--bootstrap-sha256" in runtime_installer
    assert "--effective-validator-sha256" in runtime_installer
    assert '"/proc/self/fd/$bootstrap_fd" import' in runtime_installer
    assert "$PACKAGE/artifact_readiness.py" not in runtime_installer
    for unit in ("jax-platform.service", "jax-platform-migrate.service"):
        text = (root / "ops" / "migration" / "systemd-units" / unit).read_text(encoding="utf-8")
        assert "EnvironmentFile=" not in text
