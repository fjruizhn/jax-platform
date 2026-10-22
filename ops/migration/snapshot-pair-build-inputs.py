#!/usr/bin/python3 -I
"""Root-only immutable dependency snapshot for the unprivileged pair builder.

The builder must never consume the caller's wheelhouse or lock directly.  This
tool copies each input into new root-owned inodes, verifies the *destination*
against auditor-supplied digests, seals it, and publishes it once with a name
derived from both digests.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"snapshot-pair-build-inputs: {message}")


def hex_digest(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def wheelhouse_digest(root: Path) -> str:
    entries: list[bytes] = []
    for path in sorted(root.rglob("*"), key=lambda value: os.fsencode(value.relative_to(root).as_posix())):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            fail("unsafe wheelhouse node")
        entries.append(os.fsencode(path.relative_to(root).as_posix()) + b"\0" + digest(path).encode() + b"\n")
    if not entries:
        fail("empty wheelhouse")
    return hashlib.sha256(b"".join(entries)).hexdigest()


def copy_file(source: Path, target: Path) -> None:
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        fail("unsafe input node")
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        while block := incoming.read(1024 * 1024):
            outgoing.write(block)
        outgoing.flush(); os.fsync(outgoing.fileno())
    os.chown(target, 0, 0); os.chmod(target, 0o444)
    after = source.lstat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
        fail("input changed during snapshot")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--wheelhouse-sha256", required=True)
    parser.add_argument("--lock-sha256", required=True)
    parser.add_argument("--root", type=Path, default=Path("/opt/jax-platform-pair/build-inputs"))
    args = parser.parse_args()
    if os.geteuid() != 0 or not hex_digest(args.wheelhouse_sha256) or not hex_digest(args.lock_sha256):
        fail("root and external digests required")
    if args.wheelhouse.is_symlink() or args.lock.is_symlink() or not args.wheelhouse.is_dir() or not args.lock.is_file():
        fail("unsafe input path")
    name = hashlib.sha256((args.wheelhouse_sha256 + args.lock_sha256).encode()).hexdigest()
    args.root.mkdir(mode=0o755, parents=True, exist_ok=True)
    if args.root.lstat().st_uid != 0 or args.root.lstat().st_mode & 0o022:
        fail("unsafe snapshot root")
    final = args.root / name
    if final.exists():
        fail("snapshot already exists")
    incoming = args.root / (".incoming." + os.urandom(16).hex())
    incoming.mkdir(mode=0o700)
    try:
        wheelhouse = incoming / "wheelhouse"; wheelhouse.mkdir(mode=0o755)
        for path in sorted(args.wheelhouse.rglob("*"), key=lambda value: os.fsencode(value.relative_to(args.wheelhouse).as_posix())):
            relative = path.relative_to(args.wheelhouse); target = wheelhouse / relative
            if path.is_dir():
                target.mkdir(mode=0o755)
            else:
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True); copy_file(path, target)
        copy_file(args.lock, incoming / "lock")
        if wheelhouse_digest(wheelhouse) != args.wheelhouse_sha256 or digest(incoming / "lock") != args.lock_sha256:
            fail("external digest mismatch")
        for directory in [incoming, wheelhouse, *[p for p in wheelhouse.rglob("*") if p.is_dir()]]:
            os.chown(directory, 0, 0); os.chmod(directory, 0o555)
        incoming_fd = os.open(incoming, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(incoming_fd)
        finally: os.close(incoming_fd)
        os.rename(incoming, final)
        root_fd = os.open(args.root, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(root_fd)
        finally: os.close(root_fd)
    except BaseException:
        # Quarantine for inspection: never return a failed copy to caller control.
        raise


if __name__ == "__main__":
    main()
