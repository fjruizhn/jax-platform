"""Regression tests for the Office deployment DB-principal provisioner.

The helper is deliberately invoked with only temporary paths here.  It must
never derive its grantee host from DB_HOST or read a host's live secrets.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "ops" / "migration" / "provision-db-runtime.sh"
RESTORE = REPO / "ops" / "migration" / "restore-hall9000.sh"


def run_helper(tmp_path: Path, env_text: str, *args: str) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "legacy.env"
    env_file.write_text(env_text, encoding="utf-8")
    run_env = os.environ | {"PATH": os.environ["PATH"]}
    return subprocess.run(
        ["bash", str(HELPER), "--plan", "--env", str(env_file), *args],
        cwd=REPO,
        env=run_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_plan_requires_explicit_measured_non_wildcard_grantee_host(tmp_path: Path) -> None:
    result = run_helper(
        tmp_path,
        "JAX_DB_HOST=127.0.0.1\n"
        "JAX_DB_PORT=3308\n"
        "JAX_DB_NAME=jax_memory\n"
        "JAX_DB_USER=legacy_las_manos\n"
        "JAX_DB_PASSWORD=legacy-secret\n"
        "JAX_DB_GRANTEE_HOST=%\n"
        "JAX_RUNTIME_DB_USER=jaxweb_runtime\n"
        "JAX_MIGRATE_DB_USER=jaxmigrate_ddl\n",
        "--measured-grantee-host",
        "172.30.5.1",
    )

    assert result.returncode != 0
    assert "wildcard" in result.stderr.lower()


def test_plan_rejects_duplicate_or_shell_syntax_env_keys(tmp_path: Path) -> None:
    result = run_helper(
        tmp_path,
        "JAX_DB_HOST=127.0.0.1\n"
        "JAX_DB_HOST=other\n"
        "JAX_DB_GRANTEE_HOST=172.30.5.1\n"
        "JAX_RUNTIME_DB_USER=jaxweb_runtime\n"
        "JAX_MIGRATE_DB_USER=jaxmigrate_ddl\n",
        "--measured-grantee-host",
        "172.30.5.1",
    )

    assert result.returncode != 0
    assert "duplicate" in result.stderr.lower()


def test_fresh_restore_bootstraps_only_migrator_before_office_tables_exist(tmp_path: Path) -> None:
    result = run_helper(
        tmp_path,
        "JAX_DB_HOST=127.0.0.1\n"
        "JAX_DB_PORT=33994\n"
        "JAX_DB_NAME=jax_memory\n"
        "JAX_DB_USER=legacy_las_manos\n"
        "JAX_DB_PASSWORD=legacy-secret\n"
        "JAX_DB_GRANTEE_HOST=172.30.5.1\n"
        "JAX_RUNTIME_DB_USER=jaxweb_runtime\n"
        "JAX_MIGRATE_DB_USER=jaxmigrate_ddl\n",
        "--migrator-only",
        "--measured-grantee-host",
        "172.30.5.1",
    )

    assert result.returncode == 0, result.stderr
    assert "only jaxmigrate_ddl@172.30.5.1" in result.stdout
    assert "jaxweb_runtime" not in result.stdout


def test_restore_migrates_before_provisioning_office_runtime() -> None:
    source = RESTORE.read_text(encoding="utf-8")
    bootstrap = source.index('provision-db-runtime.sh" --migrator-only')
    migrate = source.index("systemctl start jax-platform-migrate.service")
    runtime = source.index('provision-db-runtime.sh" --env "$ENV_DEST"', migrate)

    assert bootstrap < migrate < runtime


def test_units_only_load_dedicated_envs_and_run_migration_before_web() -> None:
    web = (REPO / "ops/migration/systemd-units/jax-platform.service").read_text()
    migrator = (REPO / "ops/migration/systemd-units/jax-platform-migrate.service").read_text()

    assert "User=jaxweb" in web
    assert "EnvironmentFile=/etc/jax/runtime.env" in web
    assert "EnvironmentFile=/etc/jax/.env" not in web
    assert "Requires=jax-platform-migrate.service" in web
    assert "After=" in web and "jax-platform-migrate.service" in web
    assert "User=jaxmigrate" in migrator
    assert "EnvironmentFile=/etc/jax/migrate.env" in migrator
    assert "RemainAfterExit" not in migrator
    assert "/srv/jax-prod/jax-platform/backend" in web and "/srv/jax-prod/jax-platform/backend" in migrator
