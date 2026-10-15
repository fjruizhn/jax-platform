#!/usr/bin/env python3
"""Activate a static frontend, or unconditionally restore the original unit."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time


UNIT = "jax-platform-frontend.service"


def checked(*argv, **kwargs):
    return subprocess.run(argv, check=True, **kwargs)


def release_index(release):
    release = release.resolve(strict=True)
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", str(release)):
        raise ValueError("release path contains unsupported nginx characters")
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    if manifest["source_sha"] != release.name:
        raise ValueError("release directory does not match manifest SHA")
    dist = release / "dist"
    actual = {path.relative_to(dist).as_posix() for path in dist.rglob("*") if path.is_file()}
    if actual != set(manifest["files"]):
        raise ValueError("release files differ from manifest")
    for name, expected in manifest["files"].items():
        path = dist / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"release content differs from manifest: {name}")
    return dist / "index.html"


def nginx_config(dist, port, backend):
    if not 1 <= port <= 65535 or not re.fullmatch(r"[A-Za-z0-9.-]+:[0-9]{1,5}", backend):
        raise ValueError("invalid port or backend")
    return f"""worker_processes 2;
pid nginx.pid;
error_log stderr warn;
events {{ worker_connections 1024; }}
http {{
    access_log off;
    client_body_temp_path client_body_temp;
    proxy_temp_path proxy_temp;
    server {{ listen 0.0.0.0:{port} default_server; return 444; }}
    server {{
        listen 0.0.0.0:{port};
        server_name axioma-ia.io www.axioma-ia.io localhost 127.0.0.1;
        client_max_body_size 0;
        root {dist};
        index index.html;
        location = /index.html {{ add_header Cache-Control "no-cache"; }}
        location ~ "^/assets/.+-[A-Za-z0-9_-]{{8,}}\\.[A-Za-z0-9]+$" {{
            try_files $uri =404;
            expires 1y;
            add_header Cache-Control "public, immutable";
        }}
        location /assets/ {{ try_files $uri =404; add_header Cache-Control "no-cache"; }}
        location = /api {{ proxy_pass http://{backend}; proxy_set_header Host {backend}; }}
        location ^~ /api/ {{ proxy_pass http://{backend}; proxy_set_header Host {backend}; }}
        location = /ws {{
            proxy_pass http://{backend};
            proxy_http_version 1.1;
            proxy_set_header Host {backend};
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_read_timeout 3600s;
        }}
        location ^~ /ws/ {{
            proxy_pass http://{backend};
            proxy_http_version 1.1;
            proxy_set_header Host {backend};
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_read_timeout 3600s;
        }}
        location / {{ try_files $uri $uri/ /index.html; }}
    }}
}}
"""


def rollback(dropin_dir, unit=UNIT):
    (dropin_dir / "zz-static.conf").unlink(missing_ok=True)
    (dropin_dir / "static-nginx.nginx").unlink(missing_ok=True)
    checked("systemctl", "daemon-reload")
    # An inactive unit may be unloaded after daemon-reload; reset-failed then exits 1.
    # Restart is still required, and must never be gated on that status.
    subprocess.run(["systemctl", "reset-failed", unit], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    checked("systemctl", "restart", unit)


def apply(release, dropin_dir, port, backend, health_url, unit=UNIT):
    if not re.fullmatch(r"[a-zA-Z0-9_.@-]+\.service", unit):
        raise ValueError("invalid systemd unit")
    runtime = unit.removesuffix(".service")
    index = release_index(release)
    nginx = shutil.which("nginx")
    if not nginx or not Path(nginx).is_absolute():
        raise RuntimeError("distro nginx executable not found in PATH")
    config_path = dropin_dir / "static-nginx.nginx"
    dropin_path = dropin_dir / "zz-static.conf"
    if config_path.exists() or dropin_path.exists():
        raise FileExistsError("static drop-in already exists; rollback first")
    config = nginx_config(index.parent, port, backend)
    with tempfile.TemporaryDirectory(prefix="p100-nginx-test-") as temp:
        trial = Path(temp) / "nginx.conf"
        trial.write_text(config, encoding="utf-8")
        checked(nginx, "-t", "-p", f"{temp}/", "-c", str(trial))

    dropin_dir.mkdir(parents=True, exist_ok=True)
    dropin = f"""[Service]
WorkingDirectory=/
ExecStartPre=
ExecStart=
ExecStart={nginx} -p /run/{runtime}/ -c {config_path} -g "daemon off;"
RuntimeDirectory={runtime}
"""
    installed = False
    try:
        for path, content in ((config_path, config), (dropin_path, dropin)):
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=dropin_dir, delete=False) as staged:
                staged.write(content)
                staged_path = Path(staged.name)
            os.chmod(staged_path, 0o644)
            os.replace(staged_path, path)
        installed = True
        checked("systemctl", "daemon-reload")
        subprocess.run(["systemctl", "reset-failed", unit], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        checked("systemctl", "restart", unit)
        expected = index.read_bytes()
        for _ in range(50):
            response = subprocess.run(
                ["curl", "--fail", "--silent", "--show-error", "--max-time", "1", health_url],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            if response.returncode == 0 and response.stdout == expected:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("curl did not return the selected release index.html")
    except Exception:
        if installed:
            try:
                rollback(dropin_dir, unit)
            except Exception as recovery_error:  # fail-soft: report rollback failure, then re-raise the original apply error
                print(f"automatic rollback also failed: {recovery_error}", file=sys.stderr)
        else:
            config_path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "rollback"))
    parser.add_argument("--unit", default=UNIT)
    parser.add_argument("--drop-in-dir", type=Path,
                        default=None)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--backend", default="localhost:8080")
    parser.add_argument("--health-url", default="http://127.0.0.1:5173/")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_.@-]+\.service", args.unit):
        parser.error("invalid systemd unit")
    if args.drop_in_dir is None:
        args.drop_in_dir = Path("/etc/systemd/system") / f"{args.unit}.d"
    if os.geteuid() != 0:
        parser.error("run as root")
    if args.action == "apply":
        if args.release is None:
            parser.error("--release is required for apply")
        apply(args.release, args.drop_in_dir, args.port, args.backend, args.health_url, args.unit)
    else:
        rollback(args.drop_in_dir, args.unit)
