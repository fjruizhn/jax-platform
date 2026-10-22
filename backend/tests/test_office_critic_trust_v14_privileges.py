"""Disposable MariaDB evidence for the future v14 critic-trust guard.

This test is intentionally a privilege measurement, not a runtime contract:
it creates a schema and account, measures metadata visibility, and destroys
both.  It grants no schema-wide or global privilege to the account.
"""
from __future__ import annotations

import os
import secrets
import uuid

import aiomysql
import pytest
from pymysql.err import OperationalError

from db_connect_config import db_connect_timeout_seconds
from base_de_test import _en_ci
from virtual_office import module_critic_trust as trust


async def _connection(*, database: str, user: str, password: str):
    return await aiomysql.connect(
        host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
        user=user, password=password, db=database, autocommit=True,
        charset="utf8mb4", connect_timeout=db_connect_timeout_seconds(),
    )


class _TestAdminUnavailable(RuntimeError):
    pass


def _assert_exact_grants(grants: tuple[str, ...], *, schema: str, principal: str) -> None:
    usage_prefix = f"GRANT USAGE ON *.* TO `{principal}`@`%`"
    expected_tables = {
        f"GRANT SELECT, TRIGGER ON `{schema}`.`{table}` TO `{principal}`@`%`"
        for table in trust._APPEND_ONLY
    }
    usage = tuple(grant for grant in grants if grant.startswith(usage_prefix))
    assert len(grants) == 3 and len(usage) == 1
    assert set(grants) - set(usage) == expected_tables
    assert all("GRANT OPTION" not in grant and "DEFAULT ROLE" not in grant for grant in grants)
    assert all("GRANT OPTION" not in grant and "DEFAULT ROLE" not in grant for grant in grants)


def test_v14_table_scoped_select_trigger_metadata_and_authority_boundary(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_critic_v14_{uuid.uuid4().hex[:16]}"
        principal = f"criticv14_{uuid.uuid4().hex[:12]}"
        password = secrets.token_urlsafe(24)
        admin = await _connection(
            database=source, user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"],
        )
        restricted = None
        principal_created = False
        cleanup_errors: list[Exception] = []
        try:
            async with admin.cursor() as cur:
                try:
                    await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                    await cur.execute(f"USE `{schema}`")
                    for statement in trust._KEY_DDL:
                        await cur.execute(statement)
                    for name, table, operation, statement in trust._trigger_definitions():
                        await cur.execute(
                            f"CREATE TRIGGER `{name}` BEFORE {operation} ON `{table}` FOR EACH ROW {statement}"
                        )
                    # PyMySQL consumes %% while binding the password; the
                    # created host is the literal % used below everywhere.
                    await cur.execute(f"CREATE USER `{principal}`@'%%' IDENTIFIED BY %s", (password,))
                    principal_created = True
                    for table in trust._APPEND_ONLY:
                        await cur.execute(f"GRANT SELECT, TRIGGER ON `{schema}`.`{table}` TO `{principal}`@'%'")
                except OperationalError as error:
                    if error.args and error.args[0] in {1044, 1045, 1142, 1227}:
                        raise _TestAdminUnavailable(str(error)) from error
                    raise
                await cur.execute(
                    "SELECT TRIGGER_NAME,ACTION_STATEMENT,DEFINER,SQL_MODE "
                    "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s ORDER BY TRIGGER_NAME",
                    (schema,),
                )
                expected_metadata = tuple(await cur.fetchall())
                assert expected_metadata and all(row[1] and row[2] is not None and row[3] is not None for row in expected_metadata)

            restricted = await _connection(database=schema, user=principal, password=password)
            async with restricted.cursor() as cur:
                await cur.execute("SELECT CURRENT_USER()")
                assert (await cur.fetchone())[0] == f"{principal}@%"
                await cur.execute("SHOW GRANTS")
                grants = tuple(row[0] for row in await cur.fetchall())
                _assert_exact_grants(grants, schema=schema, principal=principal)
                await cur.execute(
                    "SELECT TRIGGER_NAME,ACTION_STATEMENT,DEFINER,SQL_MODE "
                    "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() ORDER BY TRIGGER_NAME"
                )
                # Measured on the restricted account: exact metadata survives
                # table-scoped SELECT+TRIGGER, including DEFINER and SQL_MODE.
                assert tuple(await cur.fetchall()) == expected_metadata
                await cur.execute(
                    "CREATE TRIGGER `v14_probe_trigger` BEFORE INSERT ON `office_trusted_critic_key` "
                    "FOR EACH ROW SET NEW.principal_ref=NEW.principal_ref"
                )
                for sql in (
                    "INSERT INTO office_trusted_critic_key (key_id,principal_ref,purpose,algorithm,public_key,not_before) VALUES ('v14','agent:critic','rights.review','Ed25519',X'0000000000000000000000000000000000000000000000000000000000000000',UTC_TIMESTAMP(6))",
                    "UPDATE office_trusted_critic_key SET principal_ref='agent:other' WHERE key_id='v14'",
                    "DELETE FROM office_trusted_critic_key_revocation WHERE key_id='v14'",
                    "CREATE TABLE v14_outside_authority (id INT)",
                    "ALTER TABLE office_trusted_critic_key ADD COLUMN v14_outside_authority INT NULL",
                ):
                    with pytest.raises(OperationalError) as denied:
                        await cur.execute(sql)
                    assert denied.value.args[0] in {1044, 1142, 1227}
        finally:
            if restricted is not None:
                restricted.close()
            try:
                async with admin.cursor() as cur:
                    statements = [f"DROP DATABASE IF EXISTS `{schema}`"]
                    if principal_created:
                        statements = [
                            f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM `{principal}`@'%'",
                            f"DROP USER IF EXISTS `{principal}`@'%'",
                            *statements,
                        ]
                    for statement in statements:
                        try:
                            await cur.execute(statement)
                        except Exception as error:
                            cleanup_errors.append(error)
                    try:
                        await cur.execute("SELECT COUNT(*) FROM mysql.user WHERE User=%s", (principal,))
                        assert (await cur.fetchone())[0] == 0
                        await cur.execute(
                            "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (schema,)
                        )
                        assert (await cur.fetchone())[0] == 0
                    except Exception as error:
                        cleanup_errors.append(error)
            finally:
                admin.close()
            if cleanup_errors:
                raise AssertionError("critic-v14 disposable cleanup failed") from cleanup_errors[0]

    try:
        client.portal.call(scenario)
    except _TestAdminUnavailable as error:
        if _en_ci():
            raise RuntimeError("CI MariaDB principal lost required CREATE USER/GRANT authority") from error
        pytest.skip(f"MariaDB test principal cannot create the disposable v14 account: {error}")
