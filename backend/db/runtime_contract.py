"""Fail-closed Office audit fence verification for the DML web principal."""
from __future__ import annotations

import aiomysql
from virtual_office.migrations import RUNTIME_COMPONENT as SCHEMA_COMPONENT
from virtual_office.migrations import RUNTIME_REVISION as SCHEMA_REVISION
from virtual_office.migrations import assert_mfa_ledger_runtime_metadata
from virtual_office.migrations import assert_bootstrap_runtime_metadata

FENCE_KEY = "office-audit-fence-v1"
FENCE_PRINCIPAL = "system:office-schema-migrator"
FENCE_OPERATION = "AUDIT_FENCE_CANARY"
FENCE_REASON = "office_audit_fence_v1"
APPEND_ONLY_MESSAGE = "office_authorization_event append-only"
MFA_FENCE_KEY = "office-mfa-fence-v1"
MFA_APPEND_ONLY_MESSAGE = "office_mfa_security_event append-only"


class RuntimeDbContractError(RuntimeError):
    pass


def _row(row, index: int):
    return row[index] if not isinstance(row, dict) else list(row.values())[index]


async def _fence_snapshot(cur):
    await cur.execute(
        "SELECT event_id,actor_user_id,actor_principal,event_kind,fence_key,operation,project_id,tenant_id,result,reason_code,correlation_id,occurred_at "
        "FROM office_authorization_event WHERE fence_key=%s", (FENCE_KEY,))
    rows = await cur.fetchall()
    if len(rows) != 1:
        raise RuntimeDbContractError("Office audit fence is absent or duplicated")
    row = rows[0]
    canonical = (None, FENCE_PRINCIPAL, "SYSTEM_FENCE", FENCE_KEY, FENCE_OPERATION, None, None, "RECORDED", FENCE_REASON, None)
    if tuple(_row(row, i) for i in range(1, 11)) != canonical:
        raise RuntimeDbContractError("Office audit fence is not canonical")
    return row


async def _trigger_metadata(cur):
    # Restricted MariaDB principals can legitimately receive NULL
    # ACTION_STATEMENT metadata; identity/timing/event are the enforceable
    # trigger contract and are therefore the fields verified here.
    await cur.execute("SELECT TRIGGER_NAME,ACTION_TIMING,EVENT_MANIPULATION,ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME IN ('no_update_office_authorization_event','no_delete_office_authorization_event')")
    found = {(str(_row(row, 0)), str(_row(row, 1)), str(_row(row, 2))) for row in await cur.fetchall()}
    expected = {("no_update_office_authorization_event", "BEFORE", "UPDATE"), ("no_delete_office_authorization_event", "BEFORE", "DELETE")}
    if found != expected:
        raise RuntimeDbContractError("Office audit triggers are missing or invalid")


async def _mfa_fence_snapshot(cur):
    await cur.execute("SELECT event_id,event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at FROM office_mfa_security_event WHERE fence_key=%s", (MFA_FENCE_KEY,))
    rows = await cur.fetchall()
    if len(rows) != 1:
        raise RuntimeDbContractError("Office MFA fence is absent or duplicated")
    row = rows[0]
    if tuple(_row(row, i) for i in range(1, 17)) != ("SYSTEM_FENCE", None, "SYSTEM_MIGRATOR", None, None, None, None, None, None, None, None, None, None, "RECORDED", None, MFA_FENCE_KEY):
        raise RuntimeDbContractError("Office MFA fence is not canonical")
    return row


async def _mfa_trigger_metadata(cur):
    try:
        await assert_mfa_ledger_runtime_metadata(cur, allow_redacted_trigger_body=True)
    except RuntimeError as error:
        raise RuntimeDbContractError("Office MFA triggers or ledger metadata are invalid") from error


async def _bootstrap_metadata(cur):
    try:
        await assert_bootstrap_runtime_metadata(cur)
    except RuntimeError as error:
        raise RuntimeDbContractError("Office bootstrap state metadata is invalid") from error


async def _revision(cur):
    await cur.execute("SELECT revision FROM office_schema_revision WHERE component=%s", (SCHEMA_COMPONENT,))
    row = await cur.fetchone()
    if row is None or _row(row, 0) != SCHEMA_REVISION:
        raise RuntimeDbContractError("Office schema revision is absent")


_CURRENT_GRANTEE = "CONCAT(\"'\",SUBSTRING_INDEX(CURRENT_USER(),'@',1),\"'@'\",SUBSTRING_INDEX(CURRENT_USER(),'@',-1),\"'\")"
_RUNTIME_DML = frozenset(("SELECT", "INSERT", "UPDATE", "DELETE"))


async def _effective_privilege_guard(cur) -> None:
    """Require only the exact DML schema grant for the connected principal.

    Metadata is queried by CURRENT_USER(), never by an application username or
    a parsed SHOW GRANTS string.
    """
    await cur.execute("SELECT CURRENT_USER()")
    identity_row = await cur.fetchone()
    identity = "" if identity_row is None else str(_row(identity_row, 0))
    # INFORMATION_SCHEMA stores GRANTEE as a quoted user@host tuple. The
    # SQL expression below is safe only for the ordinary runtime identities
    # provisioned by this contract; ambiguous @ or quote characters fail shut.
    if identity.count("@") != 1 or "'" in identity:
        raise RuntimeDbContractError("runtime database principal identity is unsupported")

    async def privileges(source: str, scope: str = ""):
        await cur.execute(
            "SELECT PRIVILEGE_TYPE,IS_GRANTABLE FROM information_schema." + source +
            " WHERE GRANTEE=" + _CURRENT_GRANTEE + scope
        )
        return {(str(_row(row, 0)), str(_row(row, 1))) for row in await cur.fetchall()}

    global_privileges = await privileges("USER_PRIVILEGES")
    if global_privileges != {("USAGE", "NO")}:
        raise RuntimeDbContractError("runtime database principal has non-DML global privilege")
    schema_privileges = await privileges("SCHEMA_PRIVILEGES", " AND TABLE_SCHEMA=DATABASE()")
    if schema_privileges != {(privilege, "NO") for privilege in _RUNTIME_DML}:
        raise RuntimeDbContractError("runtime database principal does not have exact DML schema privileges")
    table_privileges = await privileges("TABLE_PRIVILEGES", " AND TABLE_SCHEMA=DATABASE()")
    if table_privileges:
        raise RuntimeDbContractError("runtime database principal has table-specific privileges")
    # MariaDB has no INFORMATION_SCHEMA.ROUTINE_PRIVILEGES relation, and a
    # DML-only principal cannot read mysql.procs_priv. ROUTINES only exposes
    # routines visible to the effective account, so any visible routine is an
    # extra capability and fails the exact-DML contract.
    await cur.execute(
        "SELECT ROUTINE_NAME,ROUTINE_TYPE FROM information_schema.ROUTINES "
        "WHERE ROUTINE_SCHEMA=DATABASE()"
    )
    if await cur.fetchone() is not None:
        raise RuntimeDbContractError("runtime database principal has routine privileges")
    await cur.execute("SELECT 1 FROM information_schema.APPLICABLE_ROLES WHERE GRANTEE=" + _CURRENT_GRANTEE + " LIMIT 1")
    if await cur.fetchone() is not None:
        raise RuntimeDbContractError("runtime database principal inherits an applicable role")
    # MariaDB exposes an activated default role through CURRENT_ROLE() even
    # where APPLICABLE_ROLES omits it for the connected account host pattern.
    await cur.execute("SELECT CURRENT_ROLE()")
    role = await cur.fetchone()
    if role is not None and _row(role, 0) not in (None, "NONE"):
        raise RuntimeDbContractError("runtime database principal inherits an applicable role")


async def _probe(pool, statement: str) -> None:
    before = None
    async with pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                before = await _fence_snapshot(cur)
                try:
                    await cur.execute(statement, (FENCE_KEY,))
                except aiomysql.OperationalError as exc:
                    if getattr(exc, "args", ()) != (1644, APPEND_ONLY_MESSAGE):
                        raise RuntimeDbContractError("Office audit trigger did not reject canonically") from exc
                else:
                    raise RuntimeDbContractError("Office audit trigger permitted a mutation")
                after = await _fence_snapshot(cur)
                if after != before:
                    raise RuntimeDbContractError("Office audit fence changed during rejected probe")
        finally:
            await conn.rollback()
    async with pool.acquire() as reread_conn:
        async with reread_conn.cursor() as reread_cur:
            if await _fence_snapshot(reread_cur) != before:
                raise RuntimeDbContractError("Office audit fence changed after probe rollback")


async def _mfa_probe(pool, statement: str, parameter=MFA_FENCE_KEY) -> None:
    before = None
    async with pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                before = await _mfa_fence_snapshot(cur)
                try:
                    await cur.execute(statement, (parameter,))
                except aiomysql.OperationalError as error:
                    if error.args != (1644, MFA_APPEND_ONLY_MESSAGE):
                        raise RuntimeDbContractError("Office MFA trigger did not reject canonically") from error
                else:
                    raise RuntimeDbContractError("Office MFA trigger permitted a mutation")
                if await _mfa_fence_snapshot(cur) != before:
                    raise RuntimeDbContractError("Office MFA fence changed during probe")
        finally:
            await conn.rollback()
    async with pool.acquire() as reread_conn:
        async with reread_conn.cursor() as reread_cur:
            if await _mfa_fence_snapshot(reread_cur) != before:
                raise RuntimeDbContractError("Office MFA fence changed after probe rollback")


async def _mfa_normal_event_id(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT event_id FROM office_mfa_security_event "
                "WHERE event_kind IN ('MFA_PROOF','MFA_STATE_CHANGE') ORDER BY event_id LIMIT 1"
            )
            row = await cur.fetchone()
            return None if row is None else _row(row, 0)


async def _mfa_pristine_ledger_probe(pool) -> None:
    """Prove append-only behavior without committing fabricated MFA evidence.

    A database that contains only the migration fence must still reject a
    conditional trigger which protects that fence alone.  The DML principal
    creates a contract-valid ``DENIED`` row in its transaction, probes both
    mutations, and rolls the entire transaction back.  It never persists a
    successful verification canary.
    """
    before = None
    async with pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                before = await _mfa_fence_snapshot(cur)
                await cur.execute("SELECT user_id FROM jax_users ORDER BY user_id LIMIT 1")
                subject = await cur.fetchone()
                if subject is None:
                    raise RuntimeDbContractError("Office MFA trigger cannot be safely probed without a subject")
                user_id = _row(subject, 0)
                await cur.execute(
                    "INSERT INTO office_mfa_security_event "
                    "(event_kind,actor_user_id,principal_kind,subject_user_id,operation,purpose,token_version,mfa_version,result,server_correlation_id) "
                    "VALUES ('MFA_PROOF',%s,'USER_SESSION',%s,'MFA_PROOF','STEP_UP',0,0,'DENIED',UNHEX(REPLACE(UUID(),'-','')))",
                    (user_id, user_id),
                )
                event_id = cur.lastrowid
                for statement in (
                    "UPDATE office_mfa_security_event SET occurred_at=DATE_ADD(occurred_at, INTERVAL 1 MICROSECOND) WHERE event_id=%s",
                    "DELETE FROM office_mfa_security_event WHERE event_id=%s",
                ):
                    try:
                        await cur.execute(statement, (event_id,))
                    except aiomysql.OperationalError as error:
                        if error.args != (1644, MFA_APPEND_ONLY_MESSAGE):
                            raise RuntimeDbContractError("Office MFA trigger did not reject canonically") from error
                    else:
                        raise RuntimeDbContractError("Office MFA trigger permitted a mutation")
                if await _mfa_fence_snapshot(cur) != before:
                    raise RuntimeDbContractError("Office MFA fence changed during probe")
        finally:
            await conn.rollback()
    async with pool.acquire() as reread_conn:
        async with reread_conn.cursor() as reread_cur:
            if await _mfa_fence_snapshot(reread_cur) != before:
                raise RuntimeDbContractError("Office MFA fence changed after probe rollback")


async def assert_runtime_db_contract(pool) -> None:
    """Verify revision, canonical fence, trigger metadata and DML behavior."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _effective_privilege_guard(cur)
            await _revision(cur)
            await _fence_snapshot(cur)
            await _trigger_metadata(cur)
            await _mfa_fence_snapshot(cur)
            await _mfa_trigger_metadata(cur)
            await _bootstrap_metadata(cur)
    await _probe(pool, "UPDATE office_authorization_event SET occurred_at=DATE_ADD(occurred_at, INTERVAL 1 MICROSECOND) WHERE fence_key=%s")
    await _probe(pool, "DELETE FROM office_authorization_event WHERE fence_key=%s")
    await _mfa_probe(pool, "UPDATE office_mfa_security_event SET occurred_at=DATE_ADD(occurred_at, INTERVAL 1 MICROSECOND) WHERE fence_key=%s")
    await _mfa_probe(pool, "DELETE FROM office_mfa_security_event WHERE fence_key=%s")
    normal_event_id = await _mfa_normal_event_id(pool)
    if normal_event_id is not None:
        await _mfa_probe(pool, "UPDATE office_mfa_security_event SET occurred_at=DATE_ADD(occurred_at, INTERVAL 1 MICROSECOND) WHERE event_id=%s", normal_event_id)
        await _mfa_probe(pool, "DELETE FROM office_mfa_security_event WHERE event_id=%s", normal_event_id)
    else:
        await _mfa_pristine_ledger_probe(pool)
