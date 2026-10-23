"""Contracts that prevent legacy pair publication and ambiguous activation."""
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]


def test_legacy_pair_installer_is_explicitly_disabled() -> None:
    installer = (_ROOT / "ops/migration/install-platform-pair-release.sh").read_text(encoding="utf-8")
    assert "is disabled" in installer
    assert "sealed jaxbuild builder and root adopter handoff" in installer
    assert "exit 64" in installer


def test_activation_requires_externally_pinned_target_and_predecessor() -> None:
    activator = (_ROOT / "ops/migration/activate-platform-pair-release.sh").read_text(encoding="utf-8")
    assert "external target and predecessor manifest digests required" in activator
    assert "predecessor digest mismatch" in activator
    assert "--previous-manifest-sha256 SHA" in activator
    assert "--measured-grantee-host HOST" in activator
    assert "start jax-platform-migrate.service" in activator
    assert '"$PROVISIONER" --env "$ENV_FILE" --measured-grantee-host "$MEASURED_HOST"' in activator
    assert "start jax-platform.service" in activator
    assert "RemainAfterExit=yes" in (_ROOT / "ops/migration/systemd-units/jax-platform-migrate.service").read_text(encoding="utf-8")
