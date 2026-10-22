"""Import a sealed runtime surface as data, with an all-or-recovery outcome.

The transaction core deliberately has no subprocess calls. The bootstrap program
which imports a package is installed separately from the package it consumes;
systemd reload and D-Bus verification are supplied as callbacks after every byte
has been staged. Consequently a package cannot replace the program interpreting
it.
"""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable
from contextlib import contextmanager

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
_REQUIRED_PATHS = frozenset((*_HELPERS, *_UNITS, _MANIFEST))


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


def _read_regular_fd(path: Path, *, owner_uid: int, owner_gid: int) -> tuple[os.stat_result, bytes]:
    """Open once without following a final symlink, then validate those bytes."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise PackageError("missing package node") from exc
    try:
        value = os.fstat(descriptor)
        if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1
                or value.st_uid != owner_uid or value.st_gid != owner_gid or value.st_mode & 0o022):
            raise PackageError("unsafe package node")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return value, b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _trusted_ancestors(path: Path, *, owner_uid: int = 0, owner_gid: int = 0) -> None:
    """Reject a package whose pathname can be replaced through an ancestor."""
    current = path.parent
    while True:
        _safe_node(current, directory=True, owner_uid=owner_uid, owner_gid=owner_gid)
        if current.parent == current:
            return
        current = current.parent


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


def _load(root: Path, expected_digest: str, *, owner_uid: int = 0, owner_gid: int = 0) -> tuple[dict[str, object], dict[str, bytes]]:
    """Validate every package byte and its closed destination contract."""
    _safe_node(root, directory=True, owner_uid=owner_uid, owner_gid=owner_gid)
    if owner_uid == 0 and owner_gid == 0:
        _trusted_ancestors(root, owner_uid=owner_uid, owner_gid=owner_gid)
    manifest = root / "manifest.json"
    _, manifest_bytes = _read_regular_fd(manifest, owner_uid=owner_uid, owner_gid=owner_gid)
    data = _canonical_manifest(manifest_bytes, expected_digest)
    payload = data["payload"]
    if not isinstance(payload, list) or not payload:
        raise PackageError("payload")
    paths: list[str] = []
    sealed_bytes: dict[str, bytes] = {}
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
        value, contents = _read_regular_fd(entry, owner_uid=owner_uid, owner_gid=owner_gid)
        if value.st_size != item["size"] or len(contents) != item["size"] or f"{stat.S_IMODE(value.st_mode):04o}" != required_mode or hashlib.sha256(contents).hexdigest() != item["sha256"]:
            raise PackageError("payload mismatch")
        paths.append(path)
        sealed_bytes[path] = contents
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise PackageError("payload order")
    if not _REQUIRED_PATHS.issubset(paths):
        raise PackageError("incomplete runtime package")
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
    return data, sealed_bytes


def validate(root: Path, expected_digest: str, *, owner_uid: int = 0, owner_gid: int = 0) -> dict[str, object]:
    """Validate every package byte and its closed destination contract."""
    return _load(root, expected_digest, owner_uid=owner_uid, owner_gid=owner_gid)[0]


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
    try:
        backup.mkdir(mode=0o700)
    except FileExistsError:
        try:
            value = backup.lstat()
        except OSError as exc:
            raise PackageError("unsafe backup directory") from exc
        if (not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode)
                or value.st_uid != 0 or value.st_gid != 0 or value.st_mode & 0o077
                or any(backup.iterdir())):
            raise PackageError("unsafe backup directory")
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


def _journal(backup: Path, phase: str, paths: dict[str, Path] | None = None) -> None:
    journal = backup / "journal.json"
    try:
        data = json.loads(journal.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    data["phase"] = phase
    if paths is not None:
        data["destinations"] = {name: str(path) for name, path in paths.items()}
    _write_file(journal, json.dumps(data, sort_keys=True, separators=(",", ":")).encode() + b"\n", mode=0o600, uid=os.geteuid(), gid=os.getegid())


def recover_incomplete(backup: Path, verify_restored: Callable[[Path], None] | None = None) -> None:
    try:
        data = json.loads((backup / "journal.json").read_text(encoding="utf-8"))
        phase, raw_paths = data.get("phase"), data.get("destinations")
        if phase in {"VERIFIED", "ROLLED_BACK"}:
            return
        if not isinstance(raw_paths, dict) or not all(isinstance(name, str) and isinstance(path, str) and path.startswith("/") for name, path in raw_paths.items()):
            raise ValueError("journal")
        restore({name: Path(path) for name, path in raw_paths.items()}, backup)
        if verify_restored is not None:
            verify_restored(_snapshot_unit_source(backup))
        _journal(backup, "ROLLED_BACK")
    except (OSError, ValueError, TypeError, json.JSONDecodeError, PackageError) as exc:
        raise PackageError("RECOVERY_REQUIRED") from exc


def recover_pending_imports(parent: Path, current: Path, verify_restored: Callable[[Path], None] | None = None) -> None:
    try:
        entries = sorted(parent.iterdir())
    except OSError as exc:
        raise PackageError("RECOVERY_REQUIRED") from exc
    for entry in entries:
        if entry != current and entry.is_dir() and (entry / "journal.json").is_file():
            recover_incomplete(entry, verify_restored)


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


def _sealed_unit_source(directory: Path, units: dict[str, bytes]) -> Path:
    """Write unit bytes already sealed by _load into a root-private source tree."""
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    for package_path in _UNITS:
        _write_file(directory / Path(package_path).name, units[package_path], mode=0o600, uid=os.geteuid(), gid=os.getegid())
    return directory


def _snapshot_unit_source(backup: Path) -> Path:
    """Materialize exactly the pre-import unit bytes for rollback verification."""
    try:
        records = json.loads((backup / "snapshot.json").read_text(encoding="utf-8"))
        units = {path: (backup / "files" / path).read_bytes() for path in _UNITS
                 if records.get(path, {}).get("absent") is False}
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PackageError("RECOVERY_REQUIRED") from exc
    if set(units) != set(_UNITS):
        raise PackageError("RECOVERY_REQUIRED")
    return _sealed_unit_source(backup / "restored-units", units)


def import_package(bundle: Path, digest: str, destination_root: Path, backup: Path,
                   verify_effective: Callable[[Path], None], *, fail_after: int | None = None,
                   verify_restored: Callable[[Path], None] | None = None,
                   package_owner_uid: int = 0, package_owner_gid: int = 0,
                   install_owner_uid: int = 0, install_owner_gid: int = 0) -> None:
    """Commit all files, D-Bus verification included, or restore every file."""
    data, sealed_bytes = _load(bundle, digest, owner_uid=package_owner_uid, owner_gid=package_owner_gid)
    destinations = _destinations(data, destination_root)
    snapshot(destinations, backup)
    candidate_units = _sealed_unit_source(backup / "candidate-units", sealed_bytes)
    restored_units = _snapshot_unit_source(backup) if verify_restored is not None else None
    _journal(backup, "PREPARED", destinations)
    declared = {entry["path"] for entry in data["payload"] if isinstance(entry, dict)}
    swaps = 0
    try:
        _journal(backup, "MUTATING")
        for item in data["payload"]:
            assert isinstance(item, dict)
            path, destination = item["path"], destinations[item["path"]]
            assert isinstance(path, str)
            _write_file(destination, sealed_bytes[path], mode=int(item["mode"], 8), uid=install_owner_uid, gid=install_owner_gid)
            swaps += 1
            if fail_after == swaps:
                raise PackageError("injected swap failure")
        for path, destination in destinations.items():
            if path not in declared:
                destination.unlink()
        verify_effective(candidate_units)
        _journal(backup, "VERIFIED")
    except BaseException as original:
        try:
            _journal(backup, "ROLLING_BACK")
            restore(destinations, backup)
            if verify_restored is not None:
                assert restored_units is not None
                verify_restored(restored_units)
            _journal(backup, "ROLLED_BACK")
        except BaseException as rollback_error:
            _journal(backup, "RECOVERY_REQUIRED")
            raise PackageError("RECOVERY_REQUIRED") from rollback_error
        raise original


def _trusted_program(path: Path, digest: str) -> int:
    if not path.is_absolute() or not _HEX.fullmatch(digest):
        raise PackageError("invalid bootstrap dependency")
    _trusted_ancestors(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PackageError("untrusted bootstrap dependency") from exc
    value = os.fstat(descriptor)
    try:
        if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_uid != 0 or value.st_gid != 0
                or value.st_mode & 0o022 or stat.S_IMODE(value.st_mode) not in {0o700, 0o750, 0o755}):
            raise PackageError("untrusted bootstrap dependency")
        digest_bytes = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest_bytes.update(chunk)
        if digest_bytes.hexdigest() != digest:
            raise PackageError("untrusted bootstrap dependency")
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.set_inheritable(descriptor, True)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _run_checked(command: list[str], *, pass_fds: tuple[int, ...] = ()) -> None:
    try:
        subprocess.run(command, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=pass_fds)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PackageError("effective runtime contract rejected") from exc


@contextmanager
def _exclusive_runtime_lock(path: Path = Path("/run/lock/jax-platform-runtime-import.lock")):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise PackageError("runtime import already in progress") from exc
        yield
    finally:
        os.close(descriptor)


def _require_units_inactive() -> None:
    for unit in ("jax-platform.service", "jax-platform-migrate.service"):
        try:
            result = subprocess.run(["/usr/bin/systemctl", "show", unit, "--property=ActiveState", "--value"], check=True, text=True, capture_output=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PackageError("cannot confirm runtime units inactive") from exc
        if result.stdout.strip() != "inactive":
            raise PackageError("runtime units must be inactive before import")


def main(argv: list[str]) -> int:
    """Entry point for a separately installed root-owned bootstrap program."""
    if (len(argv) != 12 or argv[1] != "import" or argv[2] != "--package"
            or argv[4] != "--expected-package-digest" or argv[6] != "--backup"
            or argv[8] != "--effective-validator" or argv[10] != "--effective-validator-sha256"):
        return 2
    package, digest, backup = Path(argv[3]), argv[5], Path(argv[7])
    validator, validator_digest = Path(argv[9]), argv[11]
    try:
        validator_fd = _trusted_program(validator, validator_digest)
        if not package.is_absolute() or not backup.is_absolute():
            raise PackageError("invalid runtime transaction path")
        _safe_node(backup, directory=True)
        if any(backup.iterdir()):
            raise PackageError("invalid runtime transaction path")

        def verify(units_dir: Path) -> None:
            # Reload before every D-Bus comparison: a restored unit file is not
            # effective until PID 1 has discarded its cached definition.
            _run_checked(["/usr/bin/systemctl", "daemon-reload"])
            _run_checked(["/usr/bin/systemd-analyze", "verify", "/etc/systemd/system/jax-platform.service", "/etc/systemd/system/jax-platform-migrate.service"])
            # `units_dir` is a root-private copy made from sealed FD bytes, not
            # a pathname reopened from the mutable package or live unit tree.
            _run_checked([f"/proc/self/fd/{validator_fd}", "/", "--units-dir", str(units_dir), "/usr/bin/busctl"], pass_fds=(validator_fd,))

        try:
            with _exclusive_runtime_lock():
                # Never restore or replace a definition while either unit runs.
                _require_units_inactive()
                recover_pending_imports(backup.parent, backup, verify)
                import_package(package, digest, Path("/"), backup, verify, verify_restored=verify)
        finally:
            os.close(validator_fd)
    except PackageError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
