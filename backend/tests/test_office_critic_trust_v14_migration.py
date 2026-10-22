"""MariaDB 12.3 evidence for the inert, explicit critic-trust v14 upgrade."""
from __future__ import annotations

import os
import uuid
import json
import stat
from pathlib import Path

import aiomysql
import pytest

from db_connect_config import db_connect_timeout_seconds
from virtual_office import module_critic_trust as trust
from virtual_office import critic_trust_activation as activation
from virtual_office.migrations import CRITIC_TRUST_V14_RUNTIME_REVISION, INVALIDATED_RUNTIME_REVISION


def _root_owned_contract_path(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """CI injects an actual descriptor-validated contract, not a bypass object."""
    original_lstat, original_fstat = os.lstat, os.fstat

    def root_stat(info: os.stat_result, *, directory: bool) -> os.stat_result:
        mode = (stat.S_IFDIR if directory else stat.S_IFREG) | (0o755 if directory else 0o644)
        return os.stat_result((mode, info.st_ino, info.st_dev, info.st_nlink, 0, 0, info.st_size, info.st_atime, info.st_mtime, info.st_ctime))

    def lstat(name: os.PathLike[str] | str) -> os.stat_result:
        observed = original_lstat(name)
        rendered = Path(name)
        if rendered == path:
            return root_stat(observed, directory=False)
        if rendered in path.parents:
            return root_stat(observed, directory=True)
        return observed

    monkeypatch.setattr(activation.os, "lstat", lstat)
    monkeypatch.setattr(activation.os, "fstat", lambda fd: root_stat(original_fstat(fd), directory=False))


async def _contract(cur, schema: str, path: Path):
    await cur.execute("SELECT VERSION(),@@SESSION.sql_mode")
    version, sql_mode = await cur.fetchone()
    value = {
        "database": {
            "charset": "utf8mb4", "collation": "utf8mb4_uca1400_ai_ci",
            "guard_account": {"host": "localhost", "user": "critic_v14_guard"},
            "schema": schema, "server_version": str(version).split("-", 1)[0], "sql_mode": sql_mode,
            "trigger_definer": {"host": "localhost", "user": "critic_v14_owner"},
        },
        "domain": activation.DOMAIN, "guard_protocol": activation.GUARD_PROTOCOL,
        "limits": {"max_frame_bytes": activation.MAX_FRAME_BYTES, "max_inflight": 8, "timeout_seconds": 3},
        "office_runtime_generation": activation.OFFICE_RUNTIME_GENERATION,
        "process": {"allowed_client_uids": [1001], "gid": 1001, "socket_path": activation.SOCKET_PATH, "uid": 1001},
        "receipt": {"algorithm": activation.RECEIPT_ALGORITHM, "critic_principal": "agent:critic", "domain": activation.RECEIPT_DOMAIN, "operation": activation.RECEIPT_OPERATION, "schema_version": activation.RECEIPT_SCHEMA_VERSION},
        "schema_version": activation.SCHEMA_VERSION,
    }
    path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    return activation.load_activation_contract(path)


async def _revision(cur) -> str | None:
    await cur.execute("SELECT revision FROM office_schema_revision WHERE component='virtual-office'")
    row = await cur.fetchone()
    return None if row is None else row[0]


def test_v14_recreates_contract_bound_triggers_preserves_rows_and_retries(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_critic_v14_migration_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(
            host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
            user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source,
            autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds(),
        )
        owner_created = False
        contract_path = tmp_path / "office-critic-trust-activation-v1.json"
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                for statement in trust._KEY_DDL:
                    await cur.execute(statement)
                for name, table, operation, statement in trust._trigger_definitions():
                    await cur.execute(
                        f"CREATE TRIGGER `{name}` BEFORE {operation} ON `{table}` FOR EACH ROW {statement}"
                    )
                await cur.execute(
                    "CREATE TABLE office_schema_revision (component VARCHAR(64) NOT NULL PRIMARY KEY,"
                    "revision VARCHAR(64) NOT NULL,applied_at DATETIME(6) NOT NULL) ENGINE=InnoDB"
                )
                await cur.execute(
                    "INSERT INTO office_schema_revision VALUES ('virtual-office','office-runtime-v13-test',NOW(6))"
                )
                await cur.execute(
                    "INSERT INTO office_trusted_critic_key "
                    "(key_id,principal_ref,purpose,algorithm,public_key,not_before) "
                    "VALUES ('key-preserved','agent:critic','rights.review','Ed25519',%s,UTC_TIMESTAMP(6))",
                    (bytes.fromhex("42" * 32),),
                )
                await cur.execute("CREATE USER IF NOT EXISTS `critic_v14_owner`@'localhost' IDENTIFIED BY 'unused-test-only'")
                owner_created = True
                contract = await _contract(cur, schema, contract_path)
                # Simulate a crash after invalidation and trigger loss.  The
                # retry is required to repair the exact four-trigger set.
                await cur.execute("UPDATE office_schema_revision SET revision=%s", (INVALIDATED_RUNTIME_REVISION,))
                await cur.execute("DROP TRIGGER no_update_office_trusted_critic_key")
                await trust.apply_critic_trust_v14(cur, contract)
                assert await _revision(cur) == CRITIC_TRUST_V14_RUNTIME_REVISION
                await cur.execute("SELECT key_id,public_key FROM office_trusted_critic_key")
                assert await cur.fetchone() == ("key-preserved", bytes.fromhex("42" * 32))
                await trust._assert_critic_trust_anchor_metadata(
                    cur, expected_columns=trust._TABLE_COLUMNS, expected_clauses=trust._CHECK_CLAUSES,
                    v14_contract=contract,
                )
                # Corrupt the environment-owned values.  A retry recreates,
                # rather than accepting, wrong DEFINER and SQL_MODE metadata.
                await cur.execute("DROP TRIGGER no_delete_office_trusted_critic_key")
                await cur.execute("SET SESSION sql_mode='ANSI'")
                await cur.execute(
                    "CREATE TRIGGER no_delete_office_trusted_critic_key BEFORE DELETE "
                    "ON office_trusted_critic_key FOR EACH ROW SIGNAL SQLSTATE '45000' "
                    "SET MESSAGE_TEXT='office_trusted_critic_key append-only'"
                )
                await trust.apply_critic_trust_v14(cur, contract)
                await trust._assert_critic_trust_anchor_metadata(
                    cur, expected_columns=trust._TABLE_COLUMNS, expected_clauses=trust._CHECK_CLAUSES,
                    v14_contract=contract,
                )
        finally:
            async with conn.cursor() as cur:
                if owner_created:
                    await cur.execute("DROP USER IF EXISTS `critic_v14_owner`@'localhost'")
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    _root_owned_contract_path(monkeypatch, tmp_path / "office-critic-trust-activation-v1.json")
    client.portal.call(scenario)
