from __future__ import annotations

import os
import stat
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from virtual_office import module_artifact_seal as seal
from virtual_office.module_release_contract import ModuleReleaseContractRejected, build_artifact_set


def artifact(root: Path, name: str = "asset.txt", data: bytes = b"approved bytes", mode: int = 0o644) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    return path


def test_seal_is_private_content_addressed_and_source_mutation_does_not_change_receipt(tmp_path):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); artifact(source, "run.sh", b"first", 0o755)
    receipt = seal.seal_artifact_set(source, store)
    artifact(source, "run.sh", b"second", 0o755)
    assert receipt.snapshot_id == receipt.root_digest.removeprefix("sha256:")
    assert (store / receipt.snapshot_id / "run.sh").read_bytes() == b"first"
    assert stat.S_IMODE((store / receipt.snapshot_id / "run.sh").stat().st_mode) == 0o555
    assert stat.S_IMODE((store / receipt.snapshot_id).stat().st_mode) == 0o500
    seal.verify_sealed_artifact(receipt, store)


@pytest.mark.parametrize("mutation", ["tamper", "delete"])
def test_verification_rehashes_and_rejects_snapshot_tampering(tmp_path, mutation):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); artifact(source)
    receipt = seal.seal_artifact_set(source, store)
    target = store / receipt.snapshot_id / "asset.txt"
    target.parent.chmod(0o700)
    target.chmod(0o600)
    if mutation == "tamper": target.write_bytes(b"changed")
    else: target.unlink()
    with pytest.raises(ModuleReleaseContractRejected): seal.verify_sealed_artifact(receipt, store)


def test_concurrent_identical_seals_converge_on_one_snapshot(tmp_path):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); artifact(source, "nested/a.txt"); artifact(source, "b.txt", b"two")
    receipts: list[seal.SealedArtifactReceipt] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)
    def run():
        try:
            barrier.wait(); receipts.append(seal.seal_artifact_set(source, store))
        except BaseException as error: errors.append(error)
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert not errors and len(receipts) == 2 and receipts[0].snapshot_id == receipts[1].snapshot_id
    assert [entry.name for entry in store.iterdir() if not entry.name.startswith(".")] == [receipts[0].snapshot_id]
    seal.verify_sealed_artifact(receipts[0], store)


def test_failure_cleans_only_its_partial_staging_tree(tmp_path, monkeypatch):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); artifact(source)
    sentinel = store / ".staging-not-ours"; store.mkdir(mode=0o700); sentinel.mkdir(mode=0o700); artifact(sentinel, "keep.txt")
    def fail(*_args, **_kwargs): raise OSError("injected fsync failure")
    monkeypatch.setattr(seal.os, "fsync", fail)
    with pytest.raises(OSError): seal.seal_artifact_set(source, store)
    assert (sentinel / "keep.txt").exists()
    assert not [path for path in store.iterdir() if path.name.startswith(".staging-") and path.name != sentinel.name]


@pytest.mark.parametrize("kind", ["same", "source_inside_store", "store_inside_source"])
def test_rejects_source_store_overlap_before_copy(tmp_path, kind):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); artifact(source)
    if kind == "same": store = source
    elif kind == "source_inside_store":
        store.mkdir(); source = store / "source"; source.mkdir(); artifact(source)
    else:
        store = source / "store"
    with pytest.raises(ModuleReleaseContractRejected, match="must not overlap"):
        seal.seal_artifact_set(source, store)


def test_malicious_receipt_path_is_rejected_before_store_io(tmp_path, monkeypatch):
    source = tmp_path / "source"; source.mkdir(); artifact(source)
    inventory = build_artifact_set(source)
    receipt = seal.SealedArtifactReceipt("../outside", inventory["root_digest"], inventory, "2026-10-22T00:00:00Z")
    monkeypatch.setattr(seal.os, "open", lambda *_args, **_kwargs: pytest.fail("must not open store"))
    with pytest.raises(ModuleReleaseContractRejected, match="invalid sealed artifact receipt"):
        seal.verify_sealed_artifact(receipt, tmp_path / "not-opened")


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_rejects_unsafe_source_entries(tmp_path, kind):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); original = artifact(source)
    if kind == "symlink": os.symlink(original, source / "link.txt")
    elif kind == "hardlink": os.link(original, source / "linked.txt")
    with pytest.raises(ModuleReleaseContractRejected): seal.seal_artifact_set(source, store)


def test_normalizes_allowed_modes_and_receipt_matches_contract_digest(tmp_path):
    source, store = tmp_path / "source", tmp_path / "store"
    # This is identical to the CV oracle rule: private 0600 becomes readonly
    # data, and any executable source mode (including 0777) becomes 0555.
    source.mkdir(); artifact(source, "program", b"x", 0o777); artifact(source, "data", b"y", 0o600)
    receipt = seal.seal_artifact_set(source, store)
    snapshot = store / receipt.snapshot_id
    assert build_artifact_set(snapshot) == receipt.artifact_set
    assert {entry["mode"] for entry in receipt.artifact_set["entries"]} == {"0555", "0444"}


def test_store_parent_swap_after_fd_binding_fails_closed(tmp_path, monkeypatch):
    source, store, external = tmp_path / "source", tmp_path / "store", tmp_path / "external"
    source.mkdir(); store.mkdir(mode=0o700); external.mkdir(mode=0o700); artifact(source)
    moved = tmp_path / "store-held"
    real_mkdir, swapped = seal.os.mkdir, False
    def swap_on_staging(name, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if isinstance(name, str) and name.startswith(".staging-") and not swapped:
            swapped = True; store.rename(moved); os.symlink(external, store)
        return real_mkdir(name, mode, dir_fd=dir_fd)
    monkeypatch.setattr(seal.os, "mkdir", swap_on_staging)
    with pytest.raises(ModuleReleaseContractRejected, match="path changed"):
        seal.seal_artifact_set(source, store)
    assert [path for path in moved.iterdir() if not path.name.startswith(".staging-")]
    assert not list(external.iterdir())


def test_staged_symlink_injection_is_rejected_without_touching_external(tmp_path, monkeypatch):
    source, store, external = tmp_path / "source", tmp_path / "store", tmp_path / "external"
    source.mkdir(); external.mkdir(); artifact(source); artifact(external, "secret", b"outside")
    real_copy = seal._copy_source
    def copy_then_inject(source_fd, staging_fd):
        real_copy(source_fd, staging_fd)
        os.symlink(external / "secret", f"/proc/self/fd/{staging_fd}/injected")
    monkeypatch.setattr(seal, "_copy_source", copy_then_inject)
    with pytest.raises(ModuleReleaseContractRejected, match="symlink"):
        seal.seal_artifact_set(source, store)
    assert (external / "secret").read_bytes() == b"outside"


def test_new_store_parent_entry_is_fsynced_before_receipt(tmp_path, monkeypatch):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); artifact(source)
    parent_identity = os.stat(tmp_path)
    real_fsync, seen = seal.os.fsync, []
    def record(fd):
        current = os.fstat(fd)
        seen.append((current.st_dev, current.st_ino))
        return real_fsync(fd)
    monkeypatch.setattr(seal.os, "fsync", record)
    seal.seal_artifact_set(source, store)
    assert (parent_identity.st_dev, parent_identity.st_ino) in seen


def test_store_create_race_fsyncs_parent_after_fileexists(tmp_path, monkeypatch):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); store.mkdir(mode=0o700); artifact(source)
    parent_identity = os.stat(tmp_path)
    real_stat, real_fsync, first, seen = seal.os.stat, seal.os.fsync, True, []
    def race_stat(name, *args, **kwargs):
        nonlocal first
        if name == "store" and first:
            first = False
            raise FileNotFoundError()
        return real_stat(name, *args, **kwargs)
    def record(fd):
        current = os.fstat(fd); seen.append((current.st_dev, current.st_ino))
        return real_fsync(fd)
    monkeypatch.setattr(seal.os, "stat", race_stat)
    monkeypatch.setattr(seal.os, "fsync", record)
    seal.seal_artifact_set(source, store)
    assert (parent_identity.st_dev, parent_identity.st_ino) in seen


def test_snapshot_collision_fsyncs_store_before_receipt(tmp_path, monkeypatch):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); artifact(source)
    first = seal.seal_artifact_set(source, store)
    store_identity = os.stat(store)
    real_fsync, seen = seal.os.fsync, []
    def record(fd):
        current = os.fstat(fd); seen.append((current.st_dev, current.st_ino))
        return real_fsync(fd)
    monkeypatch.setattr(seal.os, "fsync", record)
    second = seal.seal_artifact_set(source, store)
    assert second.snapshot_id == first.snapshot_id
    assert (store_identity.st_dev, store_identity.st_ino) in seen


def test_copy_detects_source_change_during_read_and_removes_staging(tmp_path, monkeypatch):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); target = artifact(source, data=b"x" * 100_000)
    original_read, changed = seal.os.read, False
    def mutate(fd, size):
        nonlocal changed
        chunk = original_read(fd, size)
        if chunk and not changed:
            changed = True; target.write_bytes(b"changed")
        return chunk
    monkeypatch.setattr(seal.os, "read", mutate)
    with pytest.raises(ModuleReleaseContractRejected, match="changed during copy"):
        seal.seal_artifact_set(source, store)
    assert not list(store.glob(".staging-*"))


def test_fd_open_error_is_closed_and_reported(tmp_path, monkeypatch):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); artifact(source)
    real_open = seal.os.open
    def fail_file(name, flags, *args, **kwargs):
        if name == "asset.txt": raise OSError("injected file open failure")
        return real_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(seal.os, "open", fail_file)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(ModuleReleaseContractRejected, match="file cannot be created safely"):
        seal.seal_artifact_set(source, store)
    assert len(os.listdir("/proc/self/fd")) == before


def test_root_fstat_failure_closes_source_fd(tmp_path, monkeypatch):
    source = tmp_path / "source"; source.mkdir(); artifact(source)
    real_fstat, calls = seal.os.fstat, 0
    def fail_first(fd):
        nonlocal calls
        calls += 1
        if calls == 1: raise OSError("injected root fstat")
        return real_fstat(fd)
    monkeypatch.setattr(seal.os, "fstat", fail_first)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(ModuleReleaseContractRejected, match="source cannot be opened"):
        seal._open_source_directory(source)
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_second_open_failure_closes_first_descriptor(tmp_path, monkeypatch, kind):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir()
    if kind == "directory":
        (source / "child").mkdir(); artifact(source, "child/a.txt"); name = "child"
    else:
        artifact(source); name = "asset.txt"
    real_open, seen = seal.os.open, 0
    def fail_second(candidate, flags, *args, **kwargs):
        nonlocal seen
        if candidate == name:
            seen += 1
            if seen == 2: raise OSError("injected second open")
        return real_open(candidate, flags, *args, **kwargs)
    monkeypatch.setattr(seal.os, "open", fail_second)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(ModuleReleaseContractRejected):
        seal.seal_artifact_set(source, store)
    assert seen >= 2 and len(os.listdir("/proc/self/fd")) == before


def test_store_fstat_failure_closes_parent_and_store_fd(tmp_path, monkeypatch):
    store = tmp_path / "store"
    real_fstat, calls = seal.os.fstat, 0
    def fail_store(fd):
        nonlocal calls
        calls += 1
        if calls == 1: raise OSError("injected store fstat")
        return real_fstat(fd)
    monkeypatch.setattr(seal.os, "fstat", fail_store)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(OSError):
        seal._open_store(store)
    assert len(os.listdir("/proc/self/fd")) == before


def test_store_identity_close_error_never_recloses_reused_fd(tmp_path, monkeypatch):
    store = tmp_path / "store"; store.mkdir(mode=0o700)
    real_fstat, real_close = seal.os.fstat, seal.os.close
    released, interloper = False, []
    def mismatch(fd):
        value = real_fstat(fd)
        return type("Stat", (), {"st_dev": value.st_dev + 1, "st_ino": value.st_ino})()
    def release_then_raise(fd):
        nonlocal released
        if not released:
            released = True
            real_close(fd)
            interloper.append(os.open("/dev/null", os.O_RDONLY))
            raise OSError("close released fd then reported error")
        return real_close(fd)
    monkeypatch.setattr(seal.os, "fstat", mismatch)
    monkeypatch.setattr(seal.os, "close", release_then_raise)
    with pytest.raises(ModuleReleaseContractRejected, match="descriptor could not be closed"):
        seal._open_store(store)
    try:
        assert real_fstat(interloper[0]).st_mode
    finally:
        real_close(interloper[0])


def test_source_identity_close_error_never_recloses_reused_fd(tmp_path, monkeypatch):
    source = tmp_path / "source"; source.mkdir(); artifact(source)
    real_fstat, real_close = seal.os.fstat, seal.os.close
    released, interloper = False, []
    def mismatch(fd):
        value = real_fstat(fd)
        return type("Stat", (), {"st_dev": value.st_dev + 1, "st_ino": value.st_ino})()
    def release_then_raise(fd):
        nonlocal released
        if not released:
            released = True
            real_close(fd)
            interloper.append(os.open("/dev/null", os.O_RDONLY))
            raise OSError("close released fd then reported error")
        return real_close(fd)
    monkeypatch.setattr(seal.os, "fstat", mismatch)
    monkeypatch.setattr(seal.os, "close", release_then_raise)
    with pytest.raises(ModuleReleaseContractRejected, match="descriptor could not be closed"):
        seal._open_source_directory(source)
    try:
        assert real_fstat(interloper[0]).st_mode
    finally:
        real_close(interloper[0])


def test_cleanup_error_still_closes_store_and_parent_fds(tmp_path, monkeypatch):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(); artifact(source)
    monkeypatch.setattr(seal, "_copy_source", lambda *_args: (_ for _ in ()).throw(OSError("copy fails")))
    monkeypatch.setattr(seal, "_remove_tree", lambda *_args: (_ for _ in ()).throw(OSError("cleanup fails")))
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(OSError, match="cleanup fails"):
        seal.seal_artifact_set(source, store)
    assert len(os.listdir("/proc/self/fd")) == before
