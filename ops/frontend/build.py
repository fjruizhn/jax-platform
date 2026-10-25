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


def run(*argv, cwd=None, stdout=None, env=None):
    return subprocess.run(argv, cwd=cwd, stdout=stdout, check=True, text=stdout is None, env=env)


HOSTNAME = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))*")


def normalize_office_hosts(values):
    """OP 358 pieza 1: office hosts enter the build only through
    --office-hosts — validated, lowercased, de-duplicated. The frontend
    compares them exactly against window.location.hostname (see
    frontend/src/lib/instalacionOficina.js)."""
    hosts = []
    for value in values:
        for host in value.split(","):
            host = host.strip().lower()
            if not host:
                continue
            if not HOSTNAME.fullmatch(host):
                raise ValueError(f"invalid office host: {host!r}")
            if host not in hosts:
                hosts.append(host)
    return tuple(hosts)


def build_env(office_hosts):
    env = {**os.environ}
    if office_hosts:
        env["VITE_OFFICE_HOSTS"] = ",".join(office_hosts)
    else:
        env.pop("VITE_OFFICE_HOSTS", None)
    return env


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def build(repo, releases, revision, office_hosts=()):
    injected = sorted(name for name in os.environ if name.startswith("VITE_"))
    if injected:
        raise ValueError(f"undeclared Vite build variables: {', '.join(injected)}")
    office_hosts = tuple(office_hosts)
    git = ["git", "-C", str(repo), "-c", f"safe.directory={repo}"]
    sha = subprocess.check_output(
        [*git, "rev-parse", "--verify", f"{revision}^{{commit}}"],
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
            run(*git, "archive", "--format=tar", sha, "frontend", stdout=output)
        run("tar", "-xf", str(archive), "-C", str(workspace))
        frontend = workspace / "frontend"
        lock_digest = digest(frontend / "package-lock.json")
        run("npm", "ci", "--include=dev", cwd=frontend)
        run("npm", "run", "build", cwd=frontend, env=build_env(office_hosts))
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
            # OP 358 pieza 1: dists of both installations share a source SHA;
            # office_hosts is what tells an office dist from an Axioma one.
            "office_hosts": list(office_hosts),
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
    parser.add_argument(
        "--office-hosts",
        action="append",
        default=[],
        metavar="HOST[,HOST...]",
        help="build this dist as the Oficina Virtual installation for these "
             "exact hosts (repeatable, comma-separated); the frontend then "
             "serves its login and office at the root and hides the Axioma "
             "console routes (OP 358 pieza 1)",
    )
    args = parser.parse_args()
    office_hosts = normalize_office_hosts(args.office_hosts)
    print(build(args.repo.resolve(), args.releases.resolve(), args.revision, office_hosts=office_hosts))
