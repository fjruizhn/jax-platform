"""Sealed artifact-set/v1 package boundary; this does not publish a module."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DOMAIN = "axioma.office.module-artifact"
_MAX_FILES, _MAX_DIRECTORIES = 10_000, 10_000
_MAX_FILE_BYTES, _MAX_TOTAL_BYTES = 1 << 30, 2 << 30
_SAFE_MODES = {0o444, 0o555, 0o644, 0o755}


class ModuleReleaseContractRejected(ValueError):
    pass


@dataclass(frozen=True)
class PublicationContractStatus:
    state: str
    artifact_digest: str
    pending: tuple[str, ...]


def _reject(message: str) -> None:
    raise ModuleReleaseContractRejected(message)


def _json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _path(parts: tuple[str, ...]) -> str:
    if not parts or any(part in {"", ".", ".."} for part in parts): _reject("artifact path is not a relative POSIX path")
    value = "/".join(parts)
    if "\\" in value or any(ord(c) <= 31 or 127 <= ord(c) <= 159 for c in value): _reject("artifact path contains a forbidden character")
    if unicodedata.normalize("NFC", value) != value: _reject("artifact path must be NFC")
    if len(value.encode()) > 1024: _reject("artifact path exceeds 1024 UTF-8 bytes")
    return value


def _stable(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, stat.S_IMODE(value.st_mode), value.st_mtime_ns, value.st_ctime_ns


def _frame(fd: int, parts: tuple[str, ...]) -> dict[str, Any]:
    before = os.fstat(fd)
    try:
        with os.scandir(fd) as listing: names = [entry.name for entry in listing]
    except OSError as error:
        raise ModuleReleaseContractRejected("artifact directory cannot be read safely") from error
    names.sort(key=lambda value: value.encode())
    return {"fd": fd, "parts": parts, "before": before, "names": names, "index": 0}


def build_artifact_set(root: str | Path) -> dict[str, Any]:
    """Hash a bounded, descriptor-relative package snapshot with artifact-set/v1."""
    root_path = Path(root)
    try: root_stat = root_path.lstat()
    except OSError as error: raise ModuleReleaseContractRejected("artifact root cannot be inspected") from error
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode): _reject("artifact root must be a real directory")
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try: root_fd = os.open(root_path, dir_flags)
    except OSError as error: raise ModuleReleaseContractRejected("artifact root cannot be opened safely") from error
    stack: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []; folded: set[str] = set()
    total = enumerated = 0; directories = 1
    try:
        opened_root = os.fstat(root_fd)
        if (opened_root.st_dev, opened_root.st_ino) != (root_stat.st_dev, root_stat.st_ino): _reject("artifact root changed before traversal")
        stack.append(_frame(root_fd, ()))
        while stack:
            current = stack[-1]
            if current["index"] == len(current["names"]):
                after = os.fstat(current["fd"])
                with os.scandir(current["fd"]) as listing: names_after = sorted((entry.name for entry in listing), key=lambda value: value.encode())
                if names_after != current["names"] or after.st_mtime_ns != current["before"].st_mtime_ns or after.st_ctime_ns != current["before"].st_ctime_ns: _reject("artifact directory changed during traversal")
                os.close(current["fd"]); stack.pop(); continue
            name = current["names"][current["index"]]; current["index"] += 1; enumerated += 1
            if enumerated > _MAX_FILES + _MAX_DIRECTORIES - 1: _reject("artifact exceeds entry count limit")
            path = _path(current["parts"] + (name,))
            try: before = os.stat(name, dir_fd=current["fd"], follow_symlinks=False)
            except OSError as error: raise ModuleReleaseContractRejected("artifact changed during traversal") from error
            if stat.S_ISLNK(before.st_mode): _reject(f"artifact contains symlink: {path}")
            if stat.S_ISDIR(before.st_mode):
                if directories >= _MAX_DIRECTORIES: _reject("artifact exceeds directory count limit")
                try: child_fd = os.open(name, dir_flags, dir_fd=current["fd"])
                except OSError as error: raise ModuleReleaseContractRejected(f"artifact directory cannot be opened safely: {path}") from error
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino): os.close(child_fd); _reject(f"artifact directory changed during traversal: {path}")
                directories += 1; stack.append(_frame(child_fd, current["parts"] + (name,))); continue
            if not stat.S_ISREG(before.st_mode): _reject(f"artifact contains non-regular file: {path}")
            if before.st_nlink != 1: _reject(f"artifact contains hard-linked file: {path}")
            mode = stat.S_IMODE(before.st_mode)
            if mode not in _SAFE_MODES: _reject(f"artifact file mode is not allowed: {path}")
            if before.st_size > _MAX_FILE_BYTES or total + before.st_size > _MAX_TOTAL_BYTES: _reject("artifact exceeds byte limit")
            if len(entries) >= _MAX_FILES: _reject("artifact exceeds file count limit")
            try: file_fd = os.open(name, file_flags, dir_fd=current["fd"])
            except OSError as error: raise ModuleReleaseContractRejected(f"artifact file cannot be opened safely: {path}") from error
            try:
                opened = os.fstat(file_fd)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or _stable(opened) != _stable(before): _reject(f"artifact file changed before read: {path}")
                hashed = hashlib.sha256(); count = 0
                while chunk := os.read(file_fd, 64 * 1024):
                    count += len(chunk)
                    if count > _MAX_FILE_BYTES: _reject(f"artifact file exceeds byte limit: {path}")
                    hashed.update(chunk)
                if _stable(os.fstat(file_fd)) != _stable(before) or count != before.st_size: _reject(f"artifact file changed during read: {path}")
            finally: os.close(file_fd)
            key = path.casefold()
            if key in folded: _reject(f"artifact has case-fold path collision: {path}")
            folded.add(key); total += count
            entries.append({"path": path, "size": count, "mode": f"{mode:04o}", "digest": f"sha256:{hashed.hexdigest()}"})
    finally:
        for current in stack:
            try: os.close(current["fd"])
            except OSError: pass
    if not entries: _reject("artifact must contain at least one file")
    entries.sort(key=lambda entry: entry["path"].encode())
    payload = {"domain": _DOMAIN, "entries": entries, "schema_version": 1}
    return {**payload, "root_digest": _digest(_json(payload))}


def validate_artifact_set(artifact_set: dict[str, Any]) -> None:
    if set(artifact_set) != {"schema_version", "domain", "entries", "root_digest"} or artifact_set.get("schema_version") != 1 or artifact_set.get("domain") != _DOMAIN: _reject("artifact set is not artifact-set/v1")
    entries = artifact_set.get("entries")
    if not isinstance(entries, list) or not entries or len(entries) > _MAX_FILES: _reject("artifact inventory has invalid entry count")
    paths: list[str] = []; total = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "mode", "digest"}: _reject("artifact inventory entry is invalid")
        path = entry["path"]
        if not isinstance(path, str) or _path(tuple(path.split("/"))) != path: _reject("artifact inventory path is invalid")
        if not isinstance(entry["size"], int) or isinstance(entry["size"], bool) or not 0 <= entry["size"] <= _MAX_FILE_BYTES: _reject("artifact inventory size is invalid")
        digest = entry["digest"]
        if entry["mode"] not in {"0444", "0555", "0644", "0755"} or not isinstance(digest, str) or len(digest) != 71 or not digest.startswith("sha256:") or digest != digest.lower():
            _reject("artifact inventory mode or digest is invalid")
        try: int(digest[7:], 16)
        except ValueError as error: raise ModuleReleaseContractRejected("artifact inventory digest is invalid") from error
        paths.append(path); total += entry["size"]
    if total > _MAX_TOTAL_BYTES or paths != sorted(paths, key=lambda value: value.encode()) or len(set(paths)) != len(paths) or len({path.casefold() for path in paths}) != len(paths): _reject("artifact inventory ordering, uniqueness, or total is invalid")
    payload = {key: artifact_set[key] for key in ("domain", "entries", "schema_version")}
    if artifact_set.get("root_digest") != _digest(_json(payload)): _reject("artifact root digest does not match canonical inventory")


def verify_artifact_directory(root: str | Path, artifact_set: dict[str, Any]) -> None:
    validate_artifact_set(artifact_set)
    if build_artifact_set(root) != artifact_set: _reject("artifact directory does not exactly match its inventory")


def publication_contract_status(root: str | Path) -> PublicationContractStatus:
    artifact = build_artifact_set(root)
    return PublicationContractStatus("PENDING_CONTRACT", artifact["root_digest"], ("manifest_v2", "license_evidence", "review_records", "signature_scope", "office_release_decision"))
