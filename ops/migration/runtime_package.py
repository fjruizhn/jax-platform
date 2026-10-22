"""Import a sealed runtime surface as data, with an all-or-recovery outcome.

This module deliberately has no subprocess calls. The bootstrap program which
imports a package is installed separately from the package it consumes; systemd
reload and D-Bus verification are supplied as callbacks after every byte has
been staged. Consequently a package cannot replace the program interpreting it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Callable

SCHEMA = "axioma.platform-runtime-package/v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DROPIN = re.compile(r"dropins/(jax-platform(?:-migrate)?\.service)/([A-Za-z0-9][A-Za-z0-9_.-]*\.conf)\Z")
_HELPERS = {
    "helpers/pair_artifact_readiness.py": ("/usr/local/libexec/pair_artifact_readiness.py", "0644"),
    "helpers/pair-artifact-readiness.sh": ("/usr/local/libexec/pair-artifact-readiness.sh", "0755"),
    "helpers/jax-platform-pair-exec.py": ("/usr/local/libexec/jax-platform-pair-exec.py", "0644"),
    "helpers/jax-platform-pair-exec": ("/usr/local/libexec/jax-platform-pair-exec", "0755"),
    "helpers/install-platform-pair-release.sh": ("/usr/local/libexec/install-platform-pair-release.sh", "0755"),
    "helpers/activate-platform-pair-release.sh": ("/usr/local/libexec/activate-platform-pair-release.sh", "0755"),
}
_UNITS = {
    "units/jax-platform.service": "/etc/systemd/system/jax-platform.service",
    "units/jax-platform-migrate.service": "/etc/systemd/system/jax-platform-migrate.service",
}
_MANIFEST = "runtime-manifest.json"


class PackageError(Exception):
    """The package cannot be accepted or its transaction could not be restored."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_node(path: Path, *, directory: bool = False, owner_uid: int = 0, owner_gid: int = 0) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError as exc:
        raise PackageError("missing package node") from exc
    wanted = stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
    if not wanted or stat.S_ISLNK(value.st_mode) or (not directory and value.st_nlink != 1):
        raise PackageError("unsafe package node")
    if value.st_uid != owner_uid or value.st_gid != owner_gid or value.st_mode & 0o022:
        raise PackageError("unsafe package ownership")
    return value


def _role_destination(path: str, role: str, destination_root: Path | None = None) -> tuple[Path, str]:
    root = Path("/") if destination_root is None else destination_root
    if path in _HELPERS and role == "helper":
        destination, mode = _HELPERS[path]
        return root / destination.lstrip("/"), mode
    if path in _UNITS and role == "unit":
        return root / _UNITS[path].lstrip("/"), "0644"
    match = _DROPIN.fullmatch(path)
    if match and role == "dropin":
        return root / "etc/systemd/system" / f"{match.group(1)}.d" / match.group(2), "0644"
    if path == _MANIFEST and role == "manifest":
        return root / "usr/local/libexec/axioma-platform-runtime-package.json", "0644"
    raise PackageError("destination role")


def _canonical_manifest(raw: bytes, expected_digest: str) -> dict[str, object]:
    if not _HEX.fullmatch(expected_digest) or not raw.endswith(b"\n") or hashlib.sha256(raw).hexdigest() != expected_digest:
        raise PackageError("manifest digest")
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError("manifest json") from exc
    if json.dumps(data, sort_keys=True, separators=(",", ":")).encode() + b"\n" != raw:
        raise PackageError("noncanonical manifest")
    if not isinstance(data, dict) or set(data) != {"schema", "payload"} or data["schema"] != SCHEMA:
        raise PackageError("manifest schema")
    return data


def validate(root: Path, expected_digest: str, *, owner_uid: int = 0, owner_gid: int = 0) -> dict[str, object]:
    """Validate every package byte and its closed destination contract."""
    _safe_node(root, directory=True, owner_uid=owner_uid, owner_gid=owner_gid)
    manifest = root / "manifest.json"
    _safe_node(manifest, owner_uid=owner_uid, owner_gid=owner_gid)
    data = _canonical_manifest(manifest.read_bytes(), expected_digest)
    payload = data["payload"]
    if not isinstance(payload, list) or not payload:
        raise PackageError("payload")
    paths: list[str] = []
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"path", "role", "sha256", "size", "mode", "owner"}:
            raise PackageError("entry")
        path, role = item["path"], item["role"]
        if not isinstance(path, str) or not isinstance(role, str) or not path or path.startswith("/") or ".." in Path(path).parts:
            raise PackageError("path")
        _, required_mode = _role_destination(path, role)
        if (not _HEX.fullmatch(item["sha256"] if isinstance(item["sha256"], str) else "")
                or not isinstance(item["size"], int) or item["size"] < 0
                or item["mode"] != required_mode or item["owner"] != "root:root"):
            raise PackageError("entry metadata")
        entry = root / path
        value = _safe_node(entry, owner_uid=owner_uid, owner_gid=owner_gid)
        if value.st_size != item["size"] or f"{stat.S_IMODE(value.st_mode):04o}" != required_mode or _sha(entry) != item["sha256"]:
            raise PackageError("payload mismatch")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise PackageError("payload order")
    actual: set[str] = set()
    for base, directories, names in os.walk(root, topdown=True, followlinks=False):
        directories.sort()
        names.sort()
        for name in directories:
            _safe_node(Path(base) / name, directory=True, owner_uid=owner_uid, owner_gid=owner_gid)
        for name in names:
            entry = Path(base) / name
            _safe_node(entry, owner_uid=owner_uid, owner_gid=owner_gid)
            actual.add(entry.relative_to(root).as_posix())
    if actual != set(paths) | {"manifest.json"}:
        raise PackageError("extra payload")
    return data


def _write_file(path: Path, contents: bytes, *, mode: int, uid: int, gid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def snapshot(paths: dict[str, Path], backup: Path) -> None:
    """Capture bytes, owner and mode, including explicit ABSENT state."""
    backup.mkdir(mode=0o700)
    records: dict[str, dict[str, object]] = {}
    for name, path in sorted(paths.items()):
        record = backup / "files" / name
        try:
            value = path.lstat()
        except FileNotFoundError:
            records[name] = {"absent": True}
            continue
        if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode) or value.st_nlink != 1:
            raise PackageError("unsafe destination state")
        record.parent.mkdir(parents=True, exist_ok=True)
        _write_file(record, path.read_bytes(), mode=0o600, uid=os.geteuid(), gid=os.getegid())
        records[name] = {"absent": False, "mode": stat.S_IMODE(value.st_mode), "uid": value.st_uid, "gid": value.st_gid}
    _write_file(backup / "snapshot.json", json.dumps(records, sort_keys=True, separators=(",", ":")).encode() + b"\n", mode=0o600, uid=os.geteuid(), gid=os.getegid())


def restore(paths: dict[str, Path], backup: Path) -> None:
    try:
        records = json.loads((backup / "snapshot.json").read_text(encoding="utf-8"))
        if not isinstance(records, dict) or set(records) != set(paths):
            raise ValueError("shape")
        for name, path in sorted(paths.items(), reverse=True):
            record = records[name]
            if not isinstance(record, dict):
                raise ValueError("record")
            if record.get("absent") is True:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if set(record) != {"absent", "mode", "uid", "gid"} or record["absent"] is not False:
                raise ValueError("record")
            source = backup / "files" / name
            _write_file(path, source.read_bytes(), mode=record["mode"], uid=record["uid"], gid=record["gid"])
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PackageError("RECOVERY_REQUIRED") from exc


def _destinations(data: dict[str, object], root: Path) -> dict[str, Path]:
    declared: dict[str, Path] = {}
    for item in data["payload"]:
        assert isinstance(item, dict)
        path = item["path"]
        assert isinstance(path, str)
        declared[path] = _role_destination(path, item["role"], root)[0]
    for unit in ("jax-platform.service", "jax-platform-migrate.service"):
        folder = root / "etc/systemd/system" / f"{unit}.d"
        if not folder.exists():
            continue
        if folder.is_symlink() or not folder.is_dir():
            raise PackageError("unsafe dropin directory")
        for entry in folder.iterdir():
            if entry.is_symlink() or not entry.is_file() or not entry.name.endswith(".conf"):
                raise PackageError("unsafe dropin state")
            declared.setdefault(f"dropins/{unit}/{entry.name}", entry)
    return declared


def import_package(bundle: Path, digest: str, destination_root: Path, backup: Path,
                   verify_effective: Callable[[], None], *, fail_after: int | None = None,
                   verify_restored: Callable[[], None] | None = None,
                   package_owner_uid: int = 0, package_owner_gid: int = 0,
                   install_owner_uid: int = 0, install_owner_gid: int = 0) -> None:
    """Commit all files, D-Bus verification included, or restore every file."""
    data = validate(bundle, digest, owner_uid=package_owner_uid, owner_gid=package_owner_gid)
    destinations = _destinations(data, destination_root)
    snapshot(destinations, backup)
    declared = {entry["path"] for entry in data["payload"] if isinstance(entry, dict)}
    swaps = 0
    try:
        for item in data["payload"]:
            assert isinstance(item, dict)
            path, destination = item["path"], destinations[item["path"]]
            assert isinstance(path, str)
            _write_file(destination, (bundle / path).read_bytes(), mode=int(item["mode"], 8), uid=install_owner_uid, gid=install_owner_gid)
            swaps += 1
            if fail_after == swaps:
                raise PackageError("injected swap failure")
        for path, destination in destinations.items():
            if path not in declared:
                destination.unlink()
        verify_effective()
    except BaseException as original:
        try:
            restore(destinations, backup)
            if verify_restored is not None:
                verify_restored()
        except BaseException as rollback_error:
            raise PackageError("RECOVERY_REQUIRED") from rollback_error
        raise original
