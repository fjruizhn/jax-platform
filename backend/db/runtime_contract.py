"""Fail-closed Office audit fence verification for the DML web principal."""
from __future__ import annotations

import aiomysql
import secrets
from virtual_office.migrations import RUNTIME_COMPONENT as SCHEMA_COMPONENT
from virtual_office.migrations import RUNTIME_REVISION as SCHEMA_REVISION
from virtual_office.migrations import assert_mfa_ledger_runtime_metadata
from virtual_office.migrations import assert_bootstrap_runtime_metadata
from virtual_office.migrations import assert_membership_receipt_runtime_metadata
from virtual_office.migrations import assert_profile_revocation_runtime_metadata
from .runtime_privileges import runtime_table_privilege_map

FENCE_KEY = "office-audit-fence-v1"
FENCE_PRINCIPAL = "system:office-schema-migrator"
FENCE_OPERATION = "AUDIT_FENCE_CANARY"
FENCE_REASON = "office_audit_fence_v1"
APPEND_ONLY_MESSAGE = "office_authorization_event append-only"
MFA_FENCE_KEY = "office-mfa-fence-v1"
MFA_APPEND_ONLY_MESSAGE = "office_mfa_security_event append-only"
BOOTSTRAP_KEY = "INITIAL_OFFICE_SUPERUSER"
BOOTSTRAP_APPEND_ONLY_MESSAGE = "office_bootstrap_state append-only"


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
    await cur.execute(
        "SELECT TRIGGER_NAME,ACTION_TIMING,EVENT_MANIPULATION,ACTION_STATEMENT "
        "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() "
        "AND EVENT_OBJECT_TABLE='office_authorization_event'"
    )
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
        # The web DML principal can legitimately receive a redacted trigger
        # body.  Table/FK/CHECK/PK metadata and trigger identity remain
        # enforceable here; the migration principal attests the full body.
        await assert_bootstrap_runtime_metadata(cur, allow_redacted_trigger_body=True)
    except RuntimeError as error:
        raise RuntimeDbContractError("Office bootstrap state metadata is invalid") from error


async def _membership_receipt_metadata(cur):
    try:
        await assert_membership_receipt_runtime_metadata(
            cur, allow_redacted_trigger_body=True, require_table=True)
    except RuntimeError as error:
        raise RuntimeDbContractError("Office membership receipt metadata is invalid") from error


async def _profile_revocation_metadata(cur):
    try:
        await assert_profile_revocation_runtime_metadata(cur, allow_redacted_trigger_body=True)
    except RuntimeError as error:
        raise RuntimeDbContractError("Office profile revocation metadata is invalid") from error


async def _revision(cur):
    await cur.execute("SELECT revision FROM office_schema_revision WHERE component=%s", (SCHEMA_COMPONENT,))
    row = await cur.fetchone()
    if row is None or _row(row, 0) != SCHEMA_REVISION:
        raise RuntimeDbContractError("Office schema revision is absent")


_CURRENT_GRANTEE = "CONCAT(\"'\",SUBSTRING_INDEX(CURRENT_USER(),'@',1),\"'@'\",SUBSTRING_INDEX(CURRENT_USER(),'@',-1),\"'\")"
_RUNTIME_SCHEMA_PRIVILEGES = frozenset(("SELECT", "INSERT"))


async def _effective_privilege_guard(cur) -> None:
    """Require the exact schema/table split for the connected principal.

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
    expected_schema_privileges = {(privilege, "NO") for privilege in _RUNTIME_SCHEMA_PRIVILEGES}
    await cur.execute("SELECT DATABASE()")
    database_row = await cur.fetchone()
    database = "" if database_row is None else str(_row(database_row, 0))
    if not database:
        raise RuntimeDbContractError("runtime database name is unavailable")
    await cur.execute(
        "SELECT TABLE_SCHEMA,PRIVILEGE_TYPE,IS_GRANTABLE FROM information_schema.SCHEMA_PRIVILEGES "
        "WHERE GRANTEE=" + _CURRENT_GRANTEE
    )
    actual_schema_privileges = {
        (str(_row(row, 0)), str(_row(row, 1)), str(_row(row, 2)))
        for row in await cur.fetchall()
    }
    if actual_schema_privileges != {(database, privilege, grantable) for privilege, grantable in expected_schema_privileges}:
        raise RuntimeDbContractError("runtime database principal does not have exact schema privileges")
    await cur.execute(
        "SELECT TABLE_SCHEMA,TABLE_NAME,PRIVILEGE_TYPE,IS_GRANTABLE FROM information_schema.TABLE_PRIVILEGES "
        "WHERE GRANTEE=" + _CURRENT_GRANTEE + " ORDER BY TABLE_SCHEMA,TABLE_NAME,PRIVILEGE_TYPE"
    )
    expected_table_privileges = {
        (database, table, privilege, "NO")
        for table, privileges_for_table in runtime_table_privilege_map().items()
        for privilege in privileges_for_table
    }
    actual_table_privileges = {
        (str(_row(row, 0)), str(_row(row, 1)), str(_row(row, 2)), str(_row(row, 3)))
        for row in await cur.fetchall()
    }
    if actual_table_privileges != expected_table_privileges:
        raise RuntimeDbContractError("runtime database principal does not have exact table privileges")
    column_privileges = await privileges("COLUMN_PRIVILEGES")
    if column_privileges:
        raise RuntimeDbContractError("runtime database principal has column-specific privileges")
    # MariaDB has no INFORMATION_SCHEMA.ROUTINE_PRIVILEGES relation, and a
    # DML-only principal cannot read mysql.procs_priv. ROUTINES only exposes
    # routines visible to the effective account, so any visible routine is an
    # extra capability and fails the exact-DML contract.
    await cur.execute(
        "SELECT ROUTINE_NAME,ROUTINE_TYPE FROM information_schema.ROUTINES "
        "WHERE ROUTINE_SCHEMA IS NOT NULL"
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
                    # The runtime ACL denies this before MariaDB evaluates the
                    # append-only trigger.  Trigger body attestation belongs
                    # to the migration principal, which can read it without
                    # granting the web process DDL-adjacent privileges.
                    if getattr(exc, "args", (None,))[0] != 1142:
                        raise RuntimeDbContractError("Office audit ACL did not reject canonically") from exc
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
                    if getattr(error, "args", (None,))[0] != 1142:
                        raise RuntimeDbContractError("Office MFA ACL did not reject canonically") from error
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

    The web principal may append a contract-valid ``DENIED`` row in its
    transaction, but its ACL must deny both later mutations.  It never
    persists fabricated MFA evidence.
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
                        if getattr(error, "args", (None,))[0] != 1142:
                            raise RuntimeDbContractError("Office MFA ACL did not reject canonically") from error
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


async def _bootstrap_snapshot(cur):
    await cur.execute(
        "SELECT ceremony_key,target_user_id,identity_actor_user_id,identity_proof_event_id,"
        "credential_audit_id,basis_ref,completed_at FROM office_bootstrap_state "
        "WHERE ceremony_key=%s FOR UPDATE",
        (BOOTSTRAP_KEY,),
    )
    return await cur.fetchone()


async def _insert_rolled_back_bootstrap_canary(cur):
    """Build a contract-valid but denied bootstrap row within this rollback.

    It is only used when no real ceremony exists.  Its account, audit and MFA
    proof are inert, all lie in the same transaction, and never survive it.
    """
    await cur.execute("SELECT tenant_id FROM jax_tenants WHERE status='active' ORDER BY tenant_id LIMIT 1")
    tenant = await cur.fetchone()
    created_tenant = False
    if tenant is None:
        # Startup may run before the application seed.  The transaction-local
        # tenant is inert and is verified absent after rollback together with
        # the rest of this canary; it never establishes Office authority.
        await cur.execute(
            "INSERT INTO jax_tenants (name,plan,status) "
            "VALUES (CONCAT('office-bootstrap-contract-',REPLACE(UUID(),'-','')),'test','active')"
        )
        tenant = (cur.lastrowid,)
        created_tenant = True
    basis_ref = "runtime-contract-" + secrets.token_hex(16)
    await cur.execute(
        "INSERT INTO jax_users (tenant_id,email,password_hash) "
        "VALUES (%s,CONCAT('office-bootstrap-contract-',REPLACE(UUID(),'-',''),'@invalid.local'),'!')",
        (_row(tenant, 0),),
    )
    user_id = cur.lastrowid
    await cur.execute(
        "INSERT INTO user_admin_audit (ts,actor_user_id,target_user_id,action) "
        "VALUES (UTC_TIMESTAMP(6),%s,%s,'sessions_revoked')",
        (user_id, user_id),
    )
    audit_id = cur.lastrowid
    await cur.execute(
        "INSERT INTO office_mfa_challenge "
        "(token_hash,user_id,purpose,expected_token_version,expected_mfa_version,max_attempts,expires_at,state,created_at) "
        "VALUES (UNHEX(SHA2(UUID(),256)),%s,'LOGIN',0,0,1,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 MINUTE),'ISSUED',UTC_TIMESTAMP(6))",
        (user_id,),
    )
    challenge_id = cur.lastrowid
    await cur.execute(
        "INSERT INTO office_mfa_security_event "
        "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,"
        "token_version,mfa_version,result,server_correlation_id,occurred_at) "
        "VALUES ('MFA_PROOF',NULL,'PASSWORD_PREAUTH',%s,%s,'MFA_PROOF','LOGIN',0,0,'DENIED',"
        "UNHEX(REPLACE(UUID(),'-','')),UTC_TIMESTAMP(6))",
        (challenge_id, user_id),
    )
    proof_id = cur.lastrowid
    await cur.execute(
        "INSERT INTO office_bootstrap_state "
        "(ceremony_key,target_user_id,identity_actor_user_id,identity_proof_event_id,credential_audit_id,basis_ref,completed_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))",
        (BOOTSTRAP_KEY, user_id, user_id, proof_id, audit_id, basis_ref),
    )
    return {
        "tenant_id": _row(tenant, 0), "created_tenant": created_tenant,
        "user_id": user_id, "audit_id": audit_id, "challenge_id": challenge_id,
        "proof_id": proof_id, "basis_ref": basis_ref,
    }


async def _bootstrap_append_only_probe(pool) -> None:
    """Exercise both bootstrap mutations as the exact DML runtime principal."""
    initial = None
    for attempt in range(2):
        retry_duplicate = False
        async with pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor() as cur:
                    original = await _bootstrap_snapshot(cur)
                    before = original
                    fixture = None
                    if before is None:
                        try:
                            fixture = await _insert_rolled_back_bootstrap_canary(cur)
                        except aiomysql.IntegrityError as error:
                            if getattr(error, "args", (None,))[0] == 1062 and attempt == 0:
                                retry_duplicate = True
                            else:
                                raise RuntimeDbContractError("Office bootstrap canary could not be initialized") from error
                        if retry_duplicate:
                            continue
                        before = await _bootstrap_snapshot(cur)
                    if before is None:
                        raise RuntimeDbContractError("Office bootstrap canary is absent")
                    initial = original
                    for statement in (
                        "UPDATE office_bootstrap_state SET completed_at=DATE_ADD(completed_at,INTERVAL 1 MICROSECOND) WHERE ceremony_key=%s",
                        "DELETE FROM office_bootstrap_state WHERE ceremony_key=%s",
                    ):
                        try:
                            await cur.execute(statement, (BOOTSTRAP_KEY,))
                        except aiomysql.OperationalError as error:
                            if getattr(error, "args", (None,))[0] != 1142:
                                raise RuntimeDbContractError("Office bootstrap ACL did not reject canonically") from error
                        else:
                            raise RuntimeDbContractError("Office bootstrap trigger permitted a mutation")
                        if await _bootstrap_snapshot(cur) != before:
                            raise RuntimeDbContractError("Office bootstrap state changed during rejected probe")
            finally:
                # No bootstrap canary, temporary identity, or lock survives a
                # contract check, including cancellation/BaseException.
                await conn.rollback()
        if retry_duplicate:
            continue
        async with pool.acquire() as reread_conn:
            async with reread_conn.cursor() as reread_cur:
                await reread_cur.execute(
                    "SELECT ceremony_key,target_user_id,identity_actor_user_id,identity_proof_event_id,"
                    "credential_audit_id,basis_ref,completed_at FROM office_bootstrap_state WHERE ceremony_key=%s",
                    (BOOTSTRAP_KEY,),
                )
                if await reread_cur.fetchone() != initial:
                    raise RuntimeDbContractError("Office bootstrap state changed after probe rollback")
                if fixture is not None:
                    checks = (
                        ("jax_users", "user_id", fixture["user_id"]),
                        ("user_admin_audit", "id", fixture["audit_id"]),
                        ("office_mfa_challenge", "challenge_id", fixture["challenge_id"]),
                        ("office_mfa_security_event", "event_id", fixture["proof_id"]),
                        ("office_bootstrap_state", "basis_ref", fixture["basis_ref"]),
                    )
                    if fixture["created_tenant"]:
                        checks += (("jax_tenants", "tenant_id", fixture["tenant_id"]),)
                    for table, column, value in checks:
                        await reread_cur.execute(
                            "SELECT 1 FROM " + table + " WHERE " + column + "=%s LIMIT 1",
                            (value,),
                        )
                        if await reread_cur.fetchone() is not None:
                            raise RuntimeDbContractError("Office bootstrap canary survived probe rollback")
        return
    raise RuntimeDbContractError("Office bootstrap canary contention did not settle")


async def _bootstrap_privilege_denied_probe(pool) -> None:
    """Prove the ACL denies writes before trigger behavior is considered.

    ``office_bootstrap_state`` can be empty during the first ceremony, so the
    predicate deliberately matches no row.  MariaDB still checks UPDATE and
    DELETE privilege before evaluating it; only errno 1142 proves that the
    append-only table did not accidentally receive a table grant.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for statement in (
                "UPDATE office_bootstrap_state SET completed_at=completed_at WHERE ceremony_key='__runtime_acl_probe__'",
                "DELETE FROM office_bootstrap_state WHERE ceremony_key='__runtime_acl_probe__'",
            ):
                try:
                    await cur.execute(statement)
                except aiomysql.OperationalError as error:
                    if getattr(error, "args", (None,))[0] != 1142:
                        raise RuntimeDbContractError("Office bootstrap ACL did not reject canonically") from error
                else:
                    raise RuntimeDbContractError("Office bootstrap ACL permitted a mutation")


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
            await _membership_receipt_metadata(cur)
            await _profile_revocation_metadata(cur)
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
    await _bootstrap_append_only_probe(pool)
    await _bootstrap_privilege_denied_probe(pool)
