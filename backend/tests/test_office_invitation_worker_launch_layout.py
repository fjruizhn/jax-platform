"""The systemd worker must start from the host's real release/venv layout."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_isolated_launcher_imports_from_a_trusted_sibling_backend():
    launcher = ROOT / "ops" / "virtualoffice" / "run-invitation-worker.py"
    completed = subprocess.run(
        [sys.executable, "-Es", str(launcher), "--check-import"],
        cwd=ROOT / "backend", text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_systemd_unit_uses_the_real_host_venv_and_release_tree():
    unit = (ROOT / "ops" / "virtualoffice" / "invitation-worker.service").read_text(encoding="utf-8")
    assert "WorkingDirectory=/srv/jax-prod/jax-platform/backend" in unit
    assert "ExecStartPre=/srv/jax-prod/jax-platform/ops/virtualoffice/invitation-worker-readiness.sh" in unit
    assert "ExecStart=/srv/jax-prod/jax-platform/backend/.venv/bin/python -Es /srv/jax-prod/jax-platform/ops/virtualoffice/run-invitation-worker.py" in unit
    assert "/srv/jax-prod/current" not in unit
    assert "/srv/jax-prod/venv" not in unit
