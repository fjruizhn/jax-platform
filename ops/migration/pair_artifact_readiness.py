#!/usr/bin/env python3
"""Fail closed validation for one immutable JAX + jax-platform release pair."""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
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
            if path.is_symlink():
                node = path.lstat()
                relative = _safe_relative(root, path)
                target = os.readlink(path)
                target_relative = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
                if (not relative.startswith("jax/") or not target or target.startswith("/") or "\x00" in target
                        or target_relative == "." or target_relative.startswith("../") or not target_relative.startswith("jax/")):
                    raise _reject(f"unsafe node {path}")
                target_node = root / target_relative
                target_stat = _check_node(target_node, owner_uid=owner_uid, owner_gid=owner_gid)
                if stat.S_ISLNK(target_stat.st_mode):
                    raise _reject(f"unsafe node {path}")
                entries.append({"path": relative, "mode": "120000", "target": target,
                                "target_path": target_relative,
                                "target_mode": f"{stat.S_IMODE(target_stat.st_mode):04o}"})
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
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "jax_platform_revision", "jax_revision", "dependency_lock_sha256", "wheelhouse_manifest_sha256", "files", "jax_links"}:
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
    try:
        attestation = json.loads((release / "build-attestation.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _reject("invalid build attestation") from exc
    required_attestation = {"schema", "platform_commit", "platform_tree", "platform_links", "jax_commit", "jax_tree", "jax_links", "wheelhouse_sha256", "lock_sha256", "python", "python_sha256", "python_version", "freeze_sha256"}
    if (not isinstance(manifest["jax_links"], list) or not isinstance(attestation, dict)
            or set(attestation) != required_attestation
            or attestation.get("schema") != "axioma.pair-build/v2"
            or attestation.get("platform_commit") != manifest["jax_platform_revision"]
            or attestation.get("jax_commit") != manifest["jax_revision"]
            or attestation.get("wheelhouse_sha256") != manifest["wheelhouse_manifest_sha256"]
            or attestation.get("lock_sha256") != manifest["dependency_lock_sha256"]
            or attestation.get("platform_links") != []
            or manifest["jax_links"] != attestation.get("jax_links")):
        raise _reject("jax link attestation mismatch")
    if any(not _valid_link_entry(link) for link in manifest["jax_links"]):
        raise _reject("invalid jax link attestation")
    _reject_directory_link_cycles(manifest["jax_links"])
    if not isinstance(files, list) or files != _inventory(release, owner_uid=owner_uid, owner_gid=owner_gid):
        raise _reject("artifact inventory mismatch")
    actual_links = {(item["path"], item["target"], item["target_path"])
                    for item in files if isinstance(item, dict) and item.get("mode") == "120000"}
    sealed_links = {(item["path"], item["target"], item["target_path"]) for item in manifest["jax_links"]}
    if actual_links != sealed_links:
        raise _reject("jax link inventory mismatch")
    paths = {entry["path"] for entry in files if isinstance(entry, dict)}
    if not any(path.startswith("jax-platform/backend/.venv/") for path in paths):
        raise _reject("missing sealed platform venv")
    if not any(path.startswith("jax/") for path in paths):
        raise _reject("missing sealed jax")
    return manifest


def _valid_link_entry(item: object) -> bool:
    if not isinstance(item, dict) or set(item) != {"path", "mode", "blob_oid", "target", "target_path", "target_mode", "target_oid"}:
        return False
    return (isinstance(item["path"], str) and item["path"].startswith("jax/")
            and item["mode"] == "120000" and isinstance(item["target"], str) and item["target"]
            and isinstance(item["target_path"], str) and item["target_path"].startswith("jax/")
            and item["target_mode"] in {"100644", "100755", "040000"}
            and all(isinstance(item[key], str) and len(item[key]) == 40 and all(char in "0123456789abcdef" for char in item[key]) for key in ("blob_oid", "target_oid")))


def _reject_directory_link_cycles(links: list[object]) -> None:
    directories = [item for item in links if isinstance(item, dict) and item["target_mode"] == "040000"]
    graph: dict[str, list[str]] = {}
    for item in directories:
        target = item["target_path"]
        graph[item["path"]] = [other["path"] for other in directories if other["path"].startswith(f"{target}/")]
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise _reject("directory symlink cycle")
        if name in visited:
            return
        visiting.add(name)
        for child in graph[name]:
            visit(child)
        visiting.remove(name)
        visited.add(name)

    for name in graph:
        visit(name)


def main(argv: list[str]) -> int:
    if not 2 <= len(argv) <= 4:
        return 2
    release = Path(argv[1])
    expected_jxp = argv[2] if len(argv) >= 3 else None
    expected_jax = argv[3] if len(argv) >= 4 else None
    ancestor_stop = None
    # The test harness uses a root-owned temporary tree under /tmp.  Production
    # never accepts this escape hatch: it requires effective root and an exact
    # temporary root path, while the deployed launcher runs unprivileged.
    test_root = os.environ.get("JAX_PAIR_TEST_ROOT")
    if os.environ.get("JAX_PAIR_TEST_MODE") == "1" and os.geteuid() == 0 and test_root and test_root.startswith("/tmp/"):
        ancestor_stop = Path(test_root)
    try:
        verify(release, expected_jxp=expected_jxp, expected_jax=expected_jax, ancestor_stop=ancestor_stop)
    except PairReadinessError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
