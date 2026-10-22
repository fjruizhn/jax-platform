"""MFA durable ledger schema is enforced by MariaDB before 3A2 writers exist."""
from __future__ import annotations

import os
import sys
import uuid

import aiomysql
import pytest

from virtual_office.migrations import (
    _is_canonical_mfa_check,
    apply_office_migrations,
    assert_mfa_ledger_runtime_metadata,
)
from tests.identidades import crear_usuario


def test_mfa_ledger_rejects_malformed_fence_and_is_append_only(client):
    """Removing the discriminator or either trigger must fail at the DB boundary."""
    async def scenario():
        from db.connection import get_pool
        user_id, _ = await crear_usuario()
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                with pytest.raises(aiomysql.OperationalError) as error:
                    await cur.execute("INSERT INTO office_mfa_security_event (event_kind,principal_kind,result,fence_key) VALUES ('SYSTEM_FENCE','SYSTEM_MIGRATOR','RECORDED','wrong')")
                assert error.value.args[0] == 4025
                invalid = (
                    "INSERT INTO office_mfa_security_event (event_kind,principal_kind,subject_user_id,operation,purpose,token_version,mfa_version,result) VALUES ('MFA_PROOF','USER_SESSION',%s,'MFA_PROOF','STEP_UP',0,0,'VERIFIED')",
                    "INSERT INTO office_mfa_security_event (event_kind,actor_user_id,principal_kind,subject_user_id,operation,purpose,token_version,mfa_version,result) VALUES ('MFA_PROOF',%s,'PASSWORD_PREAUTH',%s,'MFA_PROOF','STEP_UP',0,0,'VERIFIED')",
                    "INSERT INTO office_mfa_security_event (event_kind,actor_user_id,principal_kind,subject_user_id,operation,purpose,token_version,mfa_version,result) VALUES ('MFA_PROOF',%s,'USER_SESSION',%s,'WRONG','STEP_UP',0,0,'VERIFIED')",
                    "INSERT INTO office_mfa_security_event (event_kind,actor_user_id,principal_kind,subject_user_id,operation,purpose,token_version,mfa_version,result) VALUES ('MFA_PROOF',%s,'USER_SESSION',%s,'MFA_PROOF',NULL,0,0,'VERIFIED')",
                )
                for statement in invalid:
                    with pytest.raises(aiomysql.OperationalError) as error:
                        await cur.execute(statement, tuple([user_id] * statement.count('%s')))
                    assert error.value.args[0] == 4025
                await cur.execute("INSERT INTO office_mfa_security_event (event_kind,actor_user_id,principal_kind,subject_user_id,operation,purpose,token_version,mfa_version,result,server_correlation_id) VALUES ('MFA_PROOF',%s,'USER_SESSION',%s,'MFA_PROOF','STEP_UP',0,0,'VERIFIED',UNHEX(REPLACE(UUID(),'-','')))", (user_id, user_id))
                await cur.execute("SELECT event_id FROM office_mfa_security_event WHERE fence_key='office-mfa-fence-v1'")
                fence = (await cur.fetchone())[0]
                with pytest.raises(aiomysql.OperationalError) as error:
                    await cur.execute("UPDATE office_mfa_security_event SET occurred_at=NOW(6) WHERE event_id=%s", (fence,))
                assert error.value.args == (1644, 'office_mfa_security_event append-only')
                with pytest.raises(aiomysql.OperationalError) as error:
                    await cur.execute("DELETE FROM office_mfa_security_event WHERE event_id=%s", (fence,))
                assert error.value.args == (1644, 'office_mfa_security_event append-only')
    client.portal.call(scenario)


def test_mfa_ledger_v5_principal_and_state_purpose_shapes(client):
    """The ledger carries the immutable principal contract needed by 3A2."""
    async def scenario():
        from db.connection import get_pool

        user_id, _ = await crear_usuario()
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute(
                    "INSERT INTO office_mfa_challenge "
                    "(token_hash,user_id,purpose,expected_token_version,expected_mfa_version,max_attempts,expires_at,state,created_at) "
                    "VALUES (UNHEX(REPLACE(UUID(),'-','')), %s, 'LOGIN', 0, 0, 3, DATE_ADD(NOW(6), INTERVAL 5 MINUTE), 'ISSUED', NOW(6))",
                    (user_id,),
                )
                challenge_id = cur.lastrowid
                with pytest.raises(aiomysql.OperationalError) as error:
                    await cur.execute(
                        "INSERT INTO office_mfa_security_event "
                        "(event_kind,actor_user_id,principal_kind,subject_user_id,operation,purpose,token_version,mfa_version,result,server_correlation_id) "
                        "VALUES ('MFA_PROOF',%s,'USER_SESSION',%s,'MFA_PROOF','LOGIN',0,0,'VERIFIED',UNHEX(REPLACE(UUID(),'-','')))",
                        (user_id, user_id),
                    )
                assert error.value.args[0] == 4025
                await cur.execute(
                    "INSERT INTO office_mfa_security_event "
                    "(event_kind,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,result,server_correlation_id) "
                    "VALUES ('MFA_PROOF','PASSWORD_PREAUTH',%s,%s,'MFA_PROOF','LOGIN',0,0,'VERIFIED',UNHEX(REPLACE(UUID(),'-','')))",
                    (challenge_id, user_id),
                )
                proof_id = cur.lastrowid
                await cur.execute(
                    "INSERT INTO office_mfa_security_event "
                    "(event_kind,actor_user_id,principal_kind,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,result,server_correlation_id) "
                    "VALUES ('MFA_STATE_CHANGE',%s,'USER_SESSION',%s,'RECOVERY_REGENERATE','RECOVERY',1,1,0,0,%s,'RECORDED',UNHEX(REPLACE(UUID(),'-','')))",
                    (user_id, user_id, proof_id),
                )
                with pytest.raises(aiomysql.OperationalError) as error:
                    await cur.execute(
                        "INSERT INTO office_mfa_security_event "
                        "(event_kind,actor_user_id,principal_kind,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,result,server_correlation_id) "
                        "VALUES ('MFA_STATE_CHANGE',%s,'USER_SESSION',%s,'MFA_DISABLE','RECOVERY',1,1,0,0,%s,'RECORDED',UNHEX(REPLACE(UUID(),'-','')))",
                        (user_id, user_id, proof_id),
                    )
                assert error.value.args[0] == 4025
    client.portal.call(scenario)


def test_mfa_credential_pending_bundle_and_invalidated_challenge_are_enforced_by_sql(client):
    """v5 permits an ACTIVE old factor plus only a complete pending bundle."""
    async def scenario():
        from db.connection import get_pool

        active_user, _ = await crear_usuario()
        partial_users = [await crear_usuario() for _ in range(3)]
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute(
                    "INSERT INTO office_mfa_credential "
                    "(user_id,state,secret_ciphertext,pending_secret_ciphertext,pending_enrollment_id,pending_expires_at,created_at,updated_at) "
                    "VALUES (%s,'ACTIVE','old-factor','new-factor',UNHEX(REPLACE(UUID(),'-','')),DATE_ADD(NOW(6),INTERVAL 5 MINUTE),NOW(6),NOW(6))",
                    (active_user,),
                )
                for index, (user_id, _) in enumerate(partial_users):
                    pending_secret = "'new-factor'" if index != 1 else "NULL"
                    pending_id = "UNHEX(REPLACE(UUID(),'-',''))" if index != 2 else "NULL"
                    pending_expiry = "DATE_ADD(NOW(6),INTERVAL 5 MINUTE)" if index != 0 else "NULL"
                    with pytest.raises(aiomysql.OperationalError) as error:
                        await cur.execute(
                            "INSERT INTO office_mfa_credential "
                            "(user_id,state,secret_ciphertext,pending_secret_ciphertext,pending_enrollment_id,pending_expires_at,created_at,updated_at) "
                            f"VALUES (%s,'ACTIVE','old-factor',{pending_secret},{pending_id},{pending_expiry},NOW(6),NOW(6))",
                            (user_id,),
                        )
                    assert error.value.args[0] == 4025
                await cur.execute(
                    "INSERT INTO office_mfa_challenge "
                    "(token_hash,user_id,purpose,expected_token_version,expected_mfa_version,max_attempts,expires_at,state,created_at) "
                    "VALUES (UNHEX(REPLACE(UUID(),'-','')), %s, 'RECOVERY', 0, 0, 3, NOW(6), 'INVALIDATED', NOW(6))",
                    (active_user,),
                )
    client.portal.call(scenario)


def test_runtime_metadata_rejects_permissive_checks_and_budget_index_tampering(client):
    async def scenario():
        from db.connection import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                # The challenge FK needs a left-prefix user_id index while we
                # replace the budget index with deliberately bad variants.
                await cur.execute("ALTER TABLE office_mfa_challenge ADD KEY IF NOT EXISTS idx_office_mfa_challenge_test_fk_support (user_id)")

                async def restore_budget_index():
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX idx_office_mfa_challenge_user")
                    await cur.execute(
                        "ALTER TABLE office_mfa_challenge ADD KEY idx_office_mfa_challenge_user "
                        "(user_id,purpose,expected_token_version,expected_mfa_version,state,expires_at)"
                    )

                for table, name, error in (
                    ("office_mfa_credential", "chk_office_mfa_credential_shape", "credential shape"),
                    ("office_mfa_challenge", "chk_office_mfa_challenge_state", "challenge state"),
                ):
                    await cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
                    await cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK (1=1)")
                    with pytest.raises(RuntimeError, match=error):
                        await assert_mfa_ledger_runtime_metadata(cur)
                    await cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
                    if table == "office_mfa_credential":
                        await cur.execute(
                            "ALTER TABLE office_mfa_credential ADD CONSTRAINT chk_office_mfa_credential_shape "
                            "CHECK ((state='PENDING' AND secret_ciphertext IS NULL AND pending_secret_ciphertext IS NOT NULL AND pending_enrollment_id IS NOT NULL AND pending_expires_at IS NOT NULL) OR (state='ACTIVE' AND secret_ciphertext IS NOT NULL AND ((pending_secret_ciphertext IS NULL AND pending_enrollment_id IS NULL AND pending_expires_at IS NULL) OR (pending_secret_ciphertext IS NOT NULL AND pending_enrollment_id IS NOT NULL AND pending_expires_at IS NOT NULL))) OR (state IN ('RECOVERY_REQUIRED','DISABLED') AND secret_ciphertext IS NULL AND pending_secret_ciphertext IS NULL AND pending_enrollment_id IS NULL AND pending_expires_at IS NULL))"
                        )
                    else:
                        await cur.execute(
                            "ALTER TABLE office_mfa_challenge ADD CONSTRAINT chk_office_mfa_challenge_state "
                            "CHECK (state IN ('ISSUED','CONSUMED','EXHAUSTED','EXPIRED','INVALIDATED'))"
                        )

                for definition in (
                    "UNIQUE KEY idx_office_mfa_challenge_user (user_id,purpose,expected_token_version,expected_mfa_version,state,expires_at,token_hash)",
                    "KEY idx_office_mfa_challenge_user (user_id,purpose(3),expected_token_version,expected_mfa_version,state,expires_at)",
                ):
                    await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX idx_office_mfa_challenge_user")
                    await cur.execute(f"ALTER TABLE office_mfa_challenge ADD {definition}")
                    with pytest.raises(RuntimeError, match="budget index is incompatible"):
                        await assert_mfa_ledger_runtime_metadata(cur)
                    await restore_budget_index()

                await cur.execute("ALTER TABLE office_mfa_challenge ALTER INDEX idx_office_mfa_challenge_user IGNORED")
                try:
                    with pytest.raises(RuntimeError, match="budget index is incompatible"):
                        await assert_mfa_ledger_runtime_metadata(cur)
                finally:
                    await cur.execute("ALTER TABLE office_mfa_challenge ALTER INDEX idx_office_mfa_challenge_user NOT IGNORED")
                await assert_mfa_ledger_runtime_metadata(cur)
                await cur.execute("ALTER TABLE office_mfa_challenge DROP INDEX idx_office_mfa_challenge_test_fk_support")
    client.portal.call(scenario)


def test_mfa_ledger_constraint_metadata_is_exact_and_permissive_replacements_fail_closed(client):
    """Whitespace alone is harmless; case, NULL guards, grouping, and ORs are not."""
    async def scenario():
        from db.connection import get_pool
        pool = await get_pool()
        names = (
            "chk_office_mfa_security_event_fence",
            "chk_office_mfa_security_event_shape",
            "chk_office_mfa_security_event_truth",
        )
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute(
                    "SELECT CONSTRAINT_NAME,CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
                    "WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME IN (%s,%s,%s)",
                    names,
                )
                clauses = dict(await cur.fetchall())
                assert set(clauses) == set(names)
                for name, clause in clauses.items():
                    assert _is_canonical_mfa_check(name, clause)
                    assert _is_canonical_mfa_check(name, "  " + clause.replace(" ", "\n ") + "  ")
                truth = clauses["chk_office_mfa_security_event_truth"]
                assert not _is_canonical_mfa_check(
                    "chk_office_mfa_security_event_truth", truth.replace("'MFA_PROOF'", "'mfa_proof'", 1)
                )
                assert not _is_canonical_mfa_check(
                    "chk_office_mfa_security_event_truth", truth.replace(" is not null", " is null", 1)
                )
                assert not _is_canonical_mfa_check(
                    "chk_office_mfa_security_event_truth", truth.replace(" or ", " and ", 1)
                )
                for name in names:
                    await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                    await cur.execute(f"ALTER TABLE office_mfa_security_event DROP CONSTRAINT {name}")
                    await cur.execute(
                        f"ALTER TABLE office_mfa_security_event ADD CONSTRAINT {name} CHECK (1=1)"
                    )
                    with pytest.raises(RuntimeError, match=name):
                        await apply_office_migrations(cur)
                    await cur.execute(
                        "SELECT 1 FROM office_schema_revision WHERE component='virtual-office'"
                    )
                    assert await cur.fetchone() is None
                    await cur.execute(f"ALTER TABLE office_mfa_security_event DROP CONSTRAINT {name}")
                    await cur.execute(
                        f"ALTER TABLE office_mfa_security_event ADD CONSTRAINT {name} CHECK ({clauses[name]})"
                    )
                await apply_office_migrations(cur)
        from db.migrations import run_migrations
        await run_migrations()
    client.portal.call(scenario)


def test_mfa_ledger_lookup_indexes_have_real_explain_coverage(client):
    async def scenario():
        from db.connection import get_pool

        user_id, _ = await crear_usuario()
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute(
                    "INSERT INTO office_mfa_challenge "
                    "(token_hash,user_id,purpose,expected_token_version,expected_mfa_version,max_attempts,expires_at,state,created_at) "
                    "VALUES (UNHEX(REPLACE(UUID(),'-','')), %s, 'STEP_UP', 0, 0, 3, DATE_ADD(NOW(6), INTERVAL 5 MINUTE), 'ISSUED', NOW(6))",
                    (user_id,),
                )
                challenge_id = cur.lastrowid
                for _ in range(2):
                    await cur.execute(
                        "INSERT INTO office_mfa_security_event "
                        "(event_kind,actor_user_id,principal_kind,subject_user_id,operation,purpose,token_version,mfa_version,result,server_correlation_id) "
                        "VALUES ('MFA_PROOF',%s,'USER_SESSION',%s,'MFA_PROOF','STEP_UP',0,0,'VERIFIED',UNHEX(REPLACE(UUID(),'-','')))",
                        (user_id, user_id),
                    )
                probes = (
                    (
                        "EXPLAIN SELECT user_id FROM office_mfa_challenge WHERE challenge_id=%s",
                        (challenge_id,),
                        "challenge_id",
                    ),
                    (
                        "EXPLAIN SELECT token_hash FROM office_mfa_challenge "
                        "WHERE user_id=%s AND purpose='STEP_UP' AND expected_token_version=0 "
                        "AND expected_mfa_version=0 AND state='ISSUED' AND expires_at>NOW(6)",
                        (user_id,),
                        "idx_office_mfa_challenge_user",
                    ),
                    (
                        "EXPLAIN SELECT event_id FROM office_mfa_security_event "
                        "WHERE subject_user_id=%s AND purpose='STEP_UP' AND token_version=0 AND mfa_version=0 "
                        "ORDER BY occurred_at,event_id",
                        (user_id,),
                        "idx_office_mfa_security_event_proof",
                    ),
                    (
                        "EXPLAIN SELECT event_id FROM office_mfa_security_event "
                        "WHERE challenge_id=%s AND subject_user_id=%s",
                        (challenge_id, user_id),
                        "idx_office_mfa_security_event_challenge",
                    ),
                )
                for statement, args, key in probes:
                    await cur.execute(statement, args)
                    row = await cur.fetchone()
                    assert row[5] == key
                    assert "Using filesort" not in (row[9] or "")
                    assert "Using temporary" not in (row[9] or "")
    client.portal.call(scenario)


def test_migration_rejects_conditional_mfa_triggers_on_a_pristine_ledger(client):
    async def scenario():
        from db.connection import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                for event, name in (("UPDATE", "no_update_office_mfa_security_event"), ("DELETE", "no_delete_office_mfa_security_event")):
                    await cur.execute(f"DROP TRIGGER {name}")
                    await cur.execute(
                        f"CREATE TRIGGER {name} BEFORE {event} ON office_mfa_security_event FOR EACH ROW "
                        "BEGIN IF OLD.fence_key IS NOT NULL THEN "
                        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_mfa_security_event append-only'; END IF; END"
                    )
                with pytest.raises(RuntimeError, match="MFA triggers are missing or invalid"):
                    await apply_office_migrations(cur)
                await cur.execute("SELECT 1 FROM office_schema_revision WHERE component='virtual-office'")
                assert await cur.fetchone() is None
                await cur.execute("DROP TRIGGER no_update_office_mfa_security_event")
                await cur.execute("DROP TRIGGER no_delete_office_mfa_security_event")
                await apply_office_migrations(cur)
        from db.migrations import run_migrations
        await run_migrations()
    client.portal.call(scenario)


def test_migration_rejects_each_missing_or_homonymous_mfa_foreign_key(client):
    async def scenario():
        from db.connection import get_pool

        definitions = {
            "fk_office_mfa_security_event_actor": "FOREIGN KEY (actor_user_id) REFERENCES jax_users(user_id)",
            "fk_office_mfa_security_event_subject": "FOREIGN KEY (subject_user_id) REFERENCES jax_users(user_id)",
            "fk_office_mfa_security_event_challenge_subject": "FOREIGN KEY (challenge_id,subject_user_id) REFERENCES office_mfa_challenge(challenge_id,user_id)",
            "fk_office_mfa_security_event_proof": "FOREIGN KEY (proof_event_id) REFERENCES office_mfa_security_event(event_id)",
            "fk_office_mfa_security_event_authorization": "FOREIGN KEY (authorization_event_id) REFERENCES office_authorization_event(event_id)",
        }
        wrong = {
            "fk_office_mfa_security_event_actor": "FOREIGN KEY (subject_user_id) REFERENCES jax_users(user_id)",
            "fk_office_mfa_security_event_subject": "FOREIGN KEY (actor_user_id) REFERENCES jax_users(user_id)",
            "fk_office_mfa_security_event_challenge_subject": "FOREIGN KEY (challenge_id) REFERENCES office_mfa_challenge(challenge_id)",
            "fk_office_mfa_security_event_proof": "FOREIGN KEY (proof_event_id) REFERENCES office_authorization_event(event_id)",
            "fk_office_mfa_security_event_authorization": "FOREIGN KEY (authorization_event_id) REFERENCES office_mfa_security_event(event_id)",
        }
        cross_schema = {
            "fk_office_mfa_security_event_actor": "FOREIGN KEY (actor_user_id) REFERENCES `{schema}`.`jax_users`(user_id)",
            "fk_office_mfa_security_event_subject": "FOREIGN KEY (subject_user_id) REFERENCES `{schema}`.`jax_users`(user_id)",
            "fk_office_mfa_security_event_challenge_subject": "FOREIGN KEY (challenge_id,subject_user_id) REFERENCES `{schema}`.`office_mfa_challenge`(challenge_id,user_id)",
            "fk_office_mfa_security_event_proof": "FOREIGN KEY (proof_event_id) REFERENCES `{schema}`.`office_mfa_security_event`(event_id)",
            "fk_office_mfa_security_event_authorization": "FOREIGN KEY (authorization_event_id) REFERENCES `{schema}`.`office_authorization_event`(event_id)",
        }
        source = os.environ["JAX_DB_NAME"]
        shadow = f"office_mfa_fk_shadow_{uuid.uuid4().hex[:16]}"
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute(f"CREATE DATABASE `{shadow}`")

                async def restore_canonical(name: str) -> None:
                    try:
                        await cur.execute(f"ALTER TABLE office_mfa_security_event DROP FOREIGN KEY {name}")
                    except aiomysql.OperationalError as error:
                        if error.args[0] != 1091:
                            raise
                    await cur.execute(f"ALTER TABLE office_mfa_security_event ADD CONSTRAINT {name} {definitions[name]}")

                try:
                    for table in ("jax_users", "office_mfa_challenge", "office_mfa_security_event", "office_authorization_event"):
                        await cur.execute(f"CREATE TABLE `{shadow}`.`{table}` LIKE `{source}`.`{table}`")
                    for name, definition in definitions.items():
                        await cur.execute("DELETE FROM office_schema_revision WHERE component='virtual-office'")
                        await cur.execute(f"ALTER TABLE office_mfa_security_event DROP FOREIGN KEY {name}")
                        with pytest.raises(RuntimeError, match=name):
                            await apply_office_migrations(cur)
                        await cur.execute(f"ALTER TABLE office_mfa_security_event ADD CONSTRAINT {name} {definition}")
                        await cur.execute(f"ALTER TABLE office_mfa_security_event DROP FOREIGN KEY {name}")
                        # The shared disposable DB can now contain real
                        # proof_event_id evidence from the frozen service.
                        # Disable checks only for this DDL shape probe, then
                        # restore them before exercising the migrator; no row
                        # is deleted or rewritten.
                        await cur.execute("SET FOREIGN_KEY_CHECKS=0")
                        try:
                            await cur.execute(f"ALTER TABLE office_mfa_security_event ADD CONSTRAINT {name} {wrong[name]}")
                        finally:
                            await cur.execute("SET FOREIGN_KEY_CHECKS=1")
                        with pytest.raises(RuntimeError, match=name):
                            await apply_office_migrations(cur)
                        await restore_canonical(name)
                        await cur.execute(f"ALTER TABLE office_mfa_security_event DROP FOREIGN KEY {name}")
                        await cur.execute("SET FOREIGN_KEY_CHECKS=0")
                        try:
                            await cur.execute(
                                f"ALTER TABLE office_mfa_security_event ADD CONSTRAINT {name} "
                                + cross_schema[name].format(schema=shadow)
                            )
                        finally:
                            await cur.execute("SET FOREIGN_KEY_CHECKS=1")
                        with pytest.raises(RuntimeError, match=name):
                            await apply_office_migrations(cur)
                        await restore_canonical(name)
                    await apply_office_migrations(cur)
                finally:
                    original_failure = sys.exc_info()[0] is not None
                    cleanup_failure = None
                    for name in definitions:
                        try:
                            await restore_canonical(name)
                        except Exception as error:
                            cleanup_failure = cleanup_failure or error
                    try:
                        await cur.execute(f"DROP DATABASE IF EXISTS `{shadow}`")
                    except Exception as error:
                        cleanup_failure = cleanup_failure or error
                    if cleanup_failure is not None and not original_failure:
                        raise cleanup_failure
        from db.migrations import run_migrations
        await run_migrations()
    client.portal.call(scenario)
