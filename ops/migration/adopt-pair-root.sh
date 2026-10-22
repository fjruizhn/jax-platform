#!/usr/bin/python3 -I
"""Copy-and-seal a jaxbuild release without executing its bytes as root."""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import pwd
import re
import stat
import uuid
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"adopt-pair-root: {message}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(char in "0123456789abcdef" for char in value)


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        fail("invalid artifact path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        fail("invalid artifact path")
    return value


class Contract:
    def __init__(self, files: dict[str, tuple[int, str, int]], links: dict[str, str], manifest_bytes: bytes, manifest_digest: str) -> None:
        self.files = files
        self.links = links
        self.manifest_bytes = manifest_bytes
        self.manifest_digest = manifest_digest


def _read_regular_once(path: Path) -> tuple[bytes, os.stat_result]:
    """Return exactly the bytes from one non-symlink inode, never a pathname reread."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        fail(f"cannot open manifest: {error}")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            fail("invalid manifest metadata")
        chunks: list[bytes] = []
        while block := os.read(descriptor, 1024 * 1024):
            chunks.append(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    value = b"".join(chunks)
    if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size) or len(value) != before.st_size:
        fail("manifest changed during read")
    return value, before


def _load_contract(root: Path, expected_manifest: str) -> Contract:
    manifest_path = root / "pair-manifest.json"
    manifest_bytes, manifest_stat = _read_regular_once(manifest_path)
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest:
        fail("unexpected manifest digest")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        attestation = json.loads((root / "build-attestation.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        fail("invalid build contract")
    required = {"schema", "jax_platform_revision", "jax_revision", "dependency_lock_sha256", "wheelhouse_manifest_sha256", "files", "jax_links"}
    attestation_required = {"schema", "platform_commit", "platform_tree", "platform_links", "jax_commit", "jax_tree", "jax_links", "wheelhouse_sha256", "lock_sha256", "python", "python_sha256", "python_version", "freeze_sha256"}
    if (not isinstance(manifest, dict) or set(manifest) != required or manifest.get("schema") != "axioma.jax-platform-pair/v1"
            or not _hex(manifest.get("jax_platform_revision"), 40) or not _hex(manifest.get("jax_revision"), 40)
            or not _hex(manifest.get("dependency_lock_sha256"), 64) or not _hex(manifest.get("wheelhouse_manifest_sha256"), 64)
            or not isinstance(manifest.get("files"), list) or not isinstance(manifest.get("jax_links"), list)
            or not isinstance(attestation, dict) or set(attestation) != attestation_required or attestation.get("schema") != "axioma.pair-build/v2"
            or any(not _hex(attestation.get(key), 40) for key in ("platform_commit", "platform_tree", "jax_commit", "jax_tree"))
            or any(not _hex(attestation.get(key), 64) for key in ("wheelhouse_sha256", "lock_sha256", "python_sha256", "freeze_sha256"))
            or attestation.get("platform_commit") != manifest["jax_platform_revision"] or attestation.get("jax_commit") != manifest["jax_revision"]
            or attestation.get("wheelhouse_sha256") != manifest["wheelhouse_manifest_sha256"] or attestation.get("lock_sha256") != manifest["dependency_lock_sha256"]
            or attestation.get("platform_links") != [] or manifest["jax_links"] != attestation.get("jax_links")):
        fail("invalid build contract")
    files: dict[str, tuple[int, str, int]] = {}
    links: dict[str, str] = {}
    for item in manifest["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            fail("invalid artifact inventory")
        name = _relative(item["path"])
        if name in files or name in links:
            fail("duplicate artifact inventory")
        if item.get("mode") == "120000":
            if set(item) != {"path", "mode", "target", "target_path", "target_mode"} or not name.startswith("jax/"):
                fail("invalid artifact inventory")
            target = item.get("target")
            if not isinstance(target, str) or not target or target.startswith("/") or "\x00" in target:
                fail("invalid artifact inventory")
            links[name] = target
            continue
        if set(item) != {"path", "size", "mode", "sha256"} or not isinstance(item.get("size"), int) or item["size"] < 0:
            fail("invalid artifact inventory")
        mode = item.get("mode")
        if mode not in {"0644", "0755"} or not _hex(item.get("sha256"), 64):
            fail("invalid artifact inventory")
        files[name] = (item["size"], item["sha256"], int(mode, 8))
    sealed_links: dict[str, str] = {}
    for item in manifest["jax_links"]:
        if (not isinstance(item, dict) or set(item) != {"path", "mode", "blob_oid", "target", "target_path", "target_mode", "target_oid"}
                or not isinstance(item.get("path"), str) or not item["path"].startswith("jax/")
                or item.get("mode") != "120000" or not isinstance(item.get("target"), str) or not item["target"] or item["target"].startswith("/")
                or "\x00" in item["target"] or not isinstance(item.get("target_path"), str) or not item["target_path"].startswith("jax/")
                or item.get("target_mode") not in {"100644", "100755", "040000"}
                or not _hex(item.get("blob_oid"), 40) or not _hex(item.get("target_oid"), 40)):
            fail("invalid jax link attestation")
        name = _relative(item["path"])
        if name in sealed_links or links.get(name) != item["target"]:
            fail("jax link inventory mismatch")
        sealed_links[name] = item["target"]
    directory_links = [item for item in manifest["jax_links"] if item["target_mode"] == "040000"]
    directory_graph = {item["path"]: [other["path"] for other in directory_links
                                        if other["path"].startswith(f"{item['target_path']}/")]
                       for item in directory_links}
    visiting: set[str] = set(); visited: set[str] = set()
    def visit_directory_link(name: str) -> None:
        if name in visiting:
            fail("directory symlink cycle")
        if name in visited:
            return
        visiting.add(name)
        for child in directory_graph[name]:
            visit_directory_link(child)
        visiting.remove(name); visited.add(name)
    for name in directory_graph:
        visit_directory_link(name)
    if links != sealed_links or "pair-manifest.json" in files or "pair-manifest.json" in links:
        fail("jax link inventory mismatch")
    if stat.S_IMODE(manifest_stat.st_mode) != 0o644:
        fail("invalid manifest metadata")
    return Contract(files, links, manifest_bytes, expected_manifest)


def descendants(root: Path, allowed_links: dict[str, str]):
    pending = [root]
    while pending:
        path = pending.pop()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            relative = path.relative_to(root).as_posix()
            if allowed_links.get(relative) != os.readlink(path) and not (relative == "jax-platform/backend/.venv/lib64" and os.readlink(path) == "lib"):
                fail("unattested symlink in builder tree")
        yield path, metadata
        if stat.S_ISDIR(metadata.st_mode):
            with os.scandir(path) as directory:
                pending.extend(Path(entry.path) for entry in directory)


def mount_points_below(root: Path) -> set[Path]:
    found: set[Path] = set(); root_text = os.fsencode(str(root.resolve()))
    for raw_line in Path("/proc/self/mountinfo").read_bytes().splitlines():
        fields = raw_line.split(b" - ", 1)[0].split()
        if len(fields) < 5:
            continue
        encoded = re.sub(rb"\\([0-7]{3})", lambda match: bytes([int(match.group(1), 8)]), fields[4])
        if encoded == root_text or encoded.startswith(root_text + b"/"):
            found.add(Path(os.fsdecode(encoded)))
    return found


def process_uids() -> set[int]:
    present: set[int] = set()
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            for line in (process / "status").read_text(encoding="ascii").splitlines():
                if line.startswith("Uid:"):
                    present.add(int(line.split()[1])); break
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return present


def _tree_entries(root: Path, allowed_links: dict[str, str], *, owner_uid: int, owner_gid: int) -> tuple[dict[str, os.stat_result], dict[str, str]]:
    if mount_points_below(root):
        fail("mount in builder tree")
    device = root.stat().st_dev; files: dict[str, os.stat_result] = {}; links: dict[str, str] = {}
    for path, metadata in descendants(root, allowed_links):
        if metadata.st_dev != device:
            fail("cross-device builder tree")
        if metadata.st_uid != owner_uid or metadata.st_gid != owner_gid or (not stat.S_ISLNK(metadata.st_mode) and metadata.st_mode & 0o022):
            fail("unsafe builder-tree metadata")
        if not stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode) and metadata.st_nlink != 1:
            fail("hardlink in builder tree")
        try:
            if os.listxattr(path, follow_symlinks=False):
                fail("xattr or ACL in builder tree")
        except OSError as error:
            fail(f"cannot inspect xattrs: {error}")
        relative = path.relative_to(root).as_posix()
        if stat.S_ISREG(metadata.st_mode):
            if relative != "pair-manifest.json": files[relative] = metadata
        elif stat.S_ISLNK(metadata.st_mode):
            if relative != "jax-platform/backend/.venv/lib64": links[relative] = os.readlink(path)
        elif not stat.S_ISDIR(metadata.st_mode):
            fail("unsafe node in builder tree")
    return files, links


def validate_builder_tree(source: Path, builder_uid: int, expected_manifest: str, builder_gid: int | None = None) -> Contract:
    source = source.resolve(strict=True); contract = _load_contract(source, expected_manifest)
    files, links = _tree_entries(source, contract.links, owner_uid=builder_uid, owner_gid=os.getgid() if builder_gid is None else builder_gid)
    if set(files) != set(contract.files) or links != contract.links:
        fail("artifact inventory mismatch")
    if not (source / "jax-platform" / "backend" / ".venv" / "bin" / "python").is_file():
        fail("incomplete builder tree")
    for name, (size, digest, mode) in contract.files.items():
        metadata = files[name]
        if metadata.st_size != size or stat.S_IMODE(metadata.st_mode) != mode or _sha256(source / name) != digest:
            fail("artifact inventory mismatch")
    return contract


def _open_parent_at(root_fd: int, name: str, *, create: bool = False) -> tuple[int, str]:
    """Resolve every component beneath an already-open root without following links."""
    parts = Path(name).parts
    if not parts:
        fail("invalid artifact path")
    current = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            if create:
                try: os.mkdir(part, 0o755, dir_fd=current)
                except FileExistsError: pass
            try:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
            except OSError as error:
                fail(f"unsafe path component: {error}")
            os.close(current); current = following
        return current, parts[-1]
    except BaseException:
        os.close(current); raise


def _copy_regular(source_root_fd: int, destination_root_fd: int, name: str, expected: tuple[int, str, int], *, owner_uid: int, owner_gid: int) -> tuple[int, int]:
    size, expected_digest, mode = expected
    source_parent, leaf = _open_parent_at(source_root_fd, name)
    destination_parent, destination_leaf = _open_parent_at(destination_root_fd, name, create=True)
    try:
        source_fd = os.open(leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=source_parent)
    finally:
        os.close(source_parent)
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1: fail("source changed during adoption")
        destination_fd = os.open(destination_leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=destination_parent)
        try:
            digest = hashlib.sha256(); copied = 0
            while block := os.read(source_fd, 1024 * 1024):
                digest.update(block); copied += len(block); view = memoryview(block)
                while view: view = view[os.write(destination_fd, view):]
            os.fsync(destination_fd); os.fchown(destination_fd, owner_uid, owner_gid); os.fchmod(destination_fd, mode)
        finally: os.close(destination_fd)
    finally: os.close(source_fd)
    try:
        copied_fd = os.open(destination_leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=destination_parent)
        try:
            copied_stat = os.fstat(copied_fd); copied_hash = hashlib.sha256()
            while block := os.read(copied_fd, 1024 * 1024): copied_hash.update(block)
        finally: os.close(copied_fd)
    finally: os.close(destination_parent)
    if copied != size or digest.hexdigest() != expected_digest or copied_hash.hexdigest() != expected_digest or copied_stat.st_size != size:
        fail("source changed during adoption")
    # Root never changes source ownership or modes; the fresh destination FD is
    # re-owned before it is closed, with no second pathname traversal.
    after = copied_stat
    if (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino): fail("source inode reused during adoption")
    return before.st_dev, before.st_ino


def _copy_literal(destination_root_fd: int, name: str, value: bytes, *, owner_uid: int, owner_gid: int) -> None:
    """Write contract bytes once; unlike source files this has no second pathname read."""
    parent, leaf = _open_parent_at(destination_root_fd, name, create=True)
    try:
        descriptor = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644, dir_fd=parent)
        try:
            view = memoryview(value)
            while view:
                view = view[os.write(descriptor, view):]
            os.fsync(descriptor); os.fchown(descriptor, owner_uid, owner_gid); os.fchmod(descriptor, 0o644)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def _fsync_tree(root: Path, allowed_links: dict[str, str]) -> None:
    for path, metadata in descendants(root, allowed_links):
        if stat.S_ISREG(metadata.st_mode):
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try: os.fsync(fd)
            finally: os.close(fd)
    for path, metadata in descendants(root, allowed_links):
        if stat.S_ISDIR(metadata.st_mode):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)


def _rename_noreplace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True); renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None: fail("renameat2 unavailable")
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint); renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST: fail("destination already exists")
        fail(f"renameat2 failed: {os.strerror(error)}")


def copy_and_seal(source: Path, destination: Path, contract: Contract, *, owner_uid: int = 0, owner_gid: int = 0, builder_uid: int | None = None) -> None:
    """Create fresh root-owned inodes, verify, fsync, then publish once."""
    parent = destination.parent; stage = parent / f".{destination.name}.adopting-{uuid.uuid4().hex}"
    os.mkdir(stage, 0o700)
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        stage_fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            _copy_literal(stage_fd, "pair-manifest.json", contract.manifest_bytes, owner_uid=owner_uid, owner_gid=owner_gid)
            for name in sorted(contract.files, key=os.fsencode):
                _copy_regular(source_fd, stage_fd, name, contract.files[name], owner_uid=owner_uid, owner_gid=owner_gid)
        finally:
            os.close(stage_fd); os.close(source_fd)
        for name, target_text in sorted(contract.links.items(), key=lambda item: os.fsencode(item[0])):
            target = stage / name; target.parent.mkdir(parents=True, exist_ok=True); target.symlink_to(target_text); os.lchown(target, owner_uid, owner_gid)
        # This is CPython's documented relative compatibility link, not a source
        # object.  Recreate the literal link instead of dereferencing its mutable
        # builder pathname.
        special = stage / "jax-platform/backend/.venv/lib64"
        special.parent.mkdir(parents=True, exist_ok=True); special.symlink_to("lib"); os.lchown(special, owner_uid, owner_gid)
        for path, metadata in descendants(stage, contract.links):
            if stat.S_ISDIR(metadata.st_mode): os.chown(path, owner_uid, owner_gid, follow_symlinks=False); os.chmod(path, 0o755, follow_symlinks=False)
        copied_files, copied_links = _tree_entries(stage, contract.links, owner_uid=owner_uid, owner_gid=owner_gid)
        if set(copied_files) != set(contract.files) or copied_links != contract.links: fail("sealed artifact inventory mismatch")
        sealed_manifest, _ = _read_regular_once(stage / "pair-manifest.json")
        if sealed_manifest != contract.manifest_bytes or hashlib.sha256(sealed_manifest).hexdigest() != contract.manifest_digest: fail("sealed manifest mismatch")
        for name, expected in contract.files.items():
            metadata = copied_files[name]
            if metadata.st_size != expected[0] or stat.S_IMODE(metadata.st_mode) != expected[2] or _sha256(stage / name) != expected[1]: fail("sealed artifact inventory mismatch")
        if builder_uid is not None and builder_uid in process_uids():
            fail("jaxbuild gained a process during adoption")
        _fsync_tree(stage, contract.links); _rename_noreplace(stage, destination)
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(parent_fd)
        finally: os.close(parent_fd)
    except BaseException:
        # A failed stage remains quarantined under the root-owned parent, never at the release name.
        raise


def validate_supervisor_binding(source: Path, request: Path, receipt: Path) -> None:
    """Adoption consumes the root supervisor receipt, never a builder digest."""
    request_raw, request_stat = _read_regular_once(request)
    receipt_raw, receipt_stat = _read_regular_once(receipt)
    if any(x.st_uid != 0 or x.st_gid != 0 or stat.S_IMODE(x.st_mode) != 0o600 for x in (request_stat, receipt_stat)):
        fail("unsafe supervisor evidence")
    try:
        wanted, got = json.loads(request_raw), json.loads(receipt_raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("invalid supervisor evidence")
    if (wanted.get("schema") != "axioma.pair-build-request/v1" or got.get("schema") != "axioma.pair-build-receipt/v1"
            or got.get("nonce") != wanted.get("nonce") or got.get("platform_commit") != wanted.get("platform_commit")
            or got.get("jax_commit") != wanted.get("jax_commit")):
        fail("supervisor receipt mismatch")
    value = source.stat()
    if got.get("output_dev") != value.st_dev or got.get("output_ino") != value.st_ino:
        fail("supervisor output identity mismatch")
    manifest, _ = _read_regular_once(source / "pair-manifest.json")
    if hashlib.sha256(manifest).hexdigest() != got.get("manifest_sha256"):
        fail("supervisor manifest mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--source", type=Path, required=True); parser.add_argument("--destination", type=Path, required=True); parser.add_argument("--expected-manifest-sha256", required=True); parser.add_argument("--request", type=Path, required=True); parser.add_argument("--receipt", type=Path, required=True)
    arguments = parser.parse_args()
    if os.geteuid() != 0 or not _hex(arguments.expected_manifest_sha256, 64): fail("must run as root")
    try: builder = pwd.getpwnam("jaxbuild")
    except KeyError: fail("jaxbuild account absent")
    if not builder.pw_shell.endswith("nologin") or builder.pw_uid in process_uids(): fail("jaxbuild is not a stopped nologin account")
    if stat.S_ISLNK(arguments.source.lstat().st_mode): fail("builder source must not be a symlink")
    source = arguments.source.resolve(strict=True); destination = arguments.destination; parent_metadata = destination.parent.stat()
    if destination.exists() or destination.is_symlink() or parent_metadata.st_uid != 0 or parent_metadata.st_mode & 0o022 or parent_metadata.st_dev != source.stat().st_dev: fail("destination must be absent on source filesystem")
    validate_supervisor_binding(source, arguments.request, arguments.receipt)
    contract = validate_builder_tree(source, builder.pw_uid, arguments.expected_manifest_sha256, builder.pw_gid)
    copy_and_seal(source, destination, contract, builder_uid=builder.pw_uid)


if __name__ == "__main__": main()
