"""The first Office release owns a selector that JAX cannot advance."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "ops/virtualoffice/install-office-pair-release.py"


def test_office_units_and_launcher_do_not_follow_the_global_jax_selector():
    base = SCRIPT.parent
    launcher = (base / "office-pair-exec.py").read_text(encoding="utf-8")
    assert "/opt/axioma-office-pair" in launcher
    assert "/etc/axioma-office/platform-pair-selection.json" in launcher
    assert "/opt/jax-platform-pair" not in launcher
    assert "/etc/jax/platform-pair-selection.json" not in launcher
    for name in ("axioma-office-backend.service", "axioma-office-migrate.service"):
        unit = (base / "systemd" / name).read_text(encoding="utf-8")
        assert "/usr/local/libexec/axioma-office-pair-exec.py" in unit
        assert "/usr/local/libexec/jax-platform-pair-exec.py" not in unit


def test_first_install_runbook_adopts_without_jax_activation_and_imports_owner_before_start():
    runbook = (SCRIPT.parents[2] / "docs/runbooks/office-dedicated-stack.md").read_text(encoding="utf-8")
    assert "sudo /usr/local/libexec/adopt-pair-root.py" in runbook
    assert "sudo /usr/local/libexec/bootstrap-pair-predecessor.py" not in runbook
    assert "sudo /usr/local/libexec/activate-platform-pair-release.sh" not in runbook
    assert runbook.index('"$PAIR/ops/virtualoffice/import-owner.py"') < runbook.index(
        'sudo systemctl enable --now axioma-office-backend.service')
    assert "sudo systemctl is-enabled --quiet axioma-office-backend.service" in runbook


def test_first_install_runbook_supplies_invitation_configuration_before_runtime_provisioning():
    runbook = (SCRIPT.parents[2] / "docs/runbooks/office-dedicated-stack.md").read_text(encoding="utf-8")
    first_runtime_plan = runbook.index('sudo "$PROVISION" --plan --skip-os-identities')
    for key in (
        "JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_NAME", "JAX_DB_GRANTEE_HOST",
        "JAX_RUNTIME_DB_USER", "JAX_MIGRATE_DB_USER", "OFFICE_DEDICATED_DB",
        "OFFICE_DB_SERVER_ID", "OFFICE_DB_HOSTNAME", "OFFICE_MFA_POLICY",
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS", "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS", "OFFICE_MFA_RECENCY_SECONDS",
        "OFFICE_MFA_RECOVERY_CODE_COUNT",
        "OFFICE_BACKEND_BIND", "OFFICE_BACKEND_PORT", "OFFICE_BROKER_ALLOWED_BASE_URLS",
        "JAX_ENVIRONMENT", "JAX_PUBLIC_HOST_ORIGIN_BINDINGS",
        "JAX_OPERATIONAL_TRUSTED_HOSTS", "JAX_TRUSTED_PROXIES",
        "JAX_REFRESH_COOKIE_SECURE", "JAX_JWT_SECRET", "FERNET_KEY",
        "OFFICE_INVITATION_LIFETIME_SECONDS", "OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS",
        "OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT", "OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT",
        "OFFICE_INVITATION_DELIVERY_BATCH_LIMIT", "OFFICE_INVITATION_PUBLIC_BASE_URL",
        "OFFICE_PUBLIC_ORIGIN",
    ):
        assert f"{key}=" in runbook[:first_runtime_plan]
    assert "FRONTEND_ORIGIN=https://virtualoffice.axioma-ia.io" in runbook[:first_runtime_plan]


def test_office_pair_initial_install_is_idempotent_and_rejects_silent_upgrade(tmp_path, monkeypatch):
    source_root = tmp_path / "jax-pairs"
    source = source_root / "first"
    source.mkdir(parents=True)
    (source / "pair-manifest.json").write_text('{"fixture":true}\n', encoding="utf-8")
    (source / "payload").write_text("first release", encoding="utf-8")
    (source / "payload-link").symlink_to("payload")
    manifest_digest = hashlib.sha256((source / "pair-manifest.json").read_bytes()).hexdigest()
    office_root = tmp_path / "office-pairs"
    (office_root / "releases").mkdir(parents=True)
    helper = office_root / "libexec/pair_artifact_readiness.py"
    helper.parent.mkdir()
    helper.write_text("def verify(*args, **kwargs): pass\n", encoding="utf-8")
    (office_root / ".runtime.lock").touch()
    selection = tmp_path / "platform-pair-selection.json"
    spec = importlib.util.spec_from_file_location("office_pair_install_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", office_root)
    monkeypatch.setattr(module, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(module, "SELECTION", selection)
    monkeypatch.setattr(module, "HELPER", helper)
    monkeypatch.setattr(module, "inactive", lambda _unit: None)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module, "read_selection", lambda path=None: json.loads((path or selection).read_text()))

    def run(platform_sha):
        monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--source-release", str(source),
                                      "--expected-platform-sha", platform_sha,
                                      "--expected-jax-sha", "b" * 40,
                                      "--expected-manifest-sha256", manifest_digest])
        module.main()

    run("a" * 40)
    first_selection = selection.read_bytes()
    first_release = (office_root / "current").resolve(strict=True)
    assert (first_release / "payload").read_text() == "first release"
    assert (first_release / "payload-link").is_symlink()
    run("a" * 40)
    assert selection.read_bytes() == first_selection
    with pytest.raises(RuntimeError, match="upgrade requires"):
        run("c" * 40)
    assert selection.read_bytes() == first_selection
    assert (office_root / "current").resolve(strict=True) == first_release

    # A power cut after one of the two selector writes must be resumable.
    selection.unlink()
    run("a" * 40)
    assert json.loads(selection.read_text())["release"] == str(first_release)
    (office_root / "current").unlink()
    run("a" * 40)
    assert (office_root / "current").resolve(strict=True) == first_release

    selection.unlink()
    (office_root / "current").unlink()
    (office_root / ".current.new").symlink_to(Path("releases") / first_release.name)
    selection.with_name(".platform-pair-selection.new").write_bytes(first_selection)
    run("a" * 40)
    assert (office_root / "current").resolve(strict=True) == first_release
    assert selection.read_bytes() == first_selection
