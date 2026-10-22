"""The web principal has DML only and the Office audit guard measures it."""
from __future__ import annotations

import os
import re
import uuid

import aiomysql
import pytest

from db.runtime_contract import FENCE_KEY, SCHEMA_REVISION, RuntimeDbContractError, assert_runtime_db_contract
from db.runtime_privileges import (
    MANIFEST_VERSION,
    load_runtime_table_privileges,
    runtime_table_privilege_digest,
    runtime_table_privilege_map,
)
from virtual_office.migrations import apply_office_migrations, stamp_office_runtime_revision


async def _runtime_pool(user: str, password: str, database: str | None = None):
    return await aiomysql.create_pool(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=user, password=password, db=database or os.environ["JAX_DB_NAME"], charset="utf8mb4", autocommit=True)


async def _fence(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT event_id,actor_principal,event_kind,fence_key,operation,result,reason_code FROM office_authorization_event WHERE fence_key=%s", (FENCE_KEY,))
            return await cur.fetchone()


async def _errno(pool, statement: str) -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            with pytest.raises(aiomysql.OperationalError) as error:
                await cur.execute(statement)
            return int(error.value.args[0])


async def _grant_exact_runtime_acl(cur, database: str, user: str) -> None:
    """Grant the checked-in runtime ACL to a disposable test principal."""
    await cur.execute(f"GRANT SELECT, INSERT ON `{database}`.* TO `{user}`@'%'")
    for table, privileges in runtime_table_privilege_map().items():
        await cur.execute(
            f"GRANT {', '.join(sorted(privileges))} ON `{database}`.`{table}` TO `{user}`@'%'"
        )


def test_runtime_table_privilege_manifest_is_ordered_and_excludes_protected_evidence():
    rows = load_runtime_table_privileges()
    assert MANIFEST_VERSION == "runtime-table-privileges-v1"
    assert [table for table, _ in rows] == sorted(table for table, _ in rows)
    assert runtime_table_privilege_digest() == "8a1e2dab78b05d60f38617763ba4b145bf5a2bfa5698a7f0fbc265cc20fbb25a"
    grants = runtime_table_privilege_map()
    for protected in (
        "office_authorization_event", "office_bootstrap_state",
        "office_mfa_security_event", "office_schema_revision",
    ):
        assert protected not in grants


def test_restricted_runtime_principal_cannot_escape_audit_or_schema_contract(client):
    async def scenario():
        from db.connection import get_pool
        database = os.environ["JAX_DB_NAME"]
        assert re.fullmatch(r"[A-Za-z0-9_]+", database)
        tag = uuid.uuid4().hex[:16]
        user, password, role = f"office_rt_{tag}", f"Office{uuid.uuid4().hex}", f"office_rt_role_{tag}"
        unusual_user = f"office@rt_{tag}"
        routine = f"office_rt_proc_{tag}"
        admin = await get_pool()
        runtime = None
        try:
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await apply_office_migrations(cur)
                    await cur.execute(f"CREATE USER `{user}`@'%%' IDENTIFIED BY %s", (password,))
                    await _grant_exact_runtime_acl(cur, database, user)
                    await cur.execute(f"CREATE USER `{unusual_user}`@'%%' IDENTIFIED BY %s", (password,))
                    await cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{database}`.* TO `{unusual_user}`@'%'")
            runtime = await _runtime_pool(user, password)
            before = await _fence(admin)
            await assert_runtime_db_contract(runtime)
            runtime.close(); await runtime.wait_closed(); runtime = None
            runtime = await _runtime_pool(unusual_user, password)
            with pytest.raises(RuntimeDbContractError, match="identity is unsupported"):
                await assert_runtime_db_contract(runtime)
            runtime.close(); await runtime.wait_closed(); runtime = None
            runtime = await _runtime_pool(user, password)
            assert await _errno(runtime, "TRUNCATE TABLE office_authorization_event") == 1142
            assert await _errno(runtime, "DROP TABLE office_schema_revision") == 1142
            assert await _errno(runtime, "ALTER TABLE office_schema_revision ADD COLUMN office_runtime_probe INT") == 1142
            assert await _errno(runtime, "DROP TRIGGER no_update_office_authorization_event") == 1142
            assert await _errno(runtime, "CREATE ROLE office_runtime_forbidden") == 1227
            assert await _errno(runtime, "GRANT SELECT ON *.* TO 'nobody'@'%'") == 1045
            assert await _fence(admin) == before
            runtime.close(); await runtime.wait_closed(); runtime = None
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"GRANT SELECT ON *.* TO `{user}`@'%'")
            runtime = await _runtime_pool(user, password)
            with pytest.raises(RuntimeDbContractError, match="non-DML global privilege"):
                await assert_runtime_db_contract(runtime)
            runtime.close(); await runtime.wait_closed(); runtime = None
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"REVOKE SELECT ON *.* FROM `{user}`@'%'")
                    await cur.execute(f"GRANT SELECT ON `{database}`.* TO `{user}`@'%' WITH GRANT OPTION")
            runtime = await _runtime_pool(user, password)
            with pytest.raises(RuntimeDbContractError, match="exact schema privileges"):
                await assert_runtime_db_contract(runtime)
            runtime.close(); await runtime.wait_closed(); runtime = None
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"REVOKE GRANT OPTION ON `{database}`.* FROM `{user}`@'%'")
                    await cur.execute(f"CREATE PROCEDURE `{routine}`() SELECT 1")
                    await cur.execute(f"GRANT EXECUTE ON PROCEDURE `{database}`.`{routine}` TO `{user}`@'%'")
            runtime = await _runtime_pool(user, password)
            with pytest.raises(RuntimeDbContractError, match="routine privileges"):
                await assert_runtime_db_contract(runtime)
            runtime.close(); await runtime.wait_closed(); runtime = None
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"REVOKE EXECUTE ON PROCEDURE `{database}`.`{routine}` FROM `{user}`@'%'")
                    await cur.execute(f"DROP PROCEDURE `{routine}`")
                    await cur.execute(f"GRANT DROP ON `{database}`.* TO `{user}`@'%'")
            runtime = await _runtime_pool(user, password)
            with pytest.raises(RuntimeDbContractError, match="exact schema privileges"):
                await assert_runtime_db_contract(runtime)
            runtime.close(); await runtime.wait_closed(); runtime = None
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"REVOKE DROP ON `{database}`.* FROM `{user}`@'%'")
                    await cur.execute(f"CREATE ROLE `{role}`")
                    await cur.execute(f"GRANT `{role}` TO `{user}`@'%'")
                    await cur.execute(f"SET DEFAULT ROLE `{role}` FOR `{user}`@'%'")
            runtime = await _runtime_pool(user, password)
            with pytest.raises(RuntimeDbContractError, match="applicable role"):
                await assert_runtime_db_contract(runtime)
            runtime.close(); await runtime.wait_closed(); runtime = None
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"REVOKE `{role}` FROM `{user}`@'%'")
                    await cur.execute("DROP TRIGGER no_delete_office_authorization_event")
            runtime = await _runtime_pool(user, password)
            with pytest.raises(RuntimeDbContractError, match="triggers are missing"):
                await assert_runtime_db_contract(runtime)
            runtime.close(); await runtime.wait_closed(); runtime = None
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await apply_office_migrations(cur)
                    await cur.execute("DROP TRIGGER no_delete_office_authorization_event")
                    await cur.execute("DELETE FROM office_authorization_event WHERE fence_key=%s", (FENCE_KEY,))
            runtime = await _runtime_pool(user, password)
            with pytest.raises(RuntimeDbContractError, match="fence is absent"):
                await assert_runtime_db_contract(runtime)
            runtime.close(); await runtime.wait_closed(); runtime = None
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await apply_office_migrations(cur)
                    await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
            runtime = await _runtime_pool(user, password)
            with pytest.raises(RuntimeDbContractError, match="revision is absent"):
                await assert_runtime_db_contract(runtime)
            runtime.close(); await runtime.wait_closed(); runtime = None
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    from db.migrations import run_migrations
                    await run_migrations()
            runtime = await _runtime_pool(user, password)
            await assert_runtime_db_contract(runtime)
            assert (await _fence(admin))[1:] == before[1:]
        finally:
            if runtime is not None:
                runtime.close(); await runtime.wait_closed()
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"DROP USER IF EXISTS `{user}`@'%'")
                    await cur.execute(f"DROP USER IF EXISTS `{unusual_user}`@'%'")
                    await cur.execute(f"DROP ROLE IF EXISTS `{role}`")
                    await cur.execute(f"DROP PROCEDURE IF EXISTS `{routine}`")
    client.portal.call(scenario)


def test_failed_late_platform_migration_never_leaves_office_ready(client, monkeypatch):
    async def scenario():
        from db.connection import get_pool
        from db import migrations

        async def fail_after_office(_cur):
            raise RuntimeError("forced late migration failure")

        pool = await get_pool()
        original = migrations._reclassify_provenance_mismatch
        monkeypatch.setattr(migrations, "_reclassify_provenance_mismatch", fail_after_office)
        try:
            with pytest.raises(RuntimeError, match="forced late migration failure"):
                await migrations.run_migrations()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1 FROM office_schema_revision WHERE component='virtual-office'")
                    assert await cur.fetchone() is None
        finally:
            monkeypatch.setattr(migrations, "_reclassify_provenance_mismatch", original)
            await migrations.run_migrations()

    client.portal.call(scenario)


def test_previous_ready_revision_is_denied_until_the_runner_restamps_it(client):
    """A v1 marker cannot certify schema that predates the audit-shape CHECK."""
    async def scenario():
        from db.connection import get_pool
        from db.migrations import run_migrations
        from db.runtime_contract import _revision

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute(
                    "UPDATE office_schema_revision SET revision='office-runtime-v1' "
                    "WHERE component='virtual-office'"
                )
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                with pytest.raises(RuntimeDbContractError, match="revision is absent"):
                    await _revision(cur)
        await run_migrations()
        assert SCHEMA_REVISION != 'office-runtime-v1'
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await _revision(cur)
    client.portal.call(scenario)


def test_runtime_mfa_canary_rejects_a_trigger_that_only_protects_the_fence(client):
    """A pristine ledger still rejects a trigger which only protects its fence."""
    async def scenario():
        from db.connection import get_pool

        database = os.environ["JAX_DB_NAME"]
        tag = uuid.uuid4().hex[:16]
        user, password = f"office_mfa_rt_{tag}", f"Office{uuid.uuid4().hex}"
        schema = f"office_mfa_pristine_{tag}"
        admin = await aiomysql.connect(
            host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
            user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"],
            db=database, charset="utf8mb4", autocommit=True,
        )
        runtime = None
        try:
            async with admin.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}`")
                for table in ("jax_users", "jax_tenants", "projects", "jax_project_scope", "user_admin_audit"):
                    await cur.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{database}`.`{table}`")
                await cur.execute(f"INSERT INTO `{schema}`.`jax_users` SELECT * FROM `{database}`.`jax_users`")
                await cur.execute(f"USE `{schema}`")
                await apply_office_migrations(cur)
                await stamp_office_runtime_revision(cur)
                await cur.execute("SELECT COUNT(*) FROM office_mfa_security_event WHERE event_kind='MFA_PROOF'")
                assert (await cur.fetchone())[0] == 0
                await cur.execute(f"CREATE USER `{user}`@'%%' IDENTIFIED BY %s", (password,))
                await _grant_exact_runtime_acl(cur, schema, user)
                await cur.execute("DROP TRIGGER no_update_office_mfa_security_event")
                await cur.execute(
                    "CREATE TRIGGER no_update_office_mfa_security_event BEFORE UPDATE "
                    "ON office_mfa_security_event FOR EACH ROW "
                    "BEGIN IF OLD.fence_key IS NOT NULL THEN "
                    "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_mfa_security_event append-only'; "
                    "END IF; END"
                )
            runtime = await _runtime_pool(user, password, schema)
            with pytest.raises(RuntimeDbContractError, match="trigger permitted a mutation"):
                await assert_runtime_db_contract(runtime)
            async with admin.cursor() as cur:
                await cur.execute(f"USE `{schema}`")
                await cur.execute("SELECT COUNT(*) FROM office_mfa_security_event WHERE event_kind='MFA_PROOF'")
                assert (await cur.fetchone())[0] == 0
        finally:
            if runtime is not None:
                runtime.close(); await runtime.wait_closed()
            async with admin.cursor() as cur:
                await cur.execute(f"DROP USER IF EXISTS `{user}`@'%'")
                await cur.execute(f"USE `{database}`")
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            admin.close()
    client.portal.call(scenario)


def test_runtime_bootstrap_probe_rejects_redacted_same_name_noop_triggers(client):
    """A DML principal cannot inspect trigger text, so it must exercise it."""
    async def scenario():
        from db.connection import get_pool
        database = os.environ["JAX_DB_NAME"]
        tag = uuid.uuid4().hex[:16]
        user, password = f"office_bootstrap_rt_{tag}", f"Office{uuid.uuid4().hex}"
        admin = await get_pool()
        runtime = None
        try:
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await apply_office_migrations(cur)
                    await cur.execute(f"CREATE USER `{user}`@'%%' IDENTIFIED BY %s", (password,))
                    await _grant_exact_runtime_acl(cur, database, user)
                    await cur.execute("DROP TRIGGER no_update_office_bootstrap_state")
                    await cur.execute("DROP TRIGGER no_delete_office_bootstrap_state")
                    await cur.execute(
                        "CREATE TRIGGER no_update_office_bootstrap_state BEFORE UPDATE "
                        "ON office_bootstrap_state FOR EACH ROW SET NEW.completed_at=OLD.completed_at"
                    )
                    await cur.execute(
                        "CREATE TRIGGER no_delete_office_bootstrap_state BEFORE DELETE "
                        "ON office_bootstrap_state FOR EACH ROW SET @office_bootstrap_noop=1"
                    )
            runtime = await _runtime_pool(user, password)
            async with runtime.acquire() as runtime_conn:
                async with runtime_conn.cursor() as runtime_cur:
                    await runtime_cur.execute(
                        "SELECT ACTION_STATEMENT FROM information_schema.TRIGGERS "
                        "WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME IN "
                        "('no_update_office_bootstrap_state','no_delete_office_bootstrap_state') "
                        "ORDER BY TRIGGER_NAME"
                    )
                    assert await runtime_cur.fetchall() == ((None,), (None,))
            with pytest.raises(RuntimeDbContractError, match="bootstrap trigger permitted a mutation"):
                await assert_runtime_db_contract(runtime)
        finally:
            if runtime is not None:
                runtime.close(); await runtime.wait_closed()
            async with admin.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("DROP TRIGGER IF EXISTS no_update_office_bootstrap_state")
                    await cur.execute("DROP TRIGGER IF EXISTS no_delete_office_bootstrap_state")
                    await apply_office_migrations(cur)
                    await cur.execute(f"DROP USER IF EXISTS `{user}`@'%'")
    client.portal.call(scenario)


def test_bootstrap_probe_rolls_back_its_tenant_fixture_on_an_empty_tenant_schema(client):
    async def scenario():
        from db.runtime_contract import _bootstrap_append_only_probe
        from db.connection import get_pool

        database = os.environ["JAX_DB_NAME"]
        tag = uuid.uuid4().hex[:16]
        schema, user, password = f"office_bootstrap_empty_{tag}", f"office_bootstrap_empty_{tag}", f"Office{uuid.uuid4().hex}"
        admin = await aiomysql.connect(
            host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
            user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"],
            db=database, charset="utf8mb4", autocommit=True,
        )
        runtime = None
        try:
            async with admin.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}`")
                for table in ("jax_users", "jax_tenants", "projects", "jax_project_scope", "user_admin_audit"):
                    await cur.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{database}`.`{table}`")
                await cur.execute(f"USE `{schema}`")
                await apply_office_migrations(cur)
                await cur.execute(f"CREATE USER `{user}`@'%%' IDENTIFIED BY %s", (password,))
                await _grant_exact_runtime_acl(cur, schema, user)
            runtime = await _runtime_pool(user, password, schema)
            await _bootstrap_append_only_probe(runtime)
            async with admin.cursor() as cur:
                await cur.execute(f"USE `{schema}`")
                for table in ("jax_tenants", "jax_users", "user_admin_audit", "office_mfa_challenge", "office_mfa_security_event", "office_bootstrap_state"):
                    await cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                    # MFA migration fence is the only durable ledger row.
                    assert (await cur.fetchone())[0] == (1 if table == "office_mfa_security_event" else 0)
        finally:
            if runtime is not None:
                runtime.close(); await runtime.wait_closed()
            async with admin.cursor() as cur:
                await cur.execute(f"DROP USER IF EXISTS `{user}`@'%'")
                await cur.execute(f"USE `{database}`")
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            admin.close()
    client.portal.call(scenario)
