#!/usr/bin/env python3
"""Build a frontend release from a committed tree and record its exact contents."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def run(*argv, cwd=None, stdout=None):
    return subprocess.run(argv, cwd=cwd, stdout=stdout, check=True, text=stdout is None)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def build(repo, releases, revision):
    sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{revision}^{{commit}}"],
        text=True,
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("git did not resolve a full commit SHA")
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / sha
    if target.exists():
        raise FileExistsError(f"release already exists: {target}")

    with tempfile.TemporaryDirectory(prefix="jax-frontend-build-") as temp:
        workspace = Path(temp)
        archive = workspace / "frontend.tar"
        with archive.open("wb") as output:
            run("git", "-C", str(repo), "archive", "--format=tar", sha, "frontend", stdout=output)
        run("tar", "-xf", str(archive), "-C", str(workspace))
        frontend = workspace / "frontend"
        lock_digest = digest(frontend / "package-lock.json")
        run("npm", "ci", cwd=frontend)
        run("npm", "run", "build", cwd=frontend)
        dist = frontend / "dist"
        if not (dist / "index.html").is_file():
            raise RuntimeError("build produced no index.html")
        files = {
            file.relative_to(dist).as_posix(): digest(file)
            for file in sorted(dist.rglob("*"))
            if file.is_file()
        }
        manifest = {
            "source_sha": sha,
            "package_lock_sha256": lock_digest,
            "node": subprocess.check_output(["node", "--version"], text=True).strip(),
            "npm": subprocess.check_output(["npm", "--version"], text=True).strip(),
            "files": files,
        }
        staging = Path(tempfile.mkdtemp(prefix=f".{sha}-", dir=releases))
        try:
            shutil.copytree(dist, staging / "dist")
            (staging / "manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
            os.chmod(staging, 0o755)
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--releases", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    print(build(args.repo.resolve(), args.releases.resolve(), args.revision))
