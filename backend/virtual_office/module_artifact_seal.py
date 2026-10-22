"""Durable, descriptor-bound snapshots for module candidate bytes only."""
from __future__ import annotations

import errno
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .module_release_contract import (
    _MAX_DIRECTORIES, _MAX_FILE_BYTES, _MAX_FILES, _MAX_TOTAL_BYTES,
    ModuleReleaseContractRejected, _path, build_artifact_set, validate_artifact_set,
)

_SNAPSHOT_ID = re.compile(r"^[a-f0-9]{64}$")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


@dataclass(frozen=True)
class SealedArtifactReceipt:
    snapshot_id: str
    root_digest: str
    artifact_set: dict[str, Any]
    sealed_at: str


def _reject(message: str, error: OSError | None = None) -> None:
    if error is None:
        raise ModuleReleaseContractRejected(message)
    raise ModuleReleaseContractRejected(message) from error


def _same(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _open_source_directory(path: Path) -> int:
    fd: int | None = None
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            _reject("seal source must be a real directory")
        fd = os.open(path, _DIR_FLAGS)
        if not _same(before, os.fstat(fd)):
            closing_fd = fd
            fd = None
            try:
                os.close(closing_fd)
            except OSError as error:
                _reject("seal source descriptor could not be closed", error)
            _reject("seal source changed before copy")
        return fd
    except OSError as error:
        if fd is not None:
            os.close(fd)
        _reject("seal source cannot be opened safely", error)


def _open_store(store_path: Path) -> tuple[int, int]:
    """Bind a private store to a held parent fd and sync a new parent entry."""
    name = store_path.name
    if name in {"", ".", ".."}:
        _reject("sealed artifact store path is invalid")
    try:
        parent_fd = os.open(store_path.parent, _DIR_FLAGS)
    except OSError as error:
        _reject("sealed artifact store parent cannot be opened safely", error)
    try:
        try:
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileExistsError:
                # Another sealer won the create race.  Bind and validate its
                # directory below; never assume it is private or trustworthy.
                os.fsync(parent_fd)
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            _reject("sealed artifact store must be a real directory")
        if stat.S_IMODE(before.st_mode) & 0o077:
            _reject("sealed artifact store must be private")
        store_fd: int | None = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
        if not _same(before, os.fstat(store_fd)):
            # Transfer ownership before close: close(2) can release an fd and
            # still report an error, so an exception handler must never retry
            # its numeric value after another thread has reused it.
            closing_fd = store_fd
            store_fd = None
            try:
                os.close(closing_fd)
            except OSError as error:
                _reject("sealed artifact store descriptor could not be closed", error)
            _reject("sealed artifact store changed before use")
        return parent_fd, store_fd
    except BaseException:
        if "store_fd" in locals() and store_fd is not None:
            try:
                os.close(store_fd)
            except OSError:
                pass
        os.close(parent_fd)
        raise


def _assert_store_path_bound(store_path: Path, store_fd: int) -> None:
    """Fail closed if the caller's requested locator no longer names our fd."""
    try:
        named = store_path.lstat()
        opened = os.fstat(store_fd)
    except OSError as error:
        _reject("sealed artifact store path cannot be revalidated", error)
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode) or not _same(named, opened):
        _reject("sealed artifact store path changed during sealing")


def _names(fd: int) -> list[str]:
    try:
        with os.scandir(fd) as listing:
            result = []
            for entry in listing:
                entry.name.encode("utf-8")
                result.append(entry.name)
            return sorted(result, key=lambda value: value.encode("utf-8"))
    except UnicodeEncodeError as error:
        _reject("seal source contains a non-UTF-8 path", error)
    except OSError as error:
        _reject("seal directory cannot be read safely", error)


def _copy_source(source_fd: int, target_fd: int) -> None:
    count = total = entries = 0
    directories = 1

    def copy_directory(source_dir: int, target_dir: int, parent: tuple[str, ...]) -> None:
        nonlocal count, total, entries, directories
        for name in _names(source_dir):
            entries += 1
            if entries > _MAX_FILES + _MAX_DIRECTORIES - 1:
                _reject("seal source exceeds entry count limit")
            rendered = _path(parent + (name,))
            try:
                before = os.stat(name, dir_fd=source_dir, follow_symlinks=False)
            except OSError as error:
                _reject(f"seal source changed before copy: {rendered}", error)
            if stat.S_ISLNK(before.st_mode):
                _reject(f"seal source contains symlink: {rendered}")
            if stat.S_ISDIR(before.st_mode):
                if directories >= _MAX_DIRECTORIES:
                    _reject("seal source exceeds directory count limit")
                directories += 1
                child_target: int | None = None
                child_source: int | None = None
                try:
                    os.mkdir(name, 0o700, dir_fd=target_dir)
                    child_target = os.open(name, _DIR_FLAGS, dir_fd=target_dir)
                    child_source = os.open(name, _DIR_FLAGS, dir_fd=source_dir)
                except OSError as error:
                    if child_source is not None:
                        os.close(child_source)
                    if child_target is not None:
                        os.close(child_target)
                    _reject(f"seal directory cannot be created safely: {rendered}", error)
                try:
                    if not _same(before, os.fstat(child_source)):
                        _reject(f"seal source directory changed before copy: {rendered}")
                    copy_directory(child_source, child_target, parent + (name,))
                finally:
                    try:
                        if child_source is not None:
                            os.close(child_source)
                    finally:
                        if child_target is not None:
                            os.close(child_target)
                continue
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                _reject(f"seal source must contain unlinked regular files: {rendered}")
            if count >= _MAX_FILES or before.st_size > _MAX_FILE_BYTES or total + before.st_size > _MAX_TOTAL_BYTES:
                _reject("seal source exceeds artifact limits")
            source_file: int | None = None
            target_file: int | None = None
            try:
                source_file = os.open(name, _FILE_FLAGS, dir_fd=source_dir)
                target_file = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=target_dir)
            except OSError as error:
                if target_file is not None:
                    os.close(target_file)
                if source_file is not None:
                    os.close(source_file)
                _reject(f"sealed file cannot be created safely: {rendered}", error)
            try:
                opened = os.fstat(source_file)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
                    _reject(f"seal source file changed before copy: {rendered}")
                copied = 0
                while chunk := os.read(source_file, 64 * 1024):
                    copied += len(chunk)
                    if copied > _MAX_FILE_BYTES or total + copied > _MAX_TOTAL_BYTES:
                        _reject("seal source exceeds artifact limits")
                    offset = 0
                    while offset < len(chunk):
                        written = os.write(target_file, chunk[offset:])
                        if written <= 0:
                            _reject("short write while sealing artifact")
                        offset += written
                os.fsync(target_file)
                after = os.fstat(source_file)
                if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) or copied != before.st_size:
                    _reject(f"seal source file changed during copy: {rendered}")
                os.fchmod(target_file, 0o755 if stat.S_IMODE(before.st_mode) & 0o111 else 0o644)
            finally:
                try:
                    if target_file is not None:
                        os.close(target_file)
                finally:
                    if source_file is not None:
                        os.close(source_file)
            count += 1
            total += copied

    copy_directory(source_fd, target_fd, ())


def _lock_snapshot(fd: int) -> None:
    for name in _names(fd):
        try:
            item = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError as error:
            _reject("sealed snapshot changed before lock", error)
        if stat.S_ISLNK(item.st_mode):
            _reject("sealed snapshot contains symlink")
        if stat.S_ISDIR(item.st_mode):
            child = os.open(name, _DIR_FLAGS, dir_fd=fd)
            try:
                if not _same(item, os.fstat(child)):
                    _reject("sealed snapshot directory changed before lock")
                _lock_snapshot(child)
                os.fchmod(child, 0o500)
            finally:
                os.close(child)
        elif stat.S_ISREG(item.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                if not _same(item, os.fstat(child)):
                    _reject("sealed snapshot file changed before lock")
                os.fchmod(child, 0o555 if stat.S_IMODE(item.st_mode) & 0o111 else 0o444)
                os.fsync(child)
            finally:
                os.close(child)
        else:
            _reject("sealed snapshot contains non-regular file")
    os.fchmod(fd, 0o500)
    os.fsync(fd)


def _remove_tree(parent_fd: int, name: str) -> None:
    """Descriptor-relative cleanup of only this invocation's staging name."""
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    try:
        # A failed seal may already have made the tree read-only.  The held fd
        # binds this chmod to our own directory rather than a mutable path.
        os.fchmod(fd, 0o700)
        for child in _names(fd):
            item = os.stat(child, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(item.st_mode):
                _remove_tree(fd, child)
            else:
                os.unlink(child, dir_fd=fd)
        try:
            os.fsync(fd)
        except OSError:
            # Cleanup follows a prior failure.  Preserve the original failure
            # and still remove the private staging entry.
            pass
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent_fd)
    try:
        os.fsync(parent_fd)
    except OSError:
        pass


def _artifact_from_fd(fd: int) -> dict[str, Any]:
    return build_artifact_set(Path(f"/proc/self/fd/{fd}"), _allow_proc_fd=True)


def seal_artifact_set(source: str | Path, store: str | Path) -> SealedArtifactReceipt:
    """Copy, lock, install and rehash candidate bytes; this does not publish."""
    source_path, store_path = Path(source), Path(store)
    try:
        source_real, store_candidate = source_path.resolve(strict=True), store_path.resolve(strict=False)
        if source_real == store_candidate or source_real.is_relative_to(store_candidate) or store_candidate.is_relative_to(source_real):
            _reject("seal source and store must not overlap")
    except OSError as error:
        _reject("seal source cannot be resolved", error)
    parent_fd, store_fd = _open_store(store_path)
    staging_name = f".staging-{uuid.uuid4().hex}"
    installed = False
    try:
        os.mkdir(staging_name, 0o700, dir_fd=store_fd)
        staging_fd = os.open(staging_name, _DIR_FLAGS, dir_fd=store_fd)
        try:
            source_fd = _open_source_directory(source_path)
            try:
                _copy_source(source_fd, staging_fd)
            finally:
                os.close(source_fd)
            _lock_snapshot(staging_fd)
            artifact_set = _artifact_from_fd(staging_fd)
            snapshot_id = artifact_set["root_digest"].removeprefix("sha256:")
            try:
                os.rename(staging_name, snapshot_id, src_dir_fd=store_fd, dst_dir_fd=store_fd)
                installed = True
            except OSError as error:
                if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
            # This is required both for our rename and for a collision winner
            # whose receipt we are about to reuse.
            os.fsync(store_fd)
            snapshot_fd = os.open(snapshot_id, _DIR_FLAGS, dir_fd=store_fd)
            try:
                verified = _artifact_from_fd(snapshot_fd)
            finally:
                os.close(snapshot_fd)
            if verified != artifact_set:
                _reject("sealed snapshot changed before receipt")
            _assert_store_path_bound(store_path, store_fd)
            return SealedArtifactReceipt(snapshot_id, artifact_set["root_digest"], artifact_set, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        finally:
            os.close(staging_fd)
    finally:
        try:
            if not installed:
                _remove_tree(store_fd, staging_name)
        finally:
            try:
                os.close(store_fd)
            finally:
                os.close(parent_fd)


def verify_sealed_artifact(receipt: SealedArtifactReceipt, store: str | Path) -> None:
    if not isinstance(receipt, SealedArtifactReceipt):
        _reject("invalid sealed artifact receipt")
    validate_artifact_set(receipt.artifact_set)
    digest = receipt.root_digest.removeprefix("sha256:") if isinstance(receipt.root_digest, str) else ""
    if not (_SNAPSHOT_ID.fullmatch(receipt.snapshot_id) and _SNAPSHOT_ID.fullmatch(digest) and receipt.snapshot_id == digest):
        _reject("invalid sealed artifact receipt")
    parent_fd, store_fd = _open_store(Path(store))
    try:
        _assert_store_path_bound(Path(store), store_fd)
        snapshot_fd = os.open(receipt.snapshot_id, _DIR_FLAGS, dir_fd=store_fd)
        try:
            actual = _artifact_from_fd(snapshot_fd)
        finally:
            os.close(snapshot_fd)
    finally:
        try:
            os.close(store_fd)
        finally:
            os.close(parent_fd)
    if actual != receipt.artifact_set or actual["root_digest"] != receipt.root_digest:
        _reject("sealed artifact verification failed")
