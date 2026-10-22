"""The invitation worker may execute only a root-owned snapshot."""
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_worker_release_installer_requires_an_exact_revision():
    installer = ROOT / "ops" / "virtualoffice" / "install-invitation-worker-release.sh"
    completed = subprocess.run(["bash", str(installer), "--help"], text=True, capture_output=True, check=False)
    assert completed.returncode == 0
    assert "--revision SHA" in completed.stdout
    source = installer.read_text(encoding="utf-8")
    assert 'git -C "$trusted_repo" archive' in source
    assert "GIT_NO_REPLACE_OBJECTS=1" in source
    assert "chown -R root:root" in source
    assert "-type l" in source
    assert "--require-hashes --no-deps" in source
    assert "runuser -u" in source
    assert '"$stage/backend/.venv/bin/pip" check' in source
    lock = ROOT / "ops" / "virtualoffice" / "worker-requirements.lock"
    assert "--hash=sha256:" in lock.read_text(encoding="utf-8")


def test_systemd_unit_uses_the_immutable_worker_release():
    unit = (ROOT / "ops" / "virtualoffice" / "invitation-worker.service").read_text(encoding="utf-8")
    assert "WorkingDirectory=/opt/axioma-office-invitation-worker/backend" in unit
    assert "ExecStartPre=/usr/local/libexec/axioma-office-invitation-worker-readiness" in unit
    assert "ExecStart=/opt/axioma-office-invitation-worker/backend/.venv/bin/python -Es -m virtual_office.invitation_delivery_worker" in unit
    assert "/srv/jax-prod/jax-platform" not in unit
    assert "ExecStartPre=+" not in unit
    assert "PrivateDevices=true" in unit
    assert "CapabilityBoundingSet=\n" in unit
    assert "LimitCORE=0" in unit
    assert "TimeoutStartSec=3h" in unit
    runbook = (ROOT / "docs" / "runbooks" / "invitation-delivery-worker.md").read_text(encoding="utf-8")
    assert "--working-directory=/opt/axioma-office-invitation-worker/backend" in runbook
    assert 'git -C "$SOURCE" archive "$SHA"' in runbook
