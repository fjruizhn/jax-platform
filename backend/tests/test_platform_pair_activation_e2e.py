"""Root-temporary exercise of the paired selection and launcher contract."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "ops" / "migration"
SPEC = importlib.util.spec_from_file_location("pair_readiness_e2e", MIGRATION / "pair_artifact_readiness.py")
assert SPEC and SPEC.loader
readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readiness)


def _sudo_available() -> bool:
    return subprocess.run(["sudo", "-n", "true"], capture_output=True, check=False).returncode == 0


@pytest.mark.skipif(not _sudo_available(), reason="requires passwordless sudo only for a temporary root-owned release")
def test_root_temporary_activation_serializes_selection_and_scrubs_environment(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    root = tmp_path / "pair-root"
    request.addfinalizer(lambda: subprocess.run(["sudo", "-n", "rm", "-rf", "--", str(root)], capture_output=True, check=False))
    release = root / "releases" / ("a" * 40 + "-" + "b" * 40)
    bin_dir = release / "jax-platform" / "backend" / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (release / "jax" / "jax").mkdir(parents=True)
    output = tmp_path / "runtime-path.txt"
    credential = tmp_path / "runtime.env"
    credential.write_text("JAX_DB_HOST=db\nJAX_TRUSTED_PROXIES=127.0.0.1\n", encoding="utf-8")
    fake_python = "#!/bin/sh\nsleep 0.4\nprintf '%s|%s|%s|%s' \"$JAX_REPO_PATH\" \"${BASH_ENV-}\" \"${LD_PRELOAD-}\" \"$PATH\" > " + repr(str(output)) + "\n"
    for name in ("python", "uvicorn"):
        path = bin_dir / name
        path.write_text(fake_python, encoding="utf-8")
        path.chmod(0o755)
    (release / "jax" / "jax" / "memory.py").write_text("x", encoding="utf-8")
    for path in [root, root / "releases", release, *release.rglob("*")]:
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o755 if path.name in {"python", "uvicorn"} else 0o644)
    manifest = {
        "schema": readiness.SCHEMA,
        "jax_platform_revision": "a" * 40,
        "jax_revision": "b" * 40,
        "dependency_lock_sha256": "c" * 64,
        "wheelhouse_manifest_sha256": "d" * 64,
        "files": readiness._inventory(release, owner_uid=os.getuid(), owner_gid=os.getgid()),
    }
    (release / readiness.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    (release / readiness.MANIFEST).chmod(0o644)
    outside = root / "outside"
    shutil.copytree(release, outside)
    (root / "libexec").mkdir()
    shutil.copy2(MIGRATION / "pair_artifact_readiness.py", root / "libexec" / "pair_artifact_readiness.py")
    calls = tmp_path / "systemctl.calls"
    fake_systemctl = tmp_path / "systemctl"
    test_launcher = tmp_path / "launcher"
    test_launcher.write_text(
        "#!/bin/sh\nexec /usr/bin/python3 -I -S " + repr(str(MIGRATION / "jax-platform-pair-exec.py")) + " \"$1\" " + repr(str(credential)) + "\n",
        encoding="utf-8",
    )
    test_launcher.chmod(0o755)
    fake_systemctl.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$JAX_PAIR_TEST_CALLS\"\n"
        "if [ \"$1\" = is-active ]; then exit 1; fi\n"
        "if [ \"$1 $2\" = 'start jax-platform-migrate.service' ]; then exec \"$JAX_PAIR_TEST_LAUNCHER\" migrate; fi\n"
        "[ -n \"${JAX_PAIR_TEST_SLEEP:-}\" ] && sleep \"$JAX_PAIR_TEST_SLEEP\"\n",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)
    subprocess.run(["sudo", "-n", "chown", "-R", "root:root", str(root)], check=True)
    digest = hashlib.sha256((release / readiness.MANIFEST).read_bytes()).hexdigest()
    base_env = {
        "JAX_PAIR_TEST_MODE": "1",
        "JAX_PAIR_TEST_ROOT": str(root),
        "JAX_PAIR_SYSTEMCTL_BIN": str(fake_systemctl),
        "JAX_PAIR_TEST_CALLS": str(calls),
        "JAX_PAIR_TEST_LAUNCHER": str(test_launcher),
    }
    command = ["sudo", "-n", "env", *[f"{key}={value}" for key, value in base_env.items()], "JAX_PAIR_TEST_SLEEP=0.4", str(MIGRATION / "activate-platform-pair-release.sh"), "--root", str(root), "--release", str(release), "--expected-manifest-sha256", digest]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 5
    while not calls.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert calls.exists(), first.communicate(timeout=5)[1]
    second = subprocess.run(command, text=True, capture_output=True, check=False)
    deadline = time.monotonic() + 5
    while "start jax-platform-migrate.service" not in calls.read_text(encoding="utf-8") and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "start jax-platform-migrate.service" in calls.read_text(encoding="utf-8")
    held = subprocess.run(["sudo", "-n", "/usr/bin/flock", "-n", str(root / ".runtime.lock"), "-c", "true"], check=False)
    assert held.returncode != 0
    stdout, stderr = first.communicate(timeout=10)
    assert first.returncode == 0, stderr or stdout
    assert second.returncode != 0
    assert "activation already in progress" in second.stderr
    selection = json.loads((root / "platform-pair-selection.json").read_text(encoding="utf-8"))
    assert selection["release"] == str(release)
    assert selection["manifest_sha256"] == digest
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "stop jax-platform.service", "stop jax-platform-migrate.service",
        "is-active --quiet jax-platform.service", "is-active --quiet jax-platform-migrate.service",
        "start jax-platform-migrate.service", "start jax-platform.service",
    ]
    assert output.read_text(encoding="utf-8") == f"{release / 'jax'}|||/usr/bin:/bin"
    lock_inode = (root / ".runtime.lock").stat().st_ino
    repeat = subprocess.run(command, text=True, capture_output=True, check=False)
    assert repeat.returncode == 0, repeat.stderr
    assert (root / ".runtime.lock").stat().st_ino == lock_inode
    escaped = subprocess.run(["sudo", "-n", "env", *[f"{key}={value}" for key, value in base_env.items()], str(MIGRATION / "activate-platform-pair-release.sh"), "--root", str(root), "--release", str(root / "releases" / ".." / "outside"), "--expected-manifest-sha256", digest], text=True, capture_output=True, check=False)
    assert escaped.returncode != 0
    assert "unsafe release" in escaped.stderr
    bash_env = tmp_path / "bash-env"; bash_env.write_text(f"touch {tmp_path / 'bash-env-ran'}\n", encoding="utf-8")
    launcher = ["sudo", "-n", "env", "JAX_PAIR_TEST_MODE=1", f"JAX_PAIR_TEST_ROOT={root}", "JAX_REPO_PATH=/attacker/checkout", "PATH=/attacker", f"BASH_ENV={bash_env}", "LD_PRELOAD=/attacker.so", "PYTHONPATH=/attacker", "/usr/bin/python3", "-I", "-S", str(MIGRATION / "jax-platform-pair-exec.py"), "web", str(credential)]
    result = subprocess.run(launcher, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == f"{release / 'jax'}|||/usr/bin:/bin"
    assert not (tmp_path / "bash-env-ran").exists()
    hostile = tmp_path / "hostile.env"; hostile.write_text("PATH=/attacker\n", encoding="utf-8")
    rejected = subprocess.run(launcher[:-1] + [str(hostile)], text=True, capture_output=True, check=False)
    assert rejected.returncode != 0
