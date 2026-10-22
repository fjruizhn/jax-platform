#!/usr/bin/python3 -I
"""Root-side adoption of already-built bytes.  This never runs release code."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import stat
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


def _load_contract(root: Path, expected_manifest: str) -> set[tuple[str, str]]:
    """Read data only, and bind every permitted link to an external manifest hash."""
    manifest_path = root / "pair-manifest.json"
    if not manifest_path.is_file() or _sha256(manifest_path) != expected_manifest:
        fail("unexpected manifest digest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        attestation = json.loads((root / "build-attestation.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        fail("invalid build contract")
    if (not isinstance(manifest, dict)
            or set(manifest) != {"schema", "jax_platform_revision", "jax_revision", "dependency_lock_sha256", "wheelhouse_manifest_sha256", "files", "jax_links"}
            or manifest.get("schema") != "axioma.jax-platform-pair/v1"
            or not _hex(manifest.get("jax_platform_revision"), 40)
            or not _hex(manifest.get("jax_revision"), 40)
            or not _hex(manifest.get("dependency_lock_sha256"), 64)
            or not _hex(manifest.get("wheelhouse_manifest_sha256"), 64)
            or not isinstance(manifest.get("files"), list)
            or not isinstance(manifest.get("jax_links"), list)
            or not isinstance(attestation, dict)
            or set(attestation) != {"schema", "platform_commit", "platform_tree", "platform_links", "jax_commit", "jax_tree", "jax_links", "wheelhouse_sha256", "lock_sha256", "python", "python_sha256", "python_version", "freeze_sha256"}
            or attestation.get("schema") != "axioma.pair-build/v2"
            or any(not _hex(attestation.get(key), 40) for key in ("platform_commit", "platform_tree", "jax_commit", "jax_tree"))
            or any(not _hex(attestation.get(key), 64) for key in ("wheelhouse_sha256", "lock_sha256", "python_sha256", "freeze_sha256"))
            or attestation.get("platform_commit") != manifest["jax_platform_revision"]
            or attestation.get("jax_commit") != manifest["jax_revision"]
            or attestation.get("wheelhouse_sha256") != manifest["wheelhouse_manifest_sha256"]
            or attestation.get("lock_sha256") != manifest["dependency_lock_sha256"]
            or manifest["jax_links"] != attestation.get("jax_links")
            or attestation.get("platform_links") != []):
        fail("invalid build contract")
    allowed: set[tuple[str, str]] = set()
    for item in manifest["jax_links"]:
        if (not isinstance(item, dict) or set(item) != {"path", "mode", "blob_oid", "target", "target_path", "target_mode", "target_oid"}
                or not isinstance(item.get("path"), str) or not item["path"].startswith("jax/")
                or not isinstance(item.get("target"), str) or not item["target"] or item["target"].startswith("/")
                or "\x00" in item["target"]):
            fail("invalid jax link attestation")
        allowed.add((item["path"], item["target"]))
    if len(allowed) != len(manifest["jax_links"]):
        fail("duplicate jax link attestation")
    return allowed


def descendants(root: Path, allowed_links: set[tuple[str, str]]):
    """Yield lstat-only descendants, descending through only contract-bound links."""
    pending = [root]
    while pending:
        path = pending.pop()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            relative = path.relative_to(root).as_posix()
            target = os.readlink(path)
            if (relative, target) not in allowed_links and not (relative == "jax-platform/backend/.venv/lib64" and target == "lib"):
                fail("unattested symlink in builder tree")
        yield path, metadata
        if stat.S_ISDIR(metadata.st_mode):
            with os.scandir(path) as directory:
                pending.extend(Path(entry.path) for entry in directory)


def mount_points_below(root: Path) -> set[Path]:
    """Find bind mounts too; st_dev alone cannot identify them."""
    found: set[Path] = set()
    root_text = os.fsencode(str(root.resolve()))
    for raw_line in Path("/proc/self/mountinfo").read_bytes().splitlines():
        fields = raw_line.split(b" - ", 1)[0].split()
        if len(fields) < 5:
            continue
        encoded = fields[4]
        encoded = re.sub(rb"\\([0-7]{3})", lambda match: bytes([int(match.group(1), 8)]), encoded)
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
                    present.add(int(line.split()[1]))
                    break
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return present


def validate_builder_tree(source: Path, builder_uid: int, expected_manifest: str) -> None:
    source = source.resolve(strict=True)
    allowed_links = _load_contract(source, expected_manifest)
    if mount_points_below(source):
        fail("mount in builder tree")
    source_device = source.stat().st_dev
    observed_links: set[tuple[str, str]] = set()
    for path, metadata in descendants(source, allowed_links):
        if metadata.st_dev != source_device:
            fail("cross-device builder tree")
        # Directories legitimately gain one link for every child directory;
        # hardlinks to directories are forbidden by the kernel. Regular objects
        # must have exactly one link.
        if not stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode) and metadata.st_nlink != 1:
            fail("hardlink in builder tree")
        if metadata.st_uid != builder_uid:
            fail("unexpected builder-tree owner")
        if metadata.st_mode & 0o022:
            fail("group or other writable builder tree")
        if stat.S_ISLNK(metadata.st_mode):
            observed_links.add((path.relative_to(source).as_posix(), os.readlink(path)))
        try:
            attributes = os.listxattr(path, follow_symlinks=False)
        except OSError as error:
            fail(f"cannot inspect xattrs: {error}")
        if attributes:
            fail("xattr or ACL in builder tree")
    if not (source / "jax-platform" / "backend" / ".venv" / "bin" / "python").is_file():
        fail("incomplete builder tree")
    if observed_links - {("jax-platform/backend/.venv/lib64", "lib")} != allowed_links:
        fail("jax link inventory mismatch")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    arguments = parser.parse_args()
    if os.geteuid() != 0 or not _hex(arguments.expected_manifest_sha256, 64):
        fail("must run as root")
    try:
        builder = pwd.getpwnam("jaxbuild")
    except KeyError:
        fail("jaxbuild account absent")
    if not builder.pw_shell.endswith("nologin"):
        fail("jaxbuild is not a nologin account")
    if builder.pw_uid in process_uids():
        fail("jaxbuild still has a process")
    if stat.S_ISLNK(arguments.source.lstat().st_mode):
        fail("builder source must not be a symlink")
    source = arguments.source.resolve(strict=True)
    destination = arguments.destination
    parent_metadata = destination.parent.stat()
    if (
        destination.exists()
        or destination.is_symlink()
        or parent_metadata.st_uid != 0
        or parent_metadata.st_mode & 0o022
        or parent_metadata.st_dev != source.stat().st_dev
    ):
        fail("destination must be absent on source filesystem")
    validate_builder_tree(source, builder.pw_uid, arguments.expected_manifest_sha256)
    # The account is nologin and has no processes before this rename.  Moving into a
    # root-controlled parent eliminates directory-entry races before the second scan.
    os.rename(source, destination)
    try:
        validate_builder_tree(destination, builder.pw_uid, arguments.expected_manifest_sha256)
        allowed_links = _load_contract(destination, arguments.expected_manifest_sha256)
        for path, metadata in descendants(destination, allowed_links):
            os.chown(path, 0, 0, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                os.chmod(path, 0o755, follow_symlinks=False)
            elif stat.S_ISLNK(metadata.st_mode):
                continue
            else:
                os.chmod(path, 0o755 if metadata.st_mode & 0o111 else 0o644, follow_symlinks=False)
    except BaseException:
        # The root-owned destination stays quarantined for inspection; never move it
        # back into a builder-writable directory after a failed verification.
        raise


if __name__ == "__main__":
    main()
