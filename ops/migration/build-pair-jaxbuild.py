#!/usr/bin/python3
"""Build a paired release from exact Git objects as the unprivileged jaxbuild user.

This is the only build boundary that invokes Git or pip. Root adoption may inspect
the resulting bytes, but never executes them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import subprocess
import sys
from pathlib import Path

GIT = "/usr/bin/git"
SAFE_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0"}
REGULAR_MODES = {b"100644", b"100755"}


def die(message: str) -> None:
    raise SystemExit(f"build-pair-jaxbuild: {message}")


def run(*args: str, cwd: Path) -> bytes:
    return subprocess.check_output((GIT, *args), cwd=cwd, env=SAFE_ENV, stderr=subprocess.STDOUT)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def exact_commit(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def repo_tree(repo: Path, commit: str) -> tuple[str, list[bytes]]:
    """Return only an exact, clean, self-contained Git tree."""
    if os.geteuid() == 0:
        die("must not run as root")
    if not exact_commit(commit):
        die("commit must be a full lowercase SHA-1")
    git_dir = repo / ".git"
    if not repo.is_dir() or not git_dir.is_dir() or (git_dir / "objects" / "info" / "alternates").exists():
        die("unsafe repository")
    if run("replace", "-l", cwd=repo).strip():
        die("replace refs forbidden")
    if run("status", "--porcelain", cwd=repo).strip():
        die("repository not fresh")
    actual = run("rev-parse", f"{commit}^{{commit}}", cwd=repo).decode("ascii").strip()
    if actual != commit:
        die("commit mismatch")
    tree = run("rev-parse", f"{commit}^{{tree}}", cwd=repo).decode("ascii").strip()
    rows = run("ls-tree", "-rz", "-r", "-t", commit, cwd=repo).split(b"\0")[:-1]
    return tree, rows


def extract(repo: Path, commit: str, destination: Path) -> tuple[str, list[dict[str, str]]]:
    """Write only regular Git blobs; never consult a checkout worktree path."""
    tree, rows = repo_tree(repo, commit)
    entries: dict[str, tuple[bytes, bytes, bytes]] = {}
    for row in rows:
        metadata, raw_name = row.split(b"\t", 1)
        mode, object_type, object_id = metadata.split()
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            die("non-UTF-8 tree path")
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or len(object_id) != 40 or (object_type == b"tree" and mode != b"040000") or (object_type == b"blob" and mode not in REGULAR_MODES | {b"120000"}):
            die("unsafe tree entry")
        entries[name] = (mode, object_type, object_id)
    links: list[dict[str, str]] = []
    for name, (mode, object_type, object_id) in entries.items():
        if object_type != b"blob" or mode not in REGULAR_MODES:
            continue
        path = Path(name)
        output = destination / path
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as target:
            target.write(run("cat-file", "blob", object_id.decode("ascii"), cwd=repo))
        output.chmod(0o755 if mode == b"100755" else 0o644)
    for name, (mode, object_type, object_id) in entries.items():
        if mode != b"120000":
            continue
        target = run("cat-file", "blob", object_id.decode("ascii"), cwd=repo).decode("utf-8")
        if not target or target.startswith("/") or "\x00" in target:
            die("unsafe symlink target")
        target_path = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
        target_entry = entries.get(target_path)
        if target_entry is None or target_entry[0] == b"120000" or target_entry[1] not in (b"blob", b"tree"):
            die("unsafe symlink target")
        output = destination / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.symlink_to(target)
        links.append({"path": name, "mode": "120000", "blob_oid": object_id.decode("ascii"), "target": target, "target_path": target_path, "target_mode": target_entry[0].decode("ascii"), "target_oid": target_entry[2].decode("ascii")})
    return tree, links


def wheelhouse_digest(wheelhouse: Path) -> str:
    """Bind relative filenames and bytes for every wheelhouse file."""
    entries: list[bytes] = []
    files = sorted(wheelhouse.rglob("*"), key=lambda item: os.fsencode(str(item.relative_to(wheelhouse))))
    for item in files:
        if item.is_symlink():
            die("wheelhouse contains unsafe entry")
        if item.is_dir():
            continue
        if not item.is_file():
            die("wheelhouse contains unsafe entry")
        relative = os.fsencode(str(item.relative_to(wheelhouse)))
        entries.append(relative + b"\0" + digest(item).encode("ascii") + b"\n")
    if not entries:
        die("empty wheelhouse")
    return hashlib.sha256(b"".join(entries)).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", type=Path, required=True)
    parser.add_argument("--platform-commit", required=True)
    parser.add_argument("--jax", type=Path, required=True)
    parser.add_argument("--jax-commit", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--wheelhouse-sha256", required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--lock-sha256", required=True)
    parser.add_argument("--python", default=sys.executable)
    arguments = parser.parse_args()
    if (arguments.out.exists() or not arguments.wheelhouse.is_dir() or arguments.wheelhouse.is_symlink() or not arguments.lock.is_file() or arguments.lock.is_symlink() or not exact_commit(arguments.platform_commit) or not exact_commit(arguments.jax_commit) or len(arguments.wheelhouse_sha256) != 64 or len(arguments.lock_sha256) != 64 or "--hash=sha256:" not in arguments.lock.read_text(encoding="utf-8")):
        die("invalid inputs")
    actual_wheelhouse = wheelhouse_digest(arguments.wheelhouse)
    if actual_wheelhouse != arguments.wheelhouse_sha256 or digest(arguments.lock) != arguments.lock_sha256:
        die("external dependency digest mismatch")
    arguments.out.mkdir(mode=0o700)
    platform_output = arguments.out / "jax-platform"
    jax_output = arguments.out / "jax"
    platform_output.mkdir()
    jax_output.mkdir()
    platform_tree, platform_links = extract(arguments.platform, arguments.platform_commit, platform_output)
    jax_tree, jax_links = extract(arguments.jax, arguments.jax_commit, jax_output)
    venv = platform_output / "backend" / ".venv"
    subprocess.run([arguments.python, "-m", "venv", "--copies", str(venv)], check=True, env=SAFE_ENV)
    subprocess.run([str(venv / "bin" / "pip"), "install", "--no-index", f"--find-links={arguments.wheelhouse}", "--require-hashes", "-r", str(arguments.lock)], check=True, env=SAFE_ENV)
    with (arguments.out / "freeze.txt").open("xb") as freeze:
        subprocess.run([str(venv / "bin" / "pip"), "freeze", "--all"], check=True, stdout=freeze, env=SAFE_ENV)
    attestation = {"schema": "axioma.pair-build/v2", "platform_commit": arguments.platform_commit, "platform_tree": platform_tree, "platform_links": platform_links, "jax_commit": arguments.jax_commit, "jax_tree": jax_tree, "jax_links": jax_links, "wheelhouse_sha256": actual_wheelhouse, "lock_sha256": digest(arguments.lock), "python": str(Path(arguments.python).resolve()), "python_sha256": digest(Path(arguments.python).resolve()), "python_version": subprocess.check_output([arguments.python, "-c", "import sys; print(sys.implementation.cache_tag)"], env=SAFE_ENV, text=True).strip(), "freeze_sha256": digest(arguments.out / "freeze.txt")}
    (arguments.out / "build-attestation.json").write_text(json.dumps(attestation, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    # The final root adoption re-owns this tree but never changes bytes.  Keep the
    # Git-link attestation beside the sealed inventory so readiness can compare it.
    readiness_spec = __import__("importlib.util").util.spec_from_file_location("pair_readiness", Path(__file__).with_name("pair_artifact_readiness.py"))
    assert readiness_spec and readiness_spec.loader
    readiness = __import__("importlib.util").util.module_from_spec(readiness_spec)
    readiness_spec.loader.exec_module(readiness)
    manifest = {"schema": readiness.SCHEMA, "jax_platform_revision": arguments.platform_commit, "jax_revision": arguments.jax_commit, "dependency_lock_sha256": digest(arguments.lock), "wheelhouse_manifest_sha256": actual_wheelhouse, "jax_links": jax_links, "files": readiness._inventory(arguments.out, owner_uid=os.getuid(), owner_gid=os.getgid())}
    (arguments.out / readiness.MANIFEST).write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
