"""MFA durable ledger schema is enforced by MariaDB before 3A2 writers exist."""
from __future__ import annotations

import aiomysql
import pytest

from virtual_office.migrations import (
    _is_canonical_mfa_check,
    apply_office_migrations,
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
