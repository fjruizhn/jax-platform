from __future__ import annotations
import base64, contextlib, importlib.util, os, stat, sys, tracemalloc
from pathlib import Path
from types import SimpleNamespace
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

def test_rejects_directory_over_budget_while_scanning(tmp_path, monkeypatch):
    file(tmp_path, "one.txt"); file(tmp_path, "two.txt")
    monkeypatch.setattr(module, "_MAX_FILES", 1)
    monkeypatch.setattr(module, "_MAX_DIRECTORIES", 1)
    with pytest.raises(module.ModuleReleaseContractRejected, match="entry count"):
        module.build_artifact_set(tmp_path)

def test_rejects_non_utf8_name_without_file_descriptor_leak(tmp_path):
    raw = os.fsencode(tmp_path) + b"/bad-\xff"
    fd = os.open(raw, os.O_CREAT | os.O_WRONLY, 0o644); os.close(fd)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(module.ModuleReleaseContractRejected, match="valid UTF-8"):
        module.build_artifact_set(tmp_path)
    assert len(os.listdir("/proc/self/fd")) == before

def test_closes_root_fd_when_initial_scandir_fails(tmp_path, monkeypatch):
    file(tmp_path)
    before = len(os.listdir("/proc/self/fd"))
    def fail(_fd): raise OSError("injected scandir failure")
    monkeypatch.setattr(module.os, "scandir", fail)
    with pytest.raises(module.ModuleReleaseContractRejected, match="directory cannot be read"):
        module.build_artifact_set(tmp_path)
    assert len(os.listdir("/proc/self/fd")) == before

@pytest.mark.parametrize("value", [[], "not-an-object"])
def test_rejects_hostile_artifact_set_types(value):
    with pytest.raises(module.ModuleReleaseContractRejected):
        module.validate_artifact_set(value)

def test_rejects_unhashable_mode_in_inventory(tmp_path):
    file(tmp_path); artifact = module.build_artifact_set(tmp_path)
    artifact["entries"][0]["mode"] = []
    with pytest.raises(module.ModuleReleaseContractRejected):
        module.validate_artifact_set(artifact)

def test_rejects_boolean_schema_version(tmp_path):
    file(tmp_path); artifact = module.build_artifact_set(tmp_path)
    artifact["schema_version"] = True
    with pytest.raises(module.ModuleReleaseContractRejected):
        module.validate_artifact_set(artifact)

def test_closes_root_fd_when_root_identity_changes(tmp_path, monkeypatch):
    file(tmp_path); real_fstat = module.os.fstat; before = len(os.listdir("/proc/self/fd")); calls = 0
    def swapped(fd):
        nonlocal calls
        calls += 1
        result = real_fstat(fd)
        return SimpleNamespace(st_dev=result.st_dev + 1, st_ino=result.st_ino) if calls == 1 else result
    monkeypatch.setattr(module.os, "fstat", swapped)
    with pytest.raises(module.ModuleReleaseContractRejected, match="root changed"):
        module.build_artifact_set(tmp_path)
    assert len(os.listdir("/proc/self/fd")) == before

@pytest.mark.parametrize("kind", ["root", "child"])
def test_closes_owned_fd_when_fstat_fails(tmp_path, monkeypatch, kind):
    if kind == "child":
        (tmp_path / "child").mkdir(); file(tmp_path / "child")
    else:
        file(tmp_path)
    real_fstat = module.os.fstat; before = len(os.listdir("/proc/self/fd")); calls = 0
    def failing(fd):
        nonlocal calls
        calls += 1
        if (kind == "root" and calls == 1) or (kind == "child" and calls == 3):
            raise OSError("injected fstat failure")
        return real_fstat(fd)
    monkeypatch.setattr(module.os, "fstat", failing)
    with pytest.raises(module.ModuleReleaseContractRejected, match="cannot be inspected safely"):
        module.build_artifact_set(tmp_path)
    assert len(os.listdir("/proc/self/fd")) == before

def test_close_failure_still_closes_remaining_directories(tmp_path, monkeypatch):
    file(tmp_path, "child/file.txt")
    (tmp_path / "child" / "link.txt").symlink_to("file.txt")
    real_close = module.os.close
    before = len(os.listdir("/proc/self/fd"))
    calls = 0
    def close_then_fail(fd):
        nonlocal calls
        is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        real_close(fd)
        if is_directory:
            calls += 1
        if is_directory and calls == 1:
            raise OSError("injected close failure")
    monkeypatch.setattr(module.os, "close", close_then_fail)
    with pytest.raises(module.ModuleReleaseContractRejected, match="descriptor close failed"):
        module.build_artifact_set(tmp_path)
    assert calls >= 2
    assert len(os.listdir("/proc/self/fd")) == before


def test_close_error_after_fd_reuse_never_closes_a_foreign_descriptor(tmp_path, monkeypatch):
    file(tmp_path)
    foreign_path = tmp_path / "foreign.txt"; foreign_path.write_text("keep", encoding="utf-8")
    real_close = module.os.close
    source_fd = os.open(foreign_path, os.O_RDONLY)
    replacement: list[int] = []
    def close_then_reuse(fd):
        is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        real_close(fd)
        if is_directory and not replacement:
            os.dup2(source_fd, fd)
            replacement.append(fd)
            raise OSError("close reported after descriptor reuse")
    monkeypatch.setattr(module.os, "close", close_then_reuse)
    try:
        with pytest.raises(module.ModuleReleaseContractRejected, match="descriptor close failed"):
            module.build_artifact_set(tmp_path)
        assert replacement
        assert os.fstat(replacement[0]).st_size == len("keep")
    finally:
        if replacement:
            with contextlib.suppress(OSError):
                real_close(replacement[0])
        real_close(source_fd)


def test_rejects_hostile_inventory_path_before_split_amplifies_memory(tmp_path):
    file(tmp_path)
    artifact = module.build_artifact_set(tmp_path)
    artifact["entries"][0]["path"] = "a/" * 1_000_000 + "a"
    tracemalloc.start()
    try:
        with pytest.raises(module.ModuleReleaseContractRejected, match="path exceeds"):
            module.validate_artifact_set(artifact)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 16 * 1024 * 1024


def test_artifact_set_v1_golden_glm_vector_is_byte_stable(tmp_path):
    vectors = {
        "Zeta.txt": ("b3JkZW46IGxhIFogKDB4NUEpIG9yZGVuYSBhbnRlcyBxdWUgbGEgYSAoMHg2MSkgZW4gYnl0ZXMK", 0o644),
        "alfa.txt": ("b3JkZW46IGxhIGEgbWluw7pzY3VsYSBkZXNwdcOpcyBkZSBsYSBtYXnDunNjdWxhCg==", 0o644),
        "copy/taglines.txt": ("MSkgwqtMYSBiYW5jYSBxdWUgaGFibGEgdHUgaWRpb21hLsK7CjIpIMKrVHUgQmFuY28gSWRlYWwsIGRlIGxhIGdlbnRlLsK7CjMpIMKrQ3VlbnRhcyBjbGFyYXMsIGZ1dHVybyBzZWd1cm8uwrsK", 0o444),
        "ilustración/hopper.svg": ("PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPjxyZWN0IHdpZHRoPSIxMCIgaGVpZ2h0PSIxMCIvPjwvc3ZnPgo=", 0o644),
        "web/portada.html": ("PCFkb2N0eXBlIGh0bWw+CjxodG1sIGxhbmc9ImVzIj4KPGJvZHk+PGgxPkJESTwvaDE+PC9ib2R5Pgo8L2h0bWw+Cg==", 0o644),
    }
    for name, (encoded, mode) in vectors.items():
        target = tmp_path / name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded)); target.chmod(mode)
    artifact = module.build_artifact_set(tmp_path)
    assert artifact["root_digest"] == "sha256:1a665d437e9b3c931ca53a2907c208f382679f10fa86585d144728486e635221"
    assert [entry["path"] for entry in artifact["entries"]] == ["Zeta.txt", "alfa.txt", "copy/taglines.txt", "ilustración/hopper.svg", "web/portada.html"]


def test_artifact_set_v1_rejects_casefold_special_file_limits_and_v2(tmp_path, monkeypatch):
    file(tmp_path, "Logo.svg"); file(tmp_path, "logo.svg")
    with pytest.raises(module.ModuleReleaseContractRejected, match="case-fold"):
        module.build_artifact_set(tmp_path)
    (tmp_path / "logo.svg").unlink(); os.mkfifo(tmp_path / "pipe")
    with pytest.raises(module.ModuleReleaseContractRejected, match="non-regular"):
        module.build_artifact_set(tmp_path)
    (tmp_path / "pipe").unlink(); artifact = module.build_artifact_set(tmp_path)
    artifact["schema_version"] = 2
    with pytest.raises(module.ModuleReleaseContractRejected, match="artifact-set/v1"):
        module.validate_artifact_set(artifact)
    monkeypatch.setattr(module, "_MAX_FILES", 1); file(tmp_path, "second.txt")
    with pytest.raises(module.ModuleReleaseContractRejected, match="file count"):
        module.build_artifact_set(tmp_path)
