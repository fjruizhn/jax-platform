"""Create the disposable MariaDB baseline used by the F2-E-SR load run.

This is deliberately a bootstrapper, not a test database convenience wrapper:
it refuses an occupied port, an existing container, an existing volume, or an
existing credential file.  Its only MariaDB is a named, loopback-only Docker
container.  It never reads ``/etc/jax/.env`` and never names the production
database.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import socket
import stat
import subprocess
import time
from pathlib import Path


CONTAINER = "axioma-f2esr-load-20261003"
VOLUME = f"{CONTAINER}-data"
PORT = 13338
DATABASE = "jax_memory_test_f2esr"
IMAGE_ID = "ab1c3dd38194"
ENV_FILE = Path("/tmp/axioma-f2esr-load-20261003-db.env")
HERE = Path(__file__).resolve()
PLATFORM_ROOT = HERE.parents[1]
DEFAULT_JAX_ROOT = Path("/home/fruiz/worktrees/load-f2esr-jax")
PYTHON = Path("/home/fruiz/jax-platform/backend/.venv/bin/python")


class BootstrapRefused(RuntimeError):
    """The isolated resource boundary was not pristine."""


def _run(args: list[str], *, input_text: str | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, input=input_text, text=True, capture_output=True, env=env, check=False)


def _sudo_docker(*args: str, input_text: str | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = _run(["sudo", "-n", "docker", *args], input_text=input_text, env=env)
    if result.returncode:
        raise RuntimeError(f"docker {' '.join(args[:2])} failed: {result.stderr.strip()[:500]}")
    return result


def _port_is_free(port: int) -> bool:
    for family, address in ((socket.AF_INET, ("127.0.0.1", port)), (socket.AF_INET6, ("::1", port))):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(address)
        except OSError:
            return False
    return True


def _docker_exists(kind: str, name: str) -> bool:
    result = _run(["sudo", "-n", "docker", kind, "inspect", name])
    return result.returncode == 0


def assert_pristine(*, env_file: Path = ENV_FILE) -> None:
    """Fail before changing anything when a prior run could be present."""
    if not _port_is_free(PORT):
        raise BootstrapRefused(f"loopback port {PORT} is already occupied")
    if _docker_exists("container", CONTAINER):
        raise BootstrapRefused(f"container {CONTAINER} already exists")
    if _docker_exists("volume", VOLUME):
        raise BootstrapRefused(f"volume {VOLUME} already exists")
    if env_file.exists():
        raise BootstrapRefused(f"credential file already exists: {env_file}")
    image = _run(["sudo", "-n", "docker", "image", "inspect", IMAGE_ID, "--format", "{{.Id}}"])
    if image.returncode or IMAGE_ID not in image.stdout:
        raise BootstrapRefused(f"required MariaDB 12.3.3 image {IMAGE_ID} is unavailable")


def _write_fake_env(path: Path) -> dict[str, str]:
    values = {
        "JAX_DB_HOST": "127.0.0.1",
        "JAX_DB_PORT": str(PORT),
        "JAX_DB_USER": "f2esr_load",
        "JAX_DB_PASSWORD": secrets.token_urlsafe(36),
        "JAX_DB_NAME": DATABASE,
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError("isolated credential file did not retain mode 0600")
    return values


def _jax_schema_sql(jax_root: Path) -> str:
    source = (jax_root.resolve() / "jax_memory_schema.sql").resolve()
    if not source.is_file() or source.parent != jax_root.resolve():
        raise BootstrapRefused("JAX schema source is missing or escaped the exact JAX checkout")
    sql = source.read_text(encoding="utf-8")
    sql, replaced = re.subn(
        r"(?ms)^CREATE DATABASE IF NOT EXISTS jax_memory\s+CHARACTER SET.*?;\s*^USE jax_memory;\s*",
        "", sql, count=1,
    )
    if replaced != 1:
        raise BootstrapRefused("JAX schema header changed; bootstrap must be reviewed")
    return sql


def _wait_for_mariadb(password: str) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        result = _run(["sudo", "-n", "docker", "exec", "-e", f"MYSQL_PWD={password}", CONTAINER,
                       "mariadb-admin", "--protocol=TCP", "-h127.0.0.1", "-uf2esr_load", "ping"])
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("isolated MariaDB did not become ready within 45 seconds")


def _import_jax_schema(sql: str, password: str) -> None:
    env = {"MYSQL_PWD": password}
    _sudo_docker("exec", "-i", "-e", f"MYSQL_PWD={password}", CONTAINER,
                 "mariadb", "--protocol=TCP", "-h127.0.0.1", "-uf2esr_load", DATABASE,
                 input_text=sql, env=env)


def _bootstrap_platform(*, jax_root: Path, db: dict[str, str]) -> None:
    """Run the owned migration chain, including the test-only B9 remainder.

    The temporary database deliberately uses the established test-name family,
    which allows ``aplicar_migraciones_b9_restantes`` to retain its own
    fail-closed guard instead of duplicating its migration policy here.
    """
    if not PYTHON.is_file():
        raise BootstrapRefused(f"required platform Python is unavailable: {PYTHON}")
    backend = PLATFORM_ROOT / "backend"
    code = """
import asyncio, os, sys
sys.path.insert(0, os.environ['JAX_REPO_PATH'])
sys.path.insert(0, os.environ['F2ESR_BACKEND'])
from db.connection import close_pool
from db.migrations import run_migrations
from db.seed import run_seed
from base_de_test import aplicar_migraciones_b9_restantes
from jacobs import store as jacobs_store

async def main():
    try:
        await run_migrations()
        await run_seed()
        await jacobs_store.init_tables()
        await aplicar_migraciones_b9_restantes()
    finally:
        await close_pool()
        await jacobs_store.cerrar_pool()

asyncio.run(main())
"""
    env = {
        "PATH": os.environ.get("PATH", ""), "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONPATH": str(backend), "F2ESR_BACKEND": str(backend),
        "JAX_REPO_PATH": str(jax_root.resolve()), "JAX_CONFIG_PATH": str((jax_root / "config" / "config.toml").resolve()),
        "JAX_DB_CONNECT_TIMEOUT_SECONDS": "5", "JAX_OLLAMA_CPU_URL": "http://127.0.0.1:17434",
        "JAX_TRUSTED_PROXIES": "127.0.0.1", "JAX_SEED_SUPERADMIN_EMAIL": "f2esr-admin@example.invalid",
        "JAX_SEED_TENANT_NAME": "F2-E-SR isolated load tenant",
        "JAX_SEED_ADMIN_PASSWORD": "load-only-not-production",
        **db,
    }
    result = _run([str(PYTHON), "-c", code], env=env)
    if result.returncode:
        raise RuntimeError(f"platform/JAX migration chain failed: {result.stderr.strip()[-1200:]}")


def _verify_baseline(db: dict[str, str]) -> None:
    query = """SELECT
      (SELECT COUNT(*) FROM jax_tenants) AS tenants,
      (SELECT COUNT(*) FROM facet WHERE `key`='jax_local') AS local_facet,
      (SELECT COUNT(*) FROM provider WHERE id='ollama') AS ollama_provider,
      (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='jacobs_pipelines') AS jacobs_table,
      (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='governed_output_outbox') AS lifecycle_table,
      (SELECT COUNT(*) FROM facet_binding WHERE facet_key='jax_local' AND provider_id='ollama') AS local_ollama_binding;
"""
    result = _run(["sudo", "-n", "docker", "exec", "-e", f"MYSQL_PWD={db['JAX_DB_PASSWORD']}", CONTAINER,
                   "mariadb", "--protocol=TCP", "-N", "-h127.0.0.1", "-u", db["JAX_DB_USER"], db["JAX_DB_NAME"], "-e", query])
    if result.returncode:
        raise RuntimeError(f"baseline verification failed: {result.stderr.strip()[:500]}")
    values = tuple(int(item) for item in result.stdout.strip().split("\t"))
    if len(values) != 6 or any(value < 1 for value in values):
        raise RuntimeError(f"baseline is incomplete (tenant/facet/provider/Jacobs/lifecycle/binding): {values!r}")


def start(*, jax_root: Path = DEFAULT_JAX_ROOT, env_file: Path = ENV_FILE) -> None:
    assert_pristine(env_file=env_file)
    db = _write_fake_env(env_file)
    try:
        _sudo_docker("volume", "create", VOLUME)
        _sudo_docker(
            "run", "-d", "--name", CONTAINER, "--publish", f"127.0.0.1:{PORT}:3306",
            "--mount", f"type=volume,source={VOLUME},target=/var/lib/mysql",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
            "--env", f"MARIADB_DATABASE={DATABASE}", "--env", "MARIADB_USER=f2esr_load",
            "--env", f"MARIADB_PASSWORD={db['JAX_DB_PASSWORD']}",
            "--env", f"MARIADB_ROOT_PASSWORD={secrets.token_urlsafe(36)}", IMAGE_ID,
            "--innodb-buffer-pool-size=128M", "--max-connections=151",
        )
        _wait_for_mariadb(db["JAX_DB_PASSWORD"])
        _import_jax_schema(_jax_schema_sql(jax_root), db["JAX_DB_PASSWORD"])
        _bootstrap_platform(jax_root=jax_root, db=db)
        _verify_baseline(db)
    except BaseException:
        # Do not attempt a broad cleanup: a named resource is retained as
        # evidence for diagnosis and blocks accidental reuse until inspected.
        raise
    print(f"F2-E-SR isolated DB ready: container={CONTAINER} port={PORT} env={env_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="create and bootstrap the isolated database")
    parser.add_argument("--jax-root", type=Path, default=DEFAULT_JAX_ROOT)
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    args = parser.parse_args()
    if not args.start:
        parser.error("--start is required; this tool has no implicit destructive mode")
    start(jax_root=args.jax_root, env_file=args.env_file)


if __name__ == "__main__":
    main()
