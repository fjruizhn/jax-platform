#!/usr/bin/env python3
"""Fail closed validation for one immutable JAX + jax-platform release pair."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path


class PairReadinessError(Exception):
    pass


SCHEMA = "axioma.jax-platform-pair/v1"
MANIFEST = "pair-manifest.json"


def _reject(message: str) -> PairReadinessError:
    return PairReadinessError(f"pair artifact readiness: {message}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(root: Path, path: Path) -> str:
    value = path.relative_to(root).as_posix()
    if not value or value.startswith("/") or ".." in Path(value).parts:
        raise _reject("unsafe artifact path")
    return value


def _allowed_symlink(root: Path, path: Path) -> bool:
    return path == root / "jax-platform" / "backend" / ".venv" / "lib64" and os.readlink(path) == "lib"


def _check_node(path: Path, *, owner_uid: int, owner_gid: int = 0) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError as exc:
        raise _reject(f"missing {path}") from exc
    if stat.S_ISLNK(value.st_mode) or not (stat.S_ISREG(value.st_mode) or stat.S_ISDIR(value.st_mode)):
        raise _reject(f"unsafe node {path}")
    if value.st_uid != owner_uid or value.st_gid != owner_gid:
        raise _reject(f"unsafe owner {path}")
    if value.st_mode & 0o022:
        raise _reject(f"writable by group or other {path}")
    return value


def _inventory(root: Path, *, owner_uid: int, owner_gid: int = 0) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current = Path(base)
        dirs.sort(); files.sort()
        for name in [*dirs, *files]:
            path = current / name
            if path.is_symlink() and _allowed_symlink(root, path):
                continue
            node = _check_node(path, owner_uid=owner_uid, owner_gid=owner_gid)
            if stat.S_ISDIR(node.st_mode):
                continue
            relative = _safe_relative(root, path)
            if relative == MANIFEST:
                continue
            entries.append({
                "path": relative,
                "size": node.st_size,
                "mode": f"{stat.S_IMODE(node.st_mode):04o}",
                "sha256": _sha256(path),
            })
    return entries


def verify(release: Path, *, expected_jxp: str | None = None, expected_jax: str | None = None,
           owner_uid: int = 0, owner_gid: int = 0, ancestor_stop: Path | None = None) -> dict[str, object]:
    release = release.resolve(strict=True)
    _check_node(release, owner_uid=owner_uid, owner_gid=owner_gid)
    ancestor = release.parent
    while True:
        _check_node(ancestor, owner_uid=owner_uid, owner_gid=owner_gid)
        if ancestor_stop is not None and ancestor == ancestor_stop:
            break
        if ancestor.parent == ancestor:
            break
        ancestor = ancestor.parent
    for required in (release / "jax-platform" / "backend", release / "jax" / "jax", release / MANIFEST):
        _check_node(required, owner_uid=owner_uid, owner_gid=owner_gid)
    try:
        manifest = json.loads((release / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _reject("invalid manifest") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "jax_platform_revision", "jax_revision", "dependency_lock_sha256", "wheelhouse_manifest_sha256", "files"}:
        raise _reject("invalid manifest shape")
    if manifest["schema"] != SCHEMA:
        raise _reject("unsupported manifest schema")
    for key, expected in (("jax_platform_revision", expected_jxp), ("jax_revision", expected_jax)):
        revision = manifest[key]
        if not isinstance(revision, str) or len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
            raise _reject(f"invalid {key}")
        if expected is not None and revision != expected:
            raise _reject(f"unexpected {key}")
    for key in ("dependency_lock_sha256", "wheelhouse_manifest_sha256"):
        value = manifest[key]
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise _reject(f"invalid {key}")
    files = manifest["files"]
    if not isinstance(files, list) or files != _inventory(release, owner_uid=owner_uid, owner_gid=owner_gid):
        raise _reject("artifact inventory mismatch")
    paths = {entry["path"] for entry in files if isinstance(entry, dict)}
    if not any(path.startswith("jax-platform/backend/.venv/") for path in paths):
        raise _reject("missing sealed platform venv")
    if not any(path.startswith("jax/") for path in paths):
        raise _reject("missing sealed jax")
    return manifest


def main(argv: list[str]) -> int:
    if not 2 <= len(argv) <= 4:
        return 2
    release = Path(argv[1])
    expected_jxp = argv[2] if len(argv) >= 3 else None
    expected_jax = argv[3] if len(argv) >= 4 else None
    try:
        verify(release, expected_jxp=expected_jxp, expected_jax=expected_jax)
    except PairReadinessError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
