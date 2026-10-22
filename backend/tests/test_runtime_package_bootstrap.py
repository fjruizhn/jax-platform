from pathlib import Path


def test_runtime_importer_executes_only_audited_bootstrap_and_validator() -> None:
    root = Path(__file__).resolve().parents[2]
    installer = (root / "ops/migration/install-platform-pair-runtime.sh").read_text(encoding="utf-8")
    bootstrap = (root / "ops/migration/runtime_package.py").read_text(encoding="utf-8")

    assert "--expected-package-digest" in installer
    assert "--bootstrap-sha256" in installer
    assert "--effective-validator-sha256" in installer
    assert '"$BOOTSTRAP" import' in installer
    assert "$PACKAGE/artifact_readiness.py" not in installer
    assert "RECOVERY_REQUIRED" in bootstrap
    assert '"/usr/bin/systemctl", "daemon-reload"' in bootstrap
    assert '"/usr/bin/systemd-analyze", "verify"' in bootstrap
    assert 'str(validator), "/", "/usr/bin/busctl"' in bootstrap
