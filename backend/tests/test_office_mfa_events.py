"""MFA durable ledger schema is enforced by MariaDB before 3A2 writers exist."""
from __future__ import annotations

import aiomysql
import pytest

from virtual_office.migrations import apply_office_migrations
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
