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


def test_legacy_pair_installer_is_disabled_before_it_can_create_a_release(tmp_path: Path) -> None:
    root = tmp_path / "pair-root"
    result = subprocess.run(
        [str(MIGRATION / "install-platform-pair-release.sh"), "--root", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert "is disabled" in result.stderr
    assert not root.exists()


@pytest.mark.skipif(not _sudo_available(), reason="requires passwordless sudo only for a temporary root-owned release")
def test_root_temporary_activation_serializes_selection_and_scrubs_environment(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    root = tmp_path / "pair-root"
    request.addfinalizer(lambda: subprocess.run(["sudo", "-n", "rm", "-rf", "--", str(root)], capture_output=True, check=False))
    release = root / "releases" / ("a" * 40 + "-" + "b" * 40)
    bin_dir = release / "jax-platform" / "backend" / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (release / "jax" / "jax").mkdir(parents=True)
    output = tmp_path / "runtime-path.txt"
    migration_started = tmp_path / "migration-started"
    credential = tmp_path / "runtime.env"
    credential.write_text("JAX_DB_HOST=db\nJAX_TRUSTED_PROXIES=127.0.0.1\n", encoding="utf-8")
    fake_python = "#!/bin/sh\nfor arg in \"$@\"; do [ \"$arg\" = uvicorn ] && { printf '%s|||%s' \"$JAX_REPO_PATH\" \"$PATH\" > " + repr(str(output)) + "; exit 0; }; done\nexec /usr/bin/python3 \"$@\"\n"
    path = bin_dir / 'python'; path.write_text(fake_python, encoding="utf-8"); path.chmod(0o755)
    db = release / 'jax-platform' / 'backend' / 'db'; db.mkdir()
    (db / '__init__.py').write_text('')
    (db / 'migrate.py').write_text("import os,time\nopen(" + repr(str(migration_started)) + ", 'w').write('started')\ntime.sleep(.4)\nopen(" + repr(str(output)) + ", 'w').write(os.environ['JAX_REPO_PATH']+'|||'+os.environ['PATH'])\n")
    (release / "jax" / "jax" / "memory.py").write_text("x", encoding="utf-8")
    for path in [root, root / "releases", release, *release.rglob("*")]:
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o755 if path.name == "python" else 0o644)
    attestation = {"schema": "axioma.pair-build/v2", "platform_commit": "a" * 40,
                   "platform_tree": "1" * 40, "platform_links": [], "jax_commit": "b" * 40,
                   "jax_tree": "2" * 40, "jax_links": [], "wheelhouse_sha256": "d" * 64,
                   "lock_sha256": "c" * 64, "python": "/usr/bin/python3", "python_sha256": "3" * 64,
                   "python_version": "cpython-test", "freeze_sha256": "4" * 64}
    (release / "build-attestation.json").write_text(json.dumps(attestation), encoding="utf-8")
    (release / "build-attestation.json").chmod(0o644)
    (release / "release-lineage.json").write_text(json.dumps({"schema": "axioma.pair-lineage/v1", "platform_predecessor": "c" * 40, "jax_predecessor": "d" * 40}), encoding="utf-8")
    (release / "release-lineage.json").chmod(0o644)
    manifest = {
        "schema": readiness.SCHEMA,
        "jax_platform_revision": "a" * 40,
        "jax_revision": "b" * 40,
        "dependency_lock_sha256": "c" * 64,
        "wheelhouse_manifest_sha256": "d" * 64,
        "jax_links": [],
        "files": readiness._inventory(release, owner_uid=os.getuid(), owner_gid=os.getgid()),
    }
    (release / readiness.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    (release / readiness.MANIFEST).chmod(0o644)
    outside = root / "outside"
    shutil.copytree(release, outside)
    (root / "libexec").mkdir()
    shutil.copy2(MIGRATION / "pair_artifact_readiness.py", root / "libexec" / "pair_artifact_readiness.py")
    calls = tmp_path / "systemctl.calls"
    service_pid = tmp_path / "service.pid"
    migration_state = tmp_path / "migration.state"
    fake_systemctl = tmp_path / "systemctl"
    test_launcher = tmp_path / "launcher"
    test_launcher.write_text(
        "#!/bin/sh\nexec /usr/bin/python3 -I -S " + repr(str(MIGRATION / "jax-platform-pair-exec.py")) + " \"$1\" " + repr(str(credential)) + "\n",
        encoding="utf-8",
    )
    test_launcher.chmod(0o755)
    provision_calls = tmp_path / "provision.calls"
    provisioner = tmp_path / "provisioner"
    provisioner.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$JAX_PAIR_TEST_PROVISION_CALLS\"\n",
        encoding="utf-8",
    )
    provisioner.chmod(0o755)
    fake_systemctl.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$JAX_PAIR_TEST_CALLS\"\n"
        "if [ \"$1 $2\" = 'stop jax-platform.service' ] && [ -f \"$JAX_PAIR_TEST_SERVICE_PID\" ]; then kill \"$(cat \"$JAX_PAIR_TEST_SERVICE_PID\")\" 2>/dev/null || :; rm -f \"$JAX_PAIR_TEST_SERVICE_PID\"; fi\n"
        "if [ \"$1 $2\" = 'stop jax-platform-migrate.service' ]; then rm -f \"$JAX_PAIR_TEST_MIGRATION_STATE\"; fi\n"
        "if [ \"$1\" = show ] && [ \"$2\" = --value ]; then p=$3; pid=0; active=inactive; sub=dead; result=success; if [ \"$4\" = jax-platform.service ] && [ -f \"$JAX_PAIR_TEST_SERVICE_PID\" ] && kill -0 \"$(cat \"$JAX_PAIR_TEST_SERVICE_PID\")\" 2>/dev/null; then pid=$(cat \"$JAX_PAIR_TEST_SERVICE_PID\"); active=active; sub=running; fi; if [ \"$4\" = jax-platform-migrate.service ] && [ -f \"$JAX_PAIR_TEST_MIGRATION_STATE\" ]; then active=active; sub=exited; fi; case $p in --property=ActiveState) echo $active;; --property=SubState) echo $sub;; --property=MainPID) echo $pid;; --property=Result) echo $result;; esac; exit 0; fi\n"
        "if [ \"$1 $2\" = 'start jax-platform-migrate.service' ]; then \"$JAX_PAIR_TEST_LAUNCHER\" migrate; : > \"$JAX_PAIR_TEST_MIGRATION_STATE\"; fi\n"
        "if [ \"$1 $2\" = 'start jax-platform.service' ]; then \"$JAX_PAIR_TEST_LAUNCHER\" web; env JAX_REPO_PATH=\"$JAX_PAIR_TEST_RELEASE/jax\" sleep 5 & echo $! > \"$JAX_PAIR_TEST_SERVICE_PID\"; fi\n"
        "[ -n \"${JAX_PAIR_TEST_SLEEP:-}\" ] && sleep \"$JAX_PAIR_TEST_SLEEP\"\n",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)
    health = tmp_path / "health"
    health_calls = tmp_path / "health.calls"
    # Type=simple reports active before uvicorn binds.  The first two probes
    # deliberately fail so activation must poll rather than leaving DDL_STARTED.
    health.write_text(
        "#!/bin/sh\n"
        "count=0; [ -f \"$JAX_PAIR_TEST_HEALTH_CALLS\" ] && count=$(cat \"$JAX_PAIR_TEST_HEALTH_CALLS\")\n"
        "count=$((count + 1)); printf '%s' \"$count\" > \"$JAX_PAIR_TEST_HEALTH_CALLS\"\n"
        "if [ \"${JAX_PAIR_TEST_SWITCH_PID:-}\" = 1 ]; then env JAX_REPO_PATH=other sleep 5 & echo $! > \"$JAX_PAIR_TEST_SERVICE_PID\"; fi\n"
        "[ \"$count\" -ge 3 ]\n",
        encoding="utf-8",
    )
    health.chmod(0o755)
    digest = hashlib.sha256((release / readiness.MANIFEST).read_bytes()).hexdigest()
    predecessor = root / "releases" / ("c" * 40 + "-" + "d" * 40)
    shutil.copytree(release, predecessor)
    predecessor_manifest = json.loads((predecessor / readiness.MANIFEST).read_text(encoding="utf-8"))
    predecessor_manifest["jax_platform_revision"] = "c" * 40
    predecessor_manifest["jax_revision"] = "d" * 40
    (predecessor / readiness.MANIFEST).write_text(json.dumps(predecessor_manifest), encoding="utf-8")
    (root / "current").symlink_to("releases/" + predecessor.name)
    previous_digest = hashlib.sha256((predecessor / readiness.MANIFEST).read_bytes()).hexdigest()
    (root / "platform-pair-selection.json").write_text(json.dumps({"release": str(predecessor), "jax_platform_revision": "c" * 40, "jax_revision": "d" * 40, "manifest_sha256": previous_digest}), encoding="utf-8")
    next_release = root / "releases" / ("e" * 40 + "-" + "f" * 40)
    shutil.copytree(release, next_release)
    next_manifest = json.loads((next_release / readiness.MANIFEST).read_text(encoding="utf-8"))
    next_manifest["jax_platform_revision"] = "e" * 40
    next_manifest["jax_revision"] = "f" * 40
    next_attestation = json.loads((next_release / "build-attestation.json").read_text(encoding="utf-8"))
    next_attestation["platform_commit"] = "e" * 40
    next_attestation["jax_commit"] = "f" * 40
    (next_release / "build-attestation.json").write_text(json.dumps(next_attestation), encoding="utf-8")
    (next_release / "release-lineage.json").write_text(json.dumps({"schema": "axioma.pair-lineage/v1", "platform_predecessor": "a" * 40, "jax_predecessor": "b" * 40}), encoding="utf-8")
    next_manifest["files"] = readiness._inventory(next_release, owner_uid=os.getuid(), owner_gid=os.getgid())
    (next_release / readiness.MANIFEST).write_text(json.dumps(next_manifest), encoding="utf-8")
    next_digest = hashlib.sha256((next_release / readiness.MANIFEST).read_bytes()).hexdigest()
    subprocess.run(["sudo", "-n", "chown", "-R", "root:root", str(root)], check=True)
    base_env = {
        "JAX_PAIR_TEST_MODE": "1",
        "JAX_PAIR_TEST_ROOT": str(root),
        "JAX_PAIR_SYSTEMCTL_BIN": str(fake_systemctl),
        "JAX_PAIR_TEST_CALLS": str(calls),
        "JAX_PAIR_TEST_LAUNCHER": str(test_launcher),
        "JAX_PAIR_TEST_SERVICE_PID": str(service_pid),
        "JAX_PAIR_TEST_MIGRATION_STATE": str(migration_state),
        "JAX_PAIR_TEST_RELEASE": str(release),
        "JAX_PAIR_HEALTH_BIN": str(health),
        "JAX_PAIR_TEST_HEALTH_CALLS": str(health_calls),
        "JAX_PAIR_PROVISIONER_BIN": str(provisioner),
        "JAX_PAIR_TEST_PROVISION_CALLS": str(provision_calls),
    }
    command = ["sudo", "-n", "env", *[f"{key}={value}" for key, value in base_env.items()], "JAX_PAIR_TEST_SLEEP=0.4", str(MIGRATION / "activate-platform-pair-release.sh"), "--root", str(root), "--release", str(release), "--expected-manifest-sha256", digest, "--previous-manifest-sha256", previous_digest, "--measured-grantee-host", "127.0.0.1"]
    script_index = command.index(str(MIGRATION / "activate-platform-pair-release.sh"))
    killed_command = command[:script_index] + ["JAX_PAIR_TEST_KILL_AT=after_publish"] + command[script_index:]
    killed = subprocess.run(killed_command, text=True, capture_output=True, check=False)
    assert killed.returncode != 0
    journal = json.loads((root / "activation-journal.json").read_text(encoding="utf-8"))
    assert journal["schema"] == "axioma.pair-activation/v1"
    assert journal["state"] == "PRE_DDL"
    assert (root / "current").resolve() == release
    for mode in ("migrate", "web"):
        blocked = subprocess.run(
            ["sudo", "-n", "env", "JAX_PAIR_TEST_MODE=1", f"JAX_PAIR_TEST_ROOT={root}",
             "/usr/bin/python3", "-I", "-S", str(MIGRATION / "jax-platform-pair-exec.py"), mode, str(credential)],
            text=True, capture_output=True, check=False,
        )
        assert blocked.returncode != 0
    assert not migration_started.exists()
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 5
    while not calls.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert calls.exists(), first.communicate(timeout=5)[1]
    deadline = time.monotonic() + 5
    while "start jax-platform.service" not in calls.read_text(encoding="utf-8") and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "start jax-platform.service" in calls.read_text(encoding="utf-8")
    contended = command[:script_index] + ["JAX_PAIR_TEST_LOCK_SECONDS=0.1"] + command[script_index:]
    second = subprocess.run(contended, text=True, capture_output=True, check=False)
    assert second.returncode != 0
    assert "activation lock timeout" in second.stderr
    while not migration_started.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert migration_started.exists()
    stdout, stderr = first.communicate(timeout=10)
    assert first.returncode == 0, stderr or stdout
    selection = json.loads((root / "platform-pair-selection.json").read_text(encoding="utf-8"))
    assert selection["release"] == str(release)
    assert selection["manifest_sha256"] == digest
    assert calls.read_text(encoding="utf-8").splitlines()[-32:] == [
        "stop jax-platform.service", "stop jax-platform-migrate.service",
        "show --value --property=ActiveState jax-platform.service", "show --value --property=SubState jax-platform.service", "show --value --property=MainPID jax-platform.service", "show --value --property=Result jax-platform.service",
        "show --value --property=ActiveState jax-platform-migrate.service", "show --value --property=SubState jax-platform-migrate.service", "show --value --property=MainPID jax-platform-migrate.service", "show --value --property=Result jax-platform-migrate.service",
        "start jax-platform-migrate.service",
        "show --value --property=ActiveState jax-platform-migrate.service", "show --value --property=SubState jax-platform-migrate.service", "show --value --property=MainPID jax-platform-migrate.service", "show --value --property=Result jax-platform-migrate.service",
        "start jax-platform.service",
        "show --value --property=ActiveState jax-platform.service", "show --value --property=SubState jax-platform.service", "show --value --property=MainPID jax-platform.service", "show --value --property=Result jax-platform.service",
        "show --value --property=ActiveState jax-platform.service", "show --value --property=SubState jax-platform.service", "show --value --property=MainPID jax-platform.service", "show --value --property=Result jax-platform.service",
        "show --value --property=ActiveState jax-platform.service", "show --value --property=SubState jax-platform.service", "show --value --property=MainPID jax-platform.service", "show --value --property=Result jax-platform.service",
        "show --value --property=ActiveState jax-platform.service", "show --value --property=SubState jax-platform.service", "show --value --property=MainPID jax-platform.service", "show --value --property=Result jax-platform.service",
    ]
    assert provision_calls.read_text(encoding="utf-8") == f"--env /etc/jax/.env --measured-grantee-host 127.0.0.1\n"
    assert health_calls.read_text(encoding="utf-8") == "3"
    assert output.read_text(encoding="utf-8") == f"{release / 'jax'}|||/usr/bin:/bin"
    lock_inode = (root / ".runtime.lock").stat().st_ino
    repeat = subprocess.run(command[:-3] + [digest, *command[-2:]], text=True, capture_output=True, check=False)
    assert repeat.returncode == 0, repeat.stderr
    assert (root / ".runtime.lock").stat().st_ino == lock_inode
    # A different process can bind the health listener after the first state
    # sample.  A successful HTTP response alone must not complete DDL_STARTED.
    negative_env = dict(base_env)
    negative_env["JAX_PAIR_TEST_RELEASE"] = str(next_release)
    negative_command = ["sudo", "-n", "env", *[f"{key}={value}" for key, value in negative_env.items()], "JAX_PAIR_TEST_SLEEP=0", "JAX_PAIR_TEST_SWITCH_PID=1", str(MIGRATION / "activate-platform-pair-release.sh"), "--root", str(root), "--release", str(next_release), "--expected-manifest-sha256", next_digest, "--previous-manifest-sha256", digest, "--measured-grantee-host", "127.0.0.1"]
    changed_pid = subprocess.run(negative_command, text=True, capture_output=True, check=False)
    assert changed_pid.returncode != 0
    assert "web service changed during health check" in changed_pid.stderr
    assert json.loads((root / "activation-journal.json").read_text(encoding="utf-8"))["state"] == "DDL_STARTED"
    escaped = subprocess.run(["sudo", "-n", "env", *[f"{key}={value}" for key, value in base_env.items()], str(MIGRATION / "activate-platform-pair-release.sh"), "--root", str(root), "--release", str(root / "releases" / ".." / "outside"), "--expected-manifest-sha256", digest, "--previous-manifest-sha256", digest, "--measured-grantee-host", "127.0.0.1"], text=True, capture_output=True, check=False)
    assert escaped.returncode != 0
    assert "unsafe release" in escaped.stderr
    bash_env = tmp_path / "bash-env"; bash_env.write_text(f"touch {tmp_path / 'bash-env-ran'}\n", encoding="utf-8")
    launcher = ["sudo", "-n", "env", "JAX_PAIR_TEST_MODE=1", f"JAX_PAIR_TEST_ROOT={root}", "JAX_REPO_PATH=/attacker/checkout", "PATH=/attacker", f"BASH_ENV={bash_env}", "LD_PRELOAD=/attacker.so", "PYTHONPATH=/attacker", "/usr/bin/python3", "-I", "-S", str(MIGRATION / "jax-platform-pair-exec.py"), "web", str(credential)]
    result = subprocess.run(launcher, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == f"{next_release / 'jax'}|||/usr/bin:/bin"
    assert not (tmp_path / "bash-env-ran").exists()
    hostile = tmp_path / "hostile.env"; hostile.write_text("PATH=/attacker\n", encoding="utf-8")
    rejected = subprocess.run(launcher[:-1] + [str(hostile)], text=True, capture_output=True, check=False)
    assert rejected.returncode != 0
