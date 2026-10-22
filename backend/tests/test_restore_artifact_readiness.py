"""The restored units must match the release's complete effective contract."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "ops" / "migration" / "artifact-readiness.sh"


def _artifact(tmp_path: Path) -> Path:
    artifact = tmp_path / "srv" / "jax-prod" / "jax-platform"
    bin_dir = artifact / "backend" / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("python", "uvicorn"):
        executable = bin_dir / name
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o700)
    return artifact


def _contract(artifact: Path) -> dict[str, dict[str, object]]:
    backend = str(artifact / "backend")
    return {
        "jax-platform.service": {
            "WorkingDirectory": backend, "User": "jaxweb", "Group": "", "SupplementaryGroups": [],
            "Environment": ["PATH=/home/fruiz/.nvm/versions/node/v24.16.0/bin:/home/fruiz/.local/bin:/usr/local/bin:/usr/bin:/bin", "JAX_REPO_PATH=/opt/jax-platform-pair/current/jax"],
            "EnvironmentFiles": [["/etc/jax/runtime.env", False]],
            "ExecStartEx": [[f"{backend}/.venv/bin/uvicorn", [f"{backend}/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=127.0.0.1", "--no-access-log", "--log-level", "warning"], [], 0, 0, 0, 0, 0, 0, 0]],
            "ExecStartPreEx": [["/usr/local/libexec/jax-platform-pair-artifact-readiness", ["/usr/local/libexec/jax-platform-pair-artifact-readiness"], [], 0, 0, 0, 0, 0, 0, 0]], "ExecStartPostEx": [], "ExecConditionEx": [], "Type": "simple", "RemainAfterExit": False,
            "Requires": ["jax-platform-migrate.service"], "After": ["network.target", "docker.service", "jax-las-manos.service", "jax-platform-migrate.service"],
        },
        "jax-platform-migrate.service": {
            "WorkingDirectory": backend, "User": "jaxmigrate", "Group": "", "SupplementaryGroups": [],
            "Environment": ["JAX_REPO_PATH=/opt/jax-platform-pair/current/jax"], "EnvironmentFiles": [["/etc/jax/migrate.env", False]],
            "ExecStartEx": [[f"{backend}/.venv/bin/python", [f"{backend}/.venv/bin/python", "-m", "db.migrate"], [], 0, 0, 0, 0, 0, 0, 0]],
            "ExecStartPreEx": [["/usr/local/libexec/jax-platform-pair-artifact-readiness", ["/usr/local/libexec/jax-platform-pair-artifact-readiness"], [], 0, 0, 0, 0, 0, 0, 0]], "ExecStartPostEx": [], "ExecConditionEx": [], "Type": "oneshot", "RemainAfterExit": False,
            "Requires": ["docker.service"], "After": ["network-online.target", "docker.service"],
        },
    }


def _fake_busctl(tmp_path: Path, contract: dict[str, dict[str, object]]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture = tmp_path / "systemd.json"
    fixture.write_text(json.dumps(contract), encoding="utf-8")
    fake = tmp_path / "busctl"
    fake.write_text(
        "#!/usr/bin/env python3\nimport json, sys\n"
        f"contract = json.load(open({str(fixture)!r}, encoding='utf-8'))\nargs = sys.argv[1:]\n"
        "if args[:5] == ['--json=short', 'call', 'org.freedesktop.systemd1', '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager']:\n"
        " unit=args[-1]; print(json.dumps({'type':'o','data':['/org/freedesktop/systemd1/unit/'+unit.replace('-', '_2d').replace('.', '_2e')]})); raise SystemExit(0)\n"
        "if args[:3] == ['--json=short', 'get-property', 'org.freedesktop.systemd1']:\n"
        " prop=args[-1]; unit='jax-platform-migrate.service' if 'migrate' in args[3] else 'jax-platform.service'; value=contract[unit][prop]\n"
        " types={'WorkingDirectory':'s','User':'s','Group':'s','SupplementaryGroups':'as','Environment':'as','EnvironmentFiles':'a(sb)','ExecStartEx':'a(sasasttttuii)','ExecStartPreEx':'a(sasasttttuii)','ExecStartPostEx':'a(sasasttttuii)','ExecConditionEx':'a(sasasttttuii)','Type':'s','RemainAfterExit':'b','Requires':'as','After':'as'}\n"
        " print(json.dumps({'type':types[prop],'data':value})); raise SystemExit(0)\nraise SystemExit(9)\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    return fake


def _run(artifact: Path, busctl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", 'source "$1"; verify_platform_artifact_readiness "$2" "$3"', "bash", str(HELPER), str(artifact), str(busctl)],
        text=True, capture_output=True, check=False,
    )


def test_restore_accepts_complete_effective_source_unit_contract(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    result = _run(artifact, _fake_busctl(tmp_path, _contract(artifact)))
    assert result.returncode == 0, result.stderr


def test_restore_rejects_effective_contract_tampering(tmp_path: Path) -> None:
    cases = {
        "wrong argv": lambda c: c["jax-platform.service"]["ExecStartEx"][0][1].append("--reload"),
        "empty forwarded proxy allowlist": lambda c: c["jax-platform.service"]["ExecStartEx"][0][1].__setitem__(7, "--forwarded-allow-ips="),
        "wildcard forwarded proxy allowlist": lambda c: c["jax-platform.service"]["ExecStartEx"][0][1].__setitem__(7, "--forwarded-allow-ips=*"),
        "privileged start command": lambda c: c["jax-platform.service"]["ExecStartEx"][0].__setitem__(2, ["full-privileges"]),
        "no setuid start command": lambda c: c["jax-platform.service"]["ExecStartEx"][0].__setitem__(2, ["no-setuid"]),
        "ignored start command": lambda c: c["jax-platform.service"]["ExecStartEx"][0].__setitem__(2, ["ignore-failure"]),
        "argv zero start command": lambda c: c["jax-platform.service"]["ExecStartEx"][0].__setitem__(2, ["argv0"]),
        "wrong user": lambda c: c["jax-platform.service"].__setitem__("User", "root"),
        "wrong group": lambda c: c["jax-platform.service"].__setitem__("Group", "root"),
        "legacy credential group": lambda c: c["jax-platform.service"].__setitem__("SupplementaryGroups", ["jaxlegacy"]),
        "common and extra environment": lambda c: c["jax-platform.service"].__setitem__("EnvironmentFiles", [["/etc/jax/runtime.env", False], ["/etc/jax/.env", False]]),
        "injected environment": lambda c: c["jax-platform.service"].__setitem__("Environment", ["JAX_DB_PASSWORD=unexpected"]),
        "ignored environment file": lambda c: c["jax-platform.service"].__setitem__("EnvironmentFiles", [["/etc/jax/runtime.env", True]]),
        "additional start command": lambda c: c["jax-platform.service"].__setitem__("ExecStartEx", c["jax-platform.service"]["ExecStartEx"] * 2),
        "legacy start property only": lambda c: c["jax-platform.service"].__setitem__("ExecStart", c["jax-platform.service"].pop("ExecStartEx")),
        "pre command": lambda c: c["jax-platform.service"].__setitem__("ExecStartPreEx", [["/bin/true", ["/bin/true"], [], 0, 0, 0, 0, 0, 0, 0]]),
        "post command": lambda c: c["jax-platform.service"].__setitem__("ExecStartPostEx", [["/bin/true", ["/bin/true"], [], 0, 0, 0, 0, 0, 0, 0]]),
        "condition command": lambda c: c["jax-platform.service"].__setitem__("ExecConditionEx", [["/bin/true", ["/bin/true"], [], 0, 0, 0, 0, 0, 0, 0]]),
        "legacy pre property only": lambda c: c["jax-platform.service"].__setitem__("ExecStartPre", c["jax-platform.service"].pop("ExecStartPreEx")),
        "missing migration dependency": lambda c: c["jax-platform.service"].__setitem__("Requires", []),
        "missing docker dependency": lambda c: c["jax-platform-migrate.service"].__setitem__("After", []),
        "wrong type": lambda c: c["jax-platform-migrate.service"].__setitem__("Type", "simple"),
        "remain after exit": lambda c: c["jax-platform-migrate.service"].__setitem__("RemainAfterExit", True),
    }
    for name, mutate in cases.items():
        artifact = _artifact(tmp_path / name.replace(" ", "_"))
        contract = _contract(artifact)
        mutate(contract)
        result = _run(artifact, _fake_busctl(tmp_path / name.replace(" ", "_"), contract))
        assert result.returncode != 0, name
        assert "artifact readiness:" in result.stderr


def test_restore_rejects_missing_artifact_or_invalid_dbus_reply(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    (artifact / "backend" / ".venv" / "bin" / "uvicorn").unlink()
    result = _run(artifact, _fake_busctl(tmp_path, _contract(artifact)))
    assert result.returncode != 0
    assert "missing executable" in result.stderr

    for name, payload in {
        "corrupt": "{not-json}",
        "unsupported": '{"type":"x","data":[]}',
        "missing data": '{"type":"o"}',
    }.items():
        artifact = _artifact(tmp_path / name)
        busctl = _fake_busctl(tmp_path / name, _contract(artifact))
        busctl.write_text(f"#!/bin/sh\nprintf '%s\\n' '{payload}'\n", encoding="utf-8")
        busctl.chmod(0o700)
        result = _run(artifact, busctl)
        assert result.returncode != 0, name
        assert "artifact readiness:" in result.stderr
