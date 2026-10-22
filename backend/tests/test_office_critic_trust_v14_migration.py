"""MariaDB 12.3 evidence for the inert, explicit critic-trust v14 upgrade."""
from __future__ import annotations

import os
import uuid
import json
import stat
import asyncio
from dataclasses import replace
from pathlib import Path

import aiomysql
import pytest

from db_connect_config import db_connect_timeout_seconds
from virtual_office import module_critic_trust as trust
from virtual_office import critic_trust_activation as activation
from virtual_office.migrations import RUNTIME_REVISION


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
    await cur.execute(
        "SELECT DEFAULT_CHARACTER_SET_NAME,DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s",
        (schema,),
    )
    charset, collation = await cur.fetchone()
    value = {
        "database": {
            "charset": charset, "collation": collation,
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


async def _revision(cur, component: str) -> str | None:
    await cur.execute("SELECT revision FROM office_schema_revision WHERE component=%s", (component,))
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
                await cur.execute("INSERT INTO office_schema_revision VALUES ('virtual-office',%s,NOW(6))", (RUNTIME_REVISION,))
                await cur.execute(
                    "INSERT INTO office_trusted_critic_key "
                    "(key_id,principal_ref,purpose,algorithm,public_key,not_before) "
                    "VALUES ('key-preserved','agent:critic','rights.review','Ed25519',%s,UTC_TIMESTAMP(6))",
                    (bytes.fromhex("42" * 32),),
                )
                await cur.execute(
                    "INSERT INTO office_trusted_critic_key_revocation (key_id,revoked_at) "
                    "VALUES ('key-preserved',UTC_TIMESTAMP(6))"
                )
                await cur.execute("CREATE USER IF NOT EXISTS `critic_v14_owner`@'localhost' IDENTIFIED BY 'unused-test-only'")
                await cur.execute(f"GRANT TRIGGER ON `{schema}`.* TO `critic_v14_owner`@'localhost'")
                owner_created = True
                contract = await _contract(cur, schema, contract_path)
                async def dedicated():
                    return await aiomysql.connect(
                        host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
                        user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=schema,
                        autocommit=True, charset=contract.database_charset, connect_timeout=db_connect_timeout_seconds(),
                    )
                # A login-capable definer has no authority to alter the
                # registry contract, and preflight leaves its component marker absent.
                with pytest.raises(RuntimeError, match="account-locked"):
                    await trust.migrate_critic_trust_v14(contract, connect=dedicated)
                assert await _revision(cur, "virtual-office-critic-trust") is None
                await cur.execute("ALTER USER `critic_v14_owner`@'localhost' ACCOUNT LOCK")

                # The dedicated connection must reject an authorized profile
                # whose target identity/version does not match its actual DB,
                # before it can create a critic marker or execute DDL.
                with pytest.raises(RuntimeError, match="database identity"):
                    await trust.migrate_critic_trust_v14(
                        replace(contract, database_schema="other_schema"), connect=dedicated,
                    )
                with pytest.raises(RuntimeError, match="database identity"):
                    await trust.migrate_critic_trust_v14(
                        replace(contract, database_server_version="0.0.0"), connect=dedicated,
                    )
                assert await _revision(cur, "virtual-office-critic-trust") is None
                await cur.execute(
                    "INSERT INTO office_schema_revision VALUES (%s,%s,NOW(6))",
                    ("virtual-office-critic-trust", "critic-v14-ready-future-marker"),
                )
                with pytest.raises(RuntimeError, match="unknown"):
                    await trust.migrate_critic_trust_v14(contract, connect=dedicated)
                assert await _revision(cur, "virtual-office-critic-trust") == "critic-v14-ready-future-marker"
                await cur.execute("DELETE FROM office_schema_revision WHERE component=%s", ("virtual-office-critic-trust",))
                # Resume from a durable invalid marker without touching the
                # global v13 readiness state.
                await cur.execute(
                    "INSERT INTO office_schema_revision VALUES (%s,%s,NOW(6))",
                    ("virtual-office-critic-trust", trust._v14_descriptor(contract, "invalid")),
                )
                await trust.migrate_critic_trust_v14(contract, connect=dedicated)
                assert await _revision(cur, "virtual-office") == RUNTIME_REVISION
                assert await _revision(cur, "virtual-office-critic-trust") == trust._v14_descriptor(contract, "ready")
                await cur.execute("SELECT key_id,public_key FROM office_trusted_critic_key")
                assert await cur.fetchone() == ("key-preserved", bytes.fromhex("42" * 32))
                await trust._assert_critic_trust_anchor_metadata(
                    cur, expected_columns=trust._TABLE_COLUMNS, expected_clauses=trust._CHECK_CLAUSES,
                    v14_contract=contract,
                )
                # Exercise every atomic replacement while four independent
                # append-only DML statements run at deterministic barriers.
                # The test fails if any replacement exposes a mutable table.
                barriers = [asyncio.Event() for _ in trust._trigger_definitions()]
                replaced = asyncio.Queue()
                original_replace = trust._replace_v14_triggers

                async def interleaved_replace(cursor, active_contract):
                    definer = active_contract.trigger_definer
                    for index, (name, table, operation, statement) in enumerate(trust._trigger_definitions()):
                        await cursor.execute(
                            f"CREATE OR REPLACE DEFINER=`{definer.user}`@`{definer.host}` TRIGGER `{name}` "
                            f"BEFORE {operation} ON `{table}` FOR EACH ROW {statement}"
                        )
                        await replaced.put(index)
                        await barriers[index].wait()

                await cur.execute(
                    "UPDATE office_schema_revision SET revision=%s WHERE component=%s",
                    (trust._v14_descriptor(contract, "invalid"), "virtual-office-critic-trust"),
                )
                monkeypatch.setattr(trust, "_replace_v14_triggers", interleaved_replace)
                migrating = asyncio.create_task(trust.migrate_critic_trust_v14(contract, connect=dedicated))
                for index in range(4):
                    assert await replaced.get() == index
                    for statement in (
                        "UPDATE office_trusted_critic_key SET purpose=purpose WHERE key_id='key-preserved'",
                        "DELETE FROM office_trusted_critic_key WHERE key_id='key-preserved'",
                        "UPDATE office_trusted_critic_key_revocation SET revoked_at=revoked_at WHERE key_id='key-preserved'",
                        "DELETE FROM office_trusted_critic_key_revocation WHERE key_id='key-preserved'",
                    ):
                        with pytest.raises(aiomysql.OperationalError, match="append-only") as blocked:
                            await cur.execute(statement)
                        assert blocked.value.args[0] == 1644
                    barriers[index].set()
                await migrating
                monkeypatch.setattr(trust, "_replace_v14_triggers", original_replace)
                await trust._assert_critic_trust_anchor_metadata(
                    cur, expected_columns=trust._TABLE_COLUMNS, expected_clauses=trust._CHECK_CLAUSES,
                    v14_contract=contract,
                )
                await cur.execute("SELECT key_id FROM office_trusted_critic_key ORDER BY key_id")
                assert await cur.fetchall() == (("key-preserved",),)
                await cur.execute("SELECT key_id FROM office_trusted_critic_key_revocation ORDER BY key_id")
                assert await cur.fetchall() == (("key-preserved",),)

                # Two independent dedicated connections serialize on the
                # schema-derived advisory lock; neither can publish a partial
                # trigger set or alter the global v13 marker.
                await cur.execute(
                    "UPDATE office_schema_revision SET revision=%s WHERE component=%s",
                    (trust._v14_descriptor(contract, "invalid"), "virtual-office-critic-trust"),
                )
                await asyncio.gather(
                    trust.migrate_critic_trust_v14(contract, connect=dedicated),
                    trust.migrate_critic_trust_v14(contract, connect=dedicated),
                )
                assert await _revision(cur, "virtual-office") == RUNTIME_REVISION
                assert await _revision(cur, "virtual-office-critic-trust") == trust._v14_descriptor(contract, "ready")
                # A post-invalidation fault is durable under the dedicated
                # autocommit connection; old append-only triggers still
                # protect stored keys until the exact same profile retries.
                original_replace = trust._replace_v14_triggers

                async def inject_failure(_cursor, _contract):
                    raise RuntimeError("injected-v14-ddl-fault")

                await cur.execute(
                    "UPDATE office_schema_revision SET revision=%s WHERE component=%s",
                    (trust._v14_descriptor(contract, "invalid"), "virtual-office-critic-trust"),
                )
                monkeypatch.setattr(trust, "_replace_v14_triggers", inject_failure)
                with pytest.raises(RuntimeError, match="injected-v14-ddl-fault"):
                    await trust.migrate_critic_trust_v14(contract, connect=dedicated)
                assert await _revision(cur, "virtual-office-critic-trust") == trust._v14_descriptor(contract, "invalid")
                with pytest.raises(aiomysql.OperationalError, match="append-only"):
                    await cur.execute("UPDATE office_trusted_critic_key SET purpose='rights.review' WHERE key_id='key-preserved'")
                monkeypatch.setattr(trust, "_replace_v14_triggers", original_replace)
                await trust.migrate_critic_trust_v14(contract, connect=dedicated)
                # Corrupt the environment-owned values.  A retry recreates,
                # rather than accepting, wrong DEFINER and SQL_MODE metadata.
                await cur.execute("SET SESSION sql_mode='ANSI'")
                statement = next(
                    body for name, _table, _operation, body in trust._trigger_definitions()
                    if name == "no_delete_office_trusted_critic_key"
                )
                await cur.execute(
                    "CREATE OR REPLACE DEFINER=`critic_v14_owner`@`localhost` "
                    "TRIGGER no_delete_office_trusted_critic_key BEFORE DELETE "
                    "ON office_trusted_critic_key FOR EACH ROW " + statement
                )
                # CHECK_CLAUSE serialization follows the *reader's* session
                # SQL mode in MariaDB.  Restore the contract mode before the
                # v14 predecessor attestation, while the trigger retains its
                # deliberately wrong creation SQL_MODE for this regression.
                await cur.execute("SET SESSION sql_mode=%s", (contract.database_sql_mode,))
                # A ready marker with an environment drift must fail closed;
                # it never silently repairs a schema already called ready.
                with pytest.raises(RuntimeError, match="triggers are invalid"):
                    await trust.migrate_critic_trust_v14(contract, connect=dedicated)
                assert await _revision(cur, "virtual-office-critic-trust") == trust._v14_descriptor(contract, "invalid")
                # Exact same descriptor suffix encodes the contract identity;
                # only the durable lifecycle prefix changes.
                ready = trust._v14_descriptor(contract, "ready")
                invalid = trust._v14_descriptor(contract, "invalid")
                assert ready.removeprefix("critic-v14-ready-") == invalid.removeprefix("critic-v14-invalid-")
                await trust.migrate_critic_trust_v14(contract, connect=dedicated)
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
