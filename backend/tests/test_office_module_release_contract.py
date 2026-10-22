from __future__ import annotations
import importlib.util, os, sys
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("release_contract", Path(__file__).parents[1] / "virtual_office" / "module_release_contract.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)

def file(root, name="asset.txt", data=b"approved bytes"):
    target = root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data); target.chmod(0o644); return target

def test_seals_package_but_returns_pending_contract(tmp_path):
    file(tmp_path); artifact = module.build_artifact_set(tmp_path); module.validate_artifact_set(artifact)
    status = module.publication_contract_status(tmp_path)
    assert status.state == "PENDING_CONTRACT" and status.artifact_digest == artifact["root_digest"] and "signature_scope" in status.pending

@pytest.mark.parametrize("name", ["../escape.txt", "e\u0301.txt", "a\\b.txt"])
def test_rejects_traversal_nfd_and_backslash(tmp_path, name):
    file(tmp_path, name)
    with pytest.raises(module.ModuleReleaseContractRejected): module.build_artifact_set(tmp_path)

def test_rejects_symlink_hardlink_and_snapshot_drift(tmp_path):
    source = file(tmp_path, "source.txt"); os.symlink(source, tmp_path / "link.txt")
    with pytest.raises(module.ModuleReleaseContractRejected): module.build_artifact_set(tmp_path)
    (tmp_path / "link.txt").unlink(); os.link(source, tmp_path / "hard.txt")
    with pytest.raises(module.ModuleReleaseContractRejected): module.build_artifact_set(tmp_path)
    (tmp_path / "hard.txt").unlink(); artifact = module.build_artifact_set(tmp_path); file(tmp_path, "new.txt")
    with pytest.raises(module.ModuleReleaseContractRejected): module.verify_artifact_directory(tmp_path, artifact)

def test_rejects_directory_symlink_swap_before_open(tmp_path, monkeypatch):
    safe = tmp_path / "safe"; safe.mkdir(); file(safe, "in.txt")
    external = tmp_path / "external"; external.mkdir(); file(external, "secret.txt", b"outside")
    real_open, swapped = module.os.open, False
    def swap(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == "safe" and flags & os.O_DIRECTORY and not swapped:
            swapped = True; safe.rename(tmp_path / "safe-real"); os.symlink(external, safe)
        return real_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(module.os, "open", swap)
    with pytest.raises(module.ModuleReleaseContractRejected): module.build_artifact_set(tmp_path)

def test_rejects_byte_and_directory_limits(tmp_path, monkeypatch):
    file(tmp_path); monkeypatch.setattr(module, "_MAX_FILE_BYTES", 4)
    with pytest.raises(module.ModuleReleaseContractRejected): module.build_artifact_set(tmp_path)
    monkeypatch.setattr(module, "_MAX_FILE_BYTES", 1 << 30); monkeypatch.setattr(module, "_MAX_DIRECTORIES", 1); (tmp_path / "child").mkdir()
    with pytest.raises(module.ModuleReleaseContractRejected): module.build_artifact_set(tmp_path)
