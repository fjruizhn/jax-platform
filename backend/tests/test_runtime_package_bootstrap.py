from pathlib import Path


def test_runtime_importer_executes_only_audited_bootstrap_and_validator() -> None:
    root = Path(__file__).resolve().parents[2]
    installer = (root / "ops/migration/install-platform-pair-runtime.sh").read_text(encoding="utf-8")
    bootstrap = (root / "ops/migration/runtime_package.py").read_text(encoding="utf-8")

    assert "--expected-package-digest" in installer
    # Bash parses $11 as ${1}1; all six option/value pairs must retain their positions.
    assert "${11} == --effective-validator-sha256" in installer
    assert "VALIDATOR=${10}" in installer
    assert "VALIDATOR_DIGEST=${12}" in installer
    assert "--bootstrap-sha256" in installer
    assert "--effective-validator-sha256" in installer
    assert '"/proc/self/fd/$bootstrap_fd" import' in installer
    assert "$PACKAGE/artifact_readiness.py" not in installer
    assert "RECOVERY_REQUIRED" in bootstrap
    assert '"/usr/bin/systemctl", "daemon-reload"' in bootstrap
    assert '"/usr/bin/systemd-analyze", "verify"' in bootstrap
    assert '"/", "--units-dir", str(units_dir), "/usr/bin/busctl"' in bootstrap
    assert '"/proc/self/fd/{validator_fd}"' in bootstrap
