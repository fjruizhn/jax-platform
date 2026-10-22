#!/usr/bin/python3 -I
"""Root-side adoption of already-built bytes.  This never runs release code."""
from __future__ import annotations

import argparse
import os
import pwd
import stat
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"adopt-pair-root: {message}")


def descendants(root: Path):
    """Yield lstat-only descendants, refusing every link before descent."""
    pending = [root]
    while pending:
        path = pending.pop()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            fail("symlink in builder tree")
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
        for number in range(1, 8):
            encoded = encoded.replace(f"\\{number:03o}".encode(), bytes([number]))
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


def validate_builder_tree(source: Path, builder_uid: int) -> None:
    source = source.resolve(strict=True)
    if mount_points_below(source):
        fail("mount in builder tree")
    source_device = source.stat().st_dev
    for path, metadata in descendants(source):
        if metadata.st_dev != source_device:
            fail("cross-device builder tree")
        # Directories legitimately gain one link for every child directory;
        # hardlinks to directories are forbidden by the kernel. Regular objects
        # must have exactly one link.
        if not stat.S_ISDIR(metadata.st_mode) and metadata.st_nlink != 1:
            fail("hardlink in builder tree")
        if metadata.st_uid != builder_uid:
            fail("unexpected builder-tree owner")
        if metadata.st_mode & 0o022:
            fail("group or other writable builder tree")
        try:
            attributes = os.listxattr(path, follow_symlinks=False)
        except OSError as error:
            fail(f"cannot inspect xattrs: {error}")
        if attributes:
            fail("xattr or ACL in builder tree")
    if not (source / "build-attestation.json").is_file() or not (source / "jax-platform" / "backend" / ".venv" / "bin" / "python").is_file():
        fail("incomplete builder tree")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    arguments = parser.parse_args()
    if os.geteuid() != 0:
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
    validate_builder_tree(source, builder.pw_uid)
    # The account is nologin and has no processes before this rename.  Moving into a
    # root-controlled parent eliminates directory-entry races before the second scan.
    os.rename(source, destination)
    try:
        validate_builder_tree(destination, builder.pw_uid)
        for path, metadata in descendants(destination):
            os.chown(path, 0, 0, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                os.chmod(path, 0o755, follow_symlinks=False)
            else:
                os.chmod(path, 0o755 if metadata.st_mode & 0o111 else 0o644, follow_symlinks=False)
    except BaseException:
        # The root-owned destination stays quarantined for inspection; never move it
        # back into a builder-writable directory after a failed verification.
        raise


if __name__ == "__main__":
    main()
