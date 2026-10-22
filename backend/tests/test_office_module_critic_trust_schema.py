"""The critic trust anchor is inert evidence plumbing, never release authority."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import aiomysql
import pytest
from pymysql.err import OperationalError

from db_connect_config import db_connect_timeout_seconds
from virtual_office import module_critic_trust as trust


def test_trust_anchor_has_no_writer_or_release_surface() -> None:
    source = Path(trust.__file__).read_text(encoding="utf-8")
    for forbidden in ("INSERT INTO office_trusted", "UPDATE office_trusted", "release.publish", "PUBLISHED"):
        assert forbidden not in source


def test_mariadb_trust_anchor_is_append_only_and_attested(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_critic_trust_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(
            host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
            user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source,
            autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds(),
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await trust.apply_critic_trust_anchor_migrations(cur)
                await trust.apply_critic_trust_anchor_migrations(cur)
                await cur.execute(
                    "INSERT INTO office_trusted_critic_key "
                    "(key_id,principal_ref,purpose,algorithm,public_key,not_before,not_after) "
                    "VALUES ('critic-1','agent:critic','rights.review','Ed25519',%s,"
                    "'2026-10-22 11:00:00.000000','2026-10-22 13:00:00.000000')",
                    (bytes.fromhex("11" * 32),),
                )
                await cur.execute(
                    "INSERT INTO office_trusted_critic_key "
                    "(key_id,principal_ref,purpose,algorithm,public_key,not_before,not_after) "
                    "VALUES ('critic-no-expiry','agent:critic','rights.review','Ed25519',%s,"
                    "'2026-10-22 11:00:00.000000',NULL)",
                    (bytes.fromhex("22" * 32),),
                )
                await cur.execute(
                    "INSERT INTO office_trusted_critic_key_revocation (key_id,revoked_at) "
                    "VALUES ('critic-1','2026-10-22 12:30:00.000000')"
                )
                resolved = await trust.resolve_trusted_critic_key(cur, "critic-1")
                assert resolved is not None
                assert resolved.principal_ref == "agent:critic"
                assert resolved.revoked_at is not None and resolved.revoked_at.tzinfo is not None
                nonexpiring = await trust.resolve_trusted_critic_key(cur, "critic-no-expiry")
                assert nonexpiring is not None and nonexpiring.not_after is None
                assert await trust.resolve_trusted_critic_key(cur, "missing") is None
                with pytest.raises(OperationalError, match="append-only"):
                    await cur.execute("UPDATE office_trusted_critic_key SET purpose='release.publish' WHERE key_id='critic-1'")
                with pytest.raises(OperationalError, match="append-only"):
                    await cur.execute("DELETE FROM office_trusted_critic_key_revocation WHERE key_id='critic-1'")
                await trust.assert_critic_trust_anchor_metadata(cur)
                await cur.execute("DROP TRIGGER no_update_office_trusted_critic_key")
                await cur.execute("CREATE TRIGGER no_update_office_trusted_critic_key BEFORE UPDATE ON office_trusted_critic_key FOR EACH ROW SET NEW.purpose=OLD.purpose")
                with pytest.raises(RuntimeError, match="triggers are invalid"):
                    await trust.assert_critic_trust_anchor_metadata(cur)
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)


@pytest.mark.parametrize("drift", ("trigger", "foreign_key", "check"))
def test_existing_trust_anchor_drift_refuses_migration(client, drift: str) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_critic_trust_drift_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(
            host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
            user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source,
            autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds(),
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await trust.apply_critic_trust_anchor_migrations(cur)
                if drift == "trigger":
                    await cur.execute("DROP TRIGGER no_update_office_trusted_critic_key")
                elif drift == "foreign_key":
                    await cur.execute("ALTER TABLE office_trusted_critic_key_revocation DROP FOREIGN KEY fk_office_trusted_critic_key_revocation_key")
                else:
                    await cur.execute("ALTER TABLE office_trusted_critic_key DROP CONSTRAINT chk_office_trusted_critic_key_shape")
                with pytest.raises(RuntimeError, match="invalid|incompatible"):
                    await trust.apply_critic_trust_anchor_migrations(cur)
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)
