"""First-install Office settings must survive the installed credential boundary."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
PROVISION = ROOT / "ops/migration/provision-db-runtime.sh"
LAUNCHER = ROOT / "ops/virtualoffice/office-pair-exec.py"
MFA = {
    "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120",
    "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
    "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300",
    "OFFICE_MFA_RECENCY_SECONDS": "300",
    "OFFICE_MFA_RECOVERY_CODE_COUNT": "10",
}


def _plan(tmp_path: Path, settings: dict[str, str], *, provision: Path = PROVISION,
          launcher_override: bool = True) -> subprocess.CompletedProcess[str]:
    values = {
        "JAX_DB_HOST": "127.0.0.1", "JAX_DB_PORT": "3310",
        "JAX_DB_NAME": "axioma_office", "JAX_DB_GRANTEE_HOST": "172.17.0.1",
        "JAX_RUNTIME_DB_USER": "officewebdb", "JAX_MIGRATE_DB_USER": "officemigratedb",
        "OFFICE_DEDICATED_DB": "1", "OFFICE_DB_SERVER_ID": "61027",
        "OFFICE_DB_HOSTNAME": "axioma-office-db", "OFFICE_MFA_POLICY": "optional",
        "OFFICE_BROKER_ALLOWED_BASE_URLS": "https://api.z.ai/api/paas/v4",
        "OFFICE_INVITATION_LIFETIME_SECONDS": "86400",
        "OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS": "120",
        "OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT": "50",
        "OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT": "25",
        "OFFICE_INVITATION_DELIVERY_BATCH_LIMIT": "10",
        "OFFICE_INVITATION_PUBLIC_BASE_URL": "https://virtualoffice.axioma-ia.io",
        "OFFICE_PUBLIC_ORIGIN": "https://virtualoffice.axioma-ia.io",
        **MFA,
        **settings,
    }
    env_file = tmp_path / "deployment.env"
    env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("JAX_PAIR_EXEC_FIXTURE", None)
    if launcher_override:
        environment["JAX_PAIR_EXEC_FIXTURE"] = str(LAUNCHER)
    return subprocess.run(
        ["bash", str(provision), "--plan", "--skip-os-identities", "--env", str(env_file),
         "--admin-transport", "/bin/true", "--measured-grantee-host", "172.17.0.1"],
        env=environment,
        text=True, capture_output=True, check=False,
    )


def test_office_first_install_plan_accepts_complete_mfa_and_invitation_settings(tmp_path):
    result = _plan(tmp_path, {})
    assert result.returncode == 0, result.stderr


def test_office_first_install_rejects_invalid_mfa_policy_value(tmp_path):
    result = _plan(tmp_path, {"OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "6"})
    assert result.returncode != 0
    assert "invalid Office MFA setting" in result.stderr


def test_office_first_install_rejects_missing_mfa_setting(tmp_path):
    result = _plan(tmp_path, {"OFFICE_MFA_RECOVERY_CODE_COUNT": ""})
    assert result.returncode != 0
    assert "missing required key: OFFICE_MFA_RECOVERY_CODE_COUNT" in result.stderr


def test_office_launcher_accepts_every_mfa_key():
    result = subprocess.run(
        ["python3", "-I", "-S", str(LAUNCHER), "--list-allowed-keys"],
        text=True, capture_output=True, check=True,
    )
    assert set(MFA).issubset(result.stdout.splitlines())


def test_office_provisioner_selects_office_launcher_without_test_override(tmp_path):
    fixture = tmp_path / "installed"
    fixture.mkdir()
    script = fixture / "provision-db-runtime.sh"
    office_launcher = fixture / "axioma-office-pair-exec.py"
    jax_launcher = fixture / "jax-platform-pair-exec.py"
    shutil.copyfile(LAUNCHER, office_launcher)
    shutil.copyfile(ROOT / "ops/migration/jax-platform-pair-exec.py", jax_launcher)
    for manifest in ("runtime-table-privileges-v1.txt", "runtime-table-privileges-v2.txt"):
        shutil.copyfile(ROOT / "ops/migration" / manifest, fixture / manifest)
    source = PROVISION.read_text(encoding="utf-8")
    source = source.replace("/usr/local/libexec/axioma-office-pair-exec.py", str(office_launcher))
    source = source.replace("/usr/local/libexec/jax-platform-pair-exec.py", str(jax_launcher))
    script.write_text(source, encoding="utf-8")
    result = _plan(tmp_path, {}, provision=script, launcher_override=False)
    assert result.returncode == 0, result.stderr
