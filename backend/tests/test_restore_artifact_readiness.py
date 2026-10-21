"""Restore validates the artifact that its units will actually execute."""
from __future__ import annotations

import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "ops" / "migration" / "artifact-readiness.sh"


def _fake_systemctl(tmp_path: Path, artifact: Path, *, mismatch: bool = False, exec_mismatch: bool = False) -> Path:
    fake = tmp_path / "systemctl"
    backend = artifact / "backend"
    effective = backend if not mismatch else tmp_path / "other" / "backend"
    fake.write_text(
        "#!/bin/sh\ncase \"$2\" in\n"
        f"jax-platform.service) bin={effective}/.venv/bin/{'wrong' if exec_mismatch else 'uvicorn'};;\n"
        f"jax-platform-migrate.service) bin={effective}/.venv/bin/python;;\nesac\n"
        f"printf '%s\\n' 'WorkingDirectory={effective}' \"ExecStart={{ path=$bin ; argv[]=$bin main:app ; }}\"\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    return fake


def _run(artifact: Path, command: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", 'source "$1"; verify_platform_artifact_readiness "$2" "$3"', "bash", str(HELPER), str(artifact), str(command)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_restore_artifact_and_effective_units_share_the_same_target(tmp_path: Path) -> None:
    artifact = tmp_path / "srv" / "jax-prod" / "jax-platform"
    python = artifact / "backend" / ".venv" / "bin" / "python"; uvicorn = python.with_name("uvicorn")
    python.parent.mkdir(parents=True)
    for executable in (python, uvicorn): executable.write_text("#!/bin/sh\n", encoding="utf-8"); executable.chmod(0o700)

    result = _run(artifact, _fake_systemctl(tmp_path, artifact))

    assert result.returncode == 0, result.stderr


def test_restore_rejects_missing_artifact_executable_before_start_advice(tmp_path: Path) -> None:
    artifact = tmp_path / "srv" / "jax-prod" / "jax-platform"
    (artifact / "backend").mkdir(parents=True)

    result = _run(artifact, _fake_systemctl(tmp_path, artifact))

    assert result.returncode != 0
    assert "missing executable for jax-platform.service" in result.stderr


def test_restore_rejects_effective_unit_target_different_from_published_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "srv" / "jax-prod" / "jax-platform"
    python = artifact / "backend" / ".venv" / "bin" / "python"; uvicorn = python.with_name("uvicorn")
    python.parent.mkdir(parents=True)
    for executable in (python, uvicorn): executable.write_text("#!/bin/sh\n", encoding="utf-8"); executable.chmod(0o700)

    result = _run(artifact, _fake_systemctl(tmp_path, artifact, mismatch=True))

    assert result.returncode != 0
    assert "WorkingDirectory differs from artifact" in result.stderr


def test_restore_rejects_effective_execstart_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "srv" / "jax-prod" / "jax-platform"
    python = artifact / "backend" / ".venv" / "bin" / "python"; uvicorn = python.with_name("uvicorn")
    python.parent.mkdir(parents=True)
    for executable in (python, uvicorn): executable.write_text("#!/bin/sh\n", encoding="utf-8"); executable.chmod(0o700)
    result = _run(artifact, _fake_systemctl(tmp_path, artifact, exec_mismatch=True))
    assert result.returncode != 0
    assert "ExecStart differs from artifact" in result.stderr
