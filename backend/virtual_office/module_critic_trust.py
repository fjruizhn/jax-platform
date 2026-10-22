"""Fail-closed trust-anchor lookup for independently signed critic receipts.

The registry is intentionally read-only here.  Key enrollment and revocation
authority are separate contracts; an empty registry trusts no critic.  The
migrator fully attests this schema before it stamps v13.  The restricted web
principal deliberately performs no post-stamp metadata attestation: MariaDB
redacts parts of that metadata, and a live guard broker is required before
receipt verification can be activated.

An interrupted first install resumes only when it is an exact, empty,
unstamped prefix of this module's DDL/trigger sequence. Any rows, stamp, or
schema divergence fails closed rather than being repaired.

DEFINER and SQL_MODE are environment-owned trigger metadata and intentionally
stay outside this v13 digest: the four canonical bodies only SIGNAL, so neither
widens their semantics. Future sensitive trigger behavior requires a versioned
creation-policy contract before it can rely on either field.

The separate v14 entry point below is intentionally not called by the generic
Office migration runner.  It consumes a descriptor-bound root contract,
invalidates the runtime marker before any DDL, then recreates and attests the
trigger environment.  It remains schema work only: it neither makes the guard
ready nor enables a receipt consumer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from base64 import urlsafe_b64encode

import aiomysql
from db_connect_config import db_connect_timeout_seconds
from virtual_office.critic_trust_activation import ActivationContract


_KEY_DDL = (
    "CREATE TABLE IF NOT EXISTS office_trusted_critic_key ("
    "key_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,"
    "principal_ref VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "purpose VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "algorithm VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "public_key BINARY(32) NOT NULL,not_before DATETIME(6) NOT NULL,not_after DATETIME(6) NULL,"
    "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_trusted_critic_key_public_key (public_key),"
    "KEY idx_office_trusted_critic_key_lookup (principal_ref,purpose,algorithm,not_before,not_after,key_id),"
    "CONSTRAINT chk_office_trusted_critic_key_shape CHECK (key_id<>'' AND principal_ref<>'' AND purpose='rights.review' AND algorithm='Ed25519' AND (not_after IS NULL OR not_before<not_after))"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_trusted_critic_key_revocation ("
    "key_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,"
    "revoked_at DATETIME(6) NOT NULL,recorded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "CONSTRAINT fk_office_trusted_critic_key_revocation_key FOREIGN KEY (key_id) REFERENCES office_trusted_critic_key(key_id),"
    "CONSTRAINT chk_office_trusted_critic_key_revocation_shape CHECK (revoked_at<=recorded_at)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
)
_APPEND_ONLY = ("office_trusted_critic_key", "office_trusted_critic_key_revocation")
_TABLE_COLUMNS = {
    "office_trusted_critic_key": (
        ("key_id", "varchar(128)", "NO", None, "ascii", "ascii_bin", ""),
        ("principal_ref", "varchar(128)", "NO", None, "ascii", "ascii_bin", ""),
        ("purpose", "varchar(32)", "NO", None, "ascii", "ascii_bin", ""),
        ("algorithm", "varchar(16)", "NO", None, "ascii", "ascii_bin", ""),
        ("public_key", "binary(32)", "NO", None, None, None, ""),
        ("not_before", "datetime(6)", "NO", None, None, None, ""),
        ("not_after", "datetime(6)", "YES", "null", None, None, ""),
        ("created_at", "datetime(6)", "NO", "current_timestamp(6)", None, None, ""),
    ),
    "office_trusted_critic_key_revocation": (
        ("key_id", "varchar(128)", "NO", None, "ascii", "ascii_bin", ""),
        ("revoked_at", "datetime(6)", "NO", None, None, None, ""),
        ("recorded_at", "datetime(6)", "NO", "current_timestamp(6)", None, None, ""),
    ),
}
_INDEXES = {
    ("office_trusted_critic_key", "PRIMARY"): (True, ("key_id",)),
    ("office_trusted_critic_key", "uq_office_trusted_critic_key_public_key"): (True, ("public_key",)),
    ("office_trusted_critic_key", "idx_office_trusted_critic_key_lookup"): (False, ("principal_ref", "purpose", "algorithm", "not_before", "not_after", "key_id")),
    ("office_trusted_critic_key_revocation", "PRIMARY"): (True, ("key_id",)),
}
_CHECKS = {
    "chk_office_trusted_critic_key_shape": "office_trusted_critic_key",
    "chk_office_trusted_critic_key_revocation_shape": "office_trusted_critic_key_revocation",
}
_WS = re.compile(r"\s+")
_CHECK_CLAUSES = {
    "chk_office_trusted_critic_key_shape": "key_id<>''andprincipal_ref<>''andpurpose='rights.review'andalgorithm='ed25519'and(not_afterisnullornot_before<not_after)",
    "chk_office_trusted_critic_key_revocation_shape": "revoked_at<=recorded_at",
}
_PREDECESSOR_KEY_CHECK = "key_id<>''andprincipal_ref<>''andpurpose='rights.review'andalgorithm='ed25519'andnot_before<not_after"


def _normalize_check(value: object) -> str:
    return _WS.sub("", str(value)).lower().replace("`", "")


def _normalize_column(row) -> tuple:
    name, column_type, nullable, default, charset, collation, extra = row
    return (
        name, column_type, nullable,
        None if default is None else str(default).lower(), charset, collation, extra,
    )


async def _column_metadata(cur, table: str) -> tuple:
    await cur.execute(
        "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,CHARACTER_SET_NAME,COLLATION_NAME,EXTRA "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s "
        "ORDER BY ORDINAL_POSITION", (table,)
    )
    return tuple(_normalize_column(row) for row in await cur.fetchall())


async def apply_critic_trust_anchor_migrations(cur) -> None:
    """Create the empty, append-only critic trust registry and attest it."""
    existing = tuple([await _table_exists(cur, table) for table in _APPEND_ONLY])
    if any(existing):
        await _resume_exact_unstamped_prefix(cur, existing)
        return
    await _create_trust_tables_and_triggers(cur)
    await assert_critic_trust_anchor_metadata(cur)


async def _table_exists(cur, table: str) -> bool:
    await cur.execute(
        "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,)
    )
    return await cur.fetchone() is not None


def _trigger_definitions(tables=_APPEND_ONLY):
    return tuple(
        (f"no_{operation.lower()}_{table}", table, operation,
         f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'")
        for table in tables for operation in ("UPDATE", "DELETE")
    )


async def _create_trust_tables_and_triggers(cur) -> None:
    for statement in _KEY_DDL:
        await cur.execute(statement)
    for name, table, operation, statement in _trigger_definitions():
        await cur.execute(
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON `{table}` "
            f"FOR EACH ROW {statement}"
        )


async def _runtime_stamp_exists(cur) -> bool:
    if not await _table_exists(cur, "office_schema_revision"):
        return False
    await cur.execute(
        "SELECT 1 FROM office_schema_revision WHERE component='virtual-office' LIMIT 1"
    )
    return await cur.fetchone() is not None


async def _assert_empty(cur, tables) -> None:
    for table in tables:
        await cur.execute(f"SELECT 1 FROM `{table}` LIMIT 1")
        if await cur.fetchone() is not None:
            raise RuntimeError("Office critic trust key history is incompatible")


async def _resume_exact_unstamped_prefix(cur, existing) -> None:
    """Complete only the measured empty, unstamped DDL/trigger prefix."""
    if existing[0] is False:
        raise RuntimeError("Office critic trust key history is incompatible")
    if await _runtime_stamp_exists(cur):
        if not all(existing):
            raise RuntimeError("Office critic trust key history is incompatible")
        await _upgrade_not_after_nullable(cur)
        await assert_critic_trust_anchor_metadata(cur)
        return
    present = tuple(table for table, exists in zip(_APPEND_ONLY, existing) if exists)
    if present not in ((_APPEND_ONLY[0],), _APPEND_ONLY):
        raise RuntimeError("Office critic trust key history is incompatible")
    if all(existing):
        await _upgrade_not_after_nullable(cur)
    await _assert_critic_trust_anchor_metadata(
        cur, expected_columns=_TABLE_COLUMNS, expected_clauses=_CHECK_CLAUSES,
        tables=present, allow_trigger_prefix=True,
    )
    await _assert_empty(cur, present)
    await _create_trust_tables_and_triggers(cur)
    await assert_critic_trust_anchor_metadata(cur)


async def _upgrade_not_after_nullable(cur) -> None:
    """Relax only the measured v1 predecessor; unknown shape stays closed."""
    table = "office_trusted_critic_key"
    found = await _column_metadata(cur, table)
    expected = _TABLE_COLUMNS[table]
    predecessor = tuple(
        (*column[:2], "NO", None, *column[4:]) if column[0] == "not_after" else column
        for column in expected
    )
    if found == expected:
        return
    if found != predecessor:
        raise RuntimeError("Office critic trust key history is incompatible")
    predecessor_checks = dict(_CHECK_CLAUSES)
    predecessor_checks["chk_office_trusted_critic_key_shape"] = _PREDECESSOR_KEY_CHECK
    await _assert_critic_trust_anchor_metadata(
        cur, expected_columns={**_TABLE_COLUMNS, table: predecessor}, expected_clauses=predecessor_checks,
    )
    # MariaDB DDL commits implicitly, so perform the nullable-column and CHECK
    # transition in one atomic ALTER.  A dropped old CHECK must never survive
    # as a separately committed intermediate schema.
    await cur.execute(
        "ALTER TABLE office_trusted_critic_key "
        "DROP CONSTRAINT chk_office_trusted_critic_key_shape, "
        "MODIFY not_after DATETIME(6) NULL, "
        "ADD CONSTRAINT chk_office_trusted_critic_key_shape CHECK "
        "(key_id<>'' AND principal_ref<>'' AND purpose='rights.review' "
        "AND algorithm='Ed25519' AND (not_after IS NULL OR not_before<not_after))"
    )


async def assert_critic_trust_anchor_metadata(cur) -> None:
    """Reject substituted trust-anchor schema before the runtime can be ready."""
    await _assert_critic_trust_anchor_metadata(
        cur, expected_columns=_TABLE_COLUMNS, expected_clauses=_CHECK_CLAUSES,
    )


_V14_COMPONENT = "virtual-office-critic-trust"
_V14_LOCK_PREFIX = "axioma.office.critic-trust.v14:"


def _v14_descriptor(contract: ActivationContract, state: str) -> str:
    """A 64-byte marker binds the exact contract profile and lifecycle state."""
    value = {
        "allowed_client_uids": contract.allowed_client_uids, "charset": contract.database_charset, "collation": contract.database_collation,
        "critic_principal": contract.critic_principal,
        "definer": [contract.trigger_definer.user, contract.trigger_definer.host],
        "generation": 14, "guard_account": [contract.guard_account.user, contract.guard_account.host],
        "guard_gid": contract.guard_gid, "guard_uid": contract.guard_uid, "limits": [contract.timeout_seconds, contract.max_inflight],
        "schema": contract.database_schema, "server_version": contract.database_server_version, "socket_path": contract.socket_path,
        "sql_mode": contract.database_sql_mode,
        "state": state,
    }
    digest = urlsafe_b64encode(hashlib.sha256(
        b"axioma.office.critic-trust.v14\x00" + json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).digest()).rstrip(b"=").decode("ascii")
    return f"critic-v14-{state}-{digest}"


async def dedicated_migrator_connect():
    """Open the standalone autocommit connection reserved for future v14 wiring."""
    return await aiomysql.connect(
        host=os.environ["JAX_CRITIC_TRUST_MIGRATOR_HOST"],
        port=int(os.environ["JAX_CRITIC_TRUST_MIGRATOR_PORT"]),
        user=os.environ["JAX_CRITIC_TRUST_MIGRATOR_USER"],
        password=os.environ["JAX_CRITIC_TRUST_MIGRATOR_PASSWORD"],
        db=os.environ["JAX_CRITIC_TRUST_MIGRATOR_DATABASE"], autocommit=True, charset="utf8mb4",
        connect_timeout=db_connect_timeout_seconds(),
    )


async def migrate_critic_trust_v14(contract: ActivationContract, *, connect=dedicated_migrator_connect) -> None:
    """Perform only the inert, contract-bound v14 migration on its own connection."""
    if type(contract) is not ActivationContract:
        raise TypeError("critic-trust v14 requires a validated ActivationContract")
    contract.assert_unchanged()
    conn = await connect()
    try:
        if conn.get_autocommit() is not True:
            raise RuntimeError("critic-trust v14 requires an autocommit connection")
        async with conn.cursor() as cur:
            await _set_v14_session(cur, contract)
            lock_name = _V14_LOCK_PREFIX + hashlib.sha256(contract.database_schema.encode("ascii")).hexdigest()[:32]
            await cur.execute("SELECT GET_LOCK(%s,%s)", (lock_name, contract.timeout_seconds))
            if tuple(await cur.fetchone() or ()) != (1,):
                raise RuntimeError("critic-trust v14 migration lock is unavailable")
            try:
                prior = await _preflight_v14(cur, contract)
                if prior == "ready":
                    await _assert_critic_trust_anchor_metadata(
                        cur, expected_columns=_TABLE_COLUMNS, expected_clauses=_CHECK_CLAUSES, v14_contract=contract,
                    )
                    return
                await _write_v14_marker(cur, contract, "invalid")
                await _set_v14_table_defaults(cur, contract)
                await _replace_v14_triggers(cur, contract)
                await _finalize_v14(cur, contract)
            finally:
                await cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                if tuple(await cur.fetchone() or ()) != (1,):
                    raise RuntimeError("critic-trust v14 migration lock was not released")
    finally:
        conn.close()


async def _set_v14_session(cur, contract: ActivationContract) -> None:
    await cur.execute("SET SESSION sql_mode=%s", (contract.database_sql_mode,))
    await cur.execute(f"SET NAMES `{contract.database_charset}` COLLATE `{contract.database_collation}`")
    await cur.execute("SET SESSION lock_wait_timeout=%s", (contract.timeout_seconds,))


async def _preflight_v14(cur, contract: ActivationContract) -> str | None:
    await cur.execute("SELECT DATABASE(),VERSION()")
    database, version = await cur.fetchone()
    if database != contract.database_schema or str(version).split("-", 1)[0] != contract.database_server_version:
        raise RuntimeError("critic-trust v14 database identity is incompatible")
    await cur.execute(
        "SELECT DEFAULT_CHARACTER_SET_NAME,DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=DATABASE()"
    )
    if tuple(await cur.fetchone() or ()) != (contract.database_charset, contract.database_collation):
        raise RuntimeError("critic-trust v14 schema defaults are incompatible")
    await cur.execute("SELECT revision FROM office_schema_revision WHERE component='virtual-office'")
    from virtual_office.migrations import RUNTIME_REVISION
    if tuple(await cur.fetchone() or ()) != (RUNTIME_REVISION,):
        raise RuntimeError("critic-trust v14 requires the exact global Office v13 marker")
    await cur.execute("SELECT revision FROM office_schema_revision WHERE component=%s", (_V14_COMPONENT,))
    marker = await cur.fetchone()
    state = None if marker is None else marker[0]
    known = {None, _v14_descriptor(contract, "invalid"), _v14_descriptor(contract, "ready")}
    if state not in known:
        raise RuntimeError("critic-trust v14 marker is unknown or belongs to another contract")
    await cur.execute("SELECT 1 FROM mysql.user WHERE User=%s AND Host=%s", (contract.trigger_definer.user, contract.trigger_definer.host))
    if await cur.fetchone() is None:
        raise RuntimeError("critic-trust v14 trigger definer is absent")
    await _assert_critic_trust_anchor_metadata(cur, expected_columns=_TABLE_COLUMNS, expected_clauses=_CHECK_CLAUSES)
    return "ready" if state == _v14_descriptor(contract, "ready") else "invalid" if state is not None else None


async def _write_v14_marker(cur, contract: ActivationContract, state: str) -> None:
    await cur.execute(
        "INSERT INTO office_schema_revision (component,revision,applied_at) VALUES (%s,%s,NOW(6)) "
        "ON DUPLICATE KEY UPDATE revision=VALUES(revision),applied_at=VALUES(applied_at)",
        (_V14_COMPONENT, _v14_descriptor(contract, state)),
    )


async def _set_v14_table_defaults(cur, contract: ActivationContract) -> None:
    for table in _APPEND_ONLY:
        await cur.execute("SELECT TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
        if tuple(await cur.fetchone() or ()) != (contract.database_collation,):
            await cur.execute(f"ALTER TABLE `{table}` DEFAULT CHARACTER SET `{contract.database_charset}` COLLATE `{contract.database_collation}`")


async def _replace_v14_triggers(cur, contract: ActivationContract) -> None:
    definer = contract.trigger_definer
    for name, table, operation, statement in _trigger_definitions():
        await cur.execute(
            f"CREATE OR REPLACE DEFINER=`{definer.user}`@`{definer.host}` TRIGGER `{name}` "
            f"BEFORE {operation} ON `{table}` FOR EACH ROW {statement}"
        )


async def _finalize_v14(cur, contract: ActivationContract) -> None:
    await cur.execute("START TRANSACTION")
    try:
        await cur.execute("SELECT revision FROM office_schema_revision WHERE component=%s FOR UPDATE", (_V14_COMPONENT,))
        if tuple(await cur.fetchone() or ()) != (_v14_descriptor(contract, "invalid"),):
            raise RuntimeError("critic-trust v14 marker changed during migration")
        await cur.execute("SELECT revision FROM office_schema_revision WHERE component='virtual-office' FOR UPDATE")
        from virtual_office.migrations import RUNTIME_REVISION
        if tuple(await cur.fetchone() or ()) != (RUNTIME_REVISION,):
            raise RuntimeError("critic-trust v14 global Office marker changed during migration")
        for table in _APPEND_ONLY:
            await cur.execute(f"SELECT 1 FROM `{table}` WHERE 0 LOCK IN SHARE MODE")
        await _preflight_v14(cur, contract)
        await _assert_critic_trust_anchor_metadata(cur, expected_columns=_TABLE_COLUMNS, expected_clauses=_CHECK_CLAUSES, v14_contract=contract)
        contract.assert_unchanged()
        await _write_v14_marker(cur, contract, "ready")
    except BaseException:
        await cur.execute("ROLLBACK")
        raise
    else:
        await cur.execute("COMMIT")


async def _assert_critic_trust_anchor_metadata(
    cur, *, expected_columns, expected_clauses, tables=None, allow_trigger_prefix: bool = False,
    include_triggers: bool = True, v14_contract=None,
) -> None:
    """Attest an exact schema generation; callers choose the known generation."""
    tables = tuple(expected_columns) if tables is None else tuple(tables)
    for table in tables:
        expected = expected_columns[table]
        if await _column_metadata(cur, table) != expected:
            raise RuntimeError(f"Office critic trust columns for {table} are invalid")
        await cur.execute(
            "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,)
        )
        if tuple(await cur.fetchone() or ()) != ("InnoDB",):
            raise RuntimeError(f"Office critic trust engine for {table} is invalid")
    for table in tables:
        await cur.execute(
            "SELECT INDEX_NAME,NON_UNIQUE,COLUMN_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY INDEX_NAME,SEQ_IN_INDEX", (table,)
        )
        found_indexes = {}
        for index, non_unique, column in await cur.fetchall():
            found_indexes.setdefault(index, (int(non_unique) == 0, []))[1].append(column)
        expected_indexes = {
            index: (unique, list(columns))
            for (index_table, index), (unique, columns) in _INDEXES.items() if index_table == table
        }
        if found_indexes != expected_indexes:
            raise RuntimeError(f"Office critic trust indexes for {table} are invalid")
    quoted_tables = ",".join(f"'{table}'" for table in tables)
    await cur.execute(
        "SELECT tc.TABLE_NAME,tc.CONSTRAINT_NAME,cc.CHECK_CLAUSE FROM information_schema.TABLE_CONSTRAINTS tc "
        "JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA "
        "AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.CONSTRAINT_SCHEMA=DATABASE() "
        f"AND tc.TABLE_NAME IN ({quoted_tables}) "
        "AND tc.CONSTRAINT_TYPE='CHECK'"
    )
    checks = await cur.fetchall()
    expected_checks = {name: table for name, table in _CHECKS.items() if table in tables}
    if {row[1]: row[0] for row in checks} != expected_checks:
        raise RuntimeError("Office critic trust checks are invalid")
    clauses = {row[1]: _normalize_check(row[2]) for row in checks}
    expected_clauses = {name: clause for name, clause in expected_clauses.items() if name in expected_checks}
    if clauses != expected_clauses:
        raise RuntimeError("Office critic trust CHECK expressions are invalid")
    await cur.execute(
        "SELECT k.TABLE_NAME,k.CONSTRAINT_NAME,k.COLUMN_NAME,k.REFERENCED_TABLE_SCHEMA,"
        "k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,r.UPDATE_RULE,r.DELETE_RULE "
        "FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
        "ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME "
        "AND r.TABLE_NAME=k.TABLE_NAME WHERE k.CONSTRAINT_SCHEMA=DATABASE() "
        f"AND k.TABLE_NAME IN ({quoted_tables}) AND k.REFERENCED_TABLE_NAME IS NOT NULL "
        "ORDER BY k.TABLE_NAME,k.CONSTRAINT_NAME,k.ORDINAL_POSITION"
    )
    foreign_keys = tuple(await cur.fetchall())
    expected_foreign_keys = ()
    if "office_trusted_critic_key_revocation" in tables:
        await cur.execute("SELECT DATABASE()")
        database = (await cur.fetchone())[0]
        expected_foreign_keys = ((
            "office_trusted_critic_key_revocation", "fk_office_trusted_critic_key_revocation_key", "key_id",
            database, "office_trusted_critic_key", "key_id", "RESTRICT", "RESTRICT",
        ),)
    if foreign_keys != expected_foreign_keys:
        raise RuntimeError("Office critic trust foreign keys are invalid")
    if not include_triggers:
        return
    found = {}
    trigger_fields = (
        "TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,"
        "ACTION_STATEMENT,ACTION_ORDER"
    )
    if v14_contract is not None:
        trigger_fields += ",DEFINER,SQL_MODE,CHARACTER_SET_CLIENT,COLLATION_CONNECTION,DATABASE_COLLATION"
    for table in tables:
        await cur.execute(
            f"SELECT {trigger_fields} FROM information_schema.TRIGGERS "
            "WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE=%s", (table,)
        )
        found.update({row[0]: tuple(row[1:]) for row in await cur.fetchall()})
    definitions = _trigger_definitions(tables)
    expected = {
        name: (table, "BEFORE", operation, "ROW", statement, 1)
        for name, table, operation, statement in definitions
    }
    if v14_contract is not None:
        await cur.execute("SELECT VERSION()")
        version = str((await cur.fetchone() or ("",))[0]).split("-", 1)[0]
        if version != v14_contract.database_server_version:
            raise RuntimeError("Office critic trust database version is incompatible")
        expected = {
            name: (*definition, f"{v14_contract.trigger_definer.user}@{v14_contract.trigger_definer.host}",
                   v14_contract.database_sql_mode, v14_contract.database_charset,
                   v14_contract.database_collation, v14_contract.database_collation)
            for name, definition in expected.items()
        }
        for table in tables:
            await cur.execute(
                "SELECT TABLE_COLLATION FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,)
            )
            if tuple(await cur.fetchone() or ()) != (v14_contract.database_collation,):
                raise RuntimeError(f"Office critic trust table collation for {table} is invalid")
    if allow_trigger_prefix:
        expected = dict(list(expected.items())[:len(found)])
    if found != expected:
        raise RuntimeError("Office critic trust triggers are invalid")


@dataclass(frozen=True)
class TrustedCriticKey:
    key_id: str
    principal_ref: str
    purpose: str
    algorithm: str
    public_key: bytes
    not_before: datetime
    not_after: datetime | None
    revoked_at: datetime | None


def _utc(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _database_utc(value: object) -> datetime | None:
    """MariaDB DATETIME is deliberately stored/read as a UTC-naive value."""
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else _utc(value)


def _valid_key(key: TrustedCriticKey) -> bool:
    return (
        isinstance(key.key_id, str) and 1 <= len(key.key_id) <= 128
        and isinstance(key.principal_ref, str) and 1 <= len(key.principal_ref) <= 128
        and key.purpose == "rights.review" and key.algorithm == "Ed25519"
        and isinstance(key.public_key, bytes) and len(key.public_key) == 32
        and (not_before := _utc(key.not_before)) is not None
        and (key.not_after is None or ((not_after := _utc(key.not_after)) is not None and not_before < not_after))
        and (key.revoked_at is None or _utc(key.revoked_at) is not None)
    )


async def resolve_trusted_critic_key(cur, key_id: str) -> TrustedCriticKey | None:
    """Load exactly one trust key and its irreversible revocation tombstone.

    Any database error, malformed row, duplicate tombstone, or unsupported
    value becomes no key.  Callers therefore fail closed.
    """
    if not isinstance(key_id, str) or not (1 <= len(key_id) <= 128):
        return None
    try:
        await cur.execute(
            "SELECT k.key_id,k.principal_ref,k.purpose,k.algorithm,k.public_key,"
            "k.not_before,k.not_after,r.revoked_at "
            "FROM office_trusted_critic_key k "
            "LEFT JOIN office_trusted_critic_key_revocation r ON r.key_id=k.key_id "
            "WHERE k.key_id=%s",
            (key_id,),
        )
        rows = await cur.fetchall()
    except Exception:  # fail-soft: an unavailable lookup yields no trusted key; callers reject it
        return None
    if len(rows) != 1:
        return None
    row = rows[0]
    try:
        values = tuple(row.values()) if isinstance(row, dict) else tuple(row)
        key = TrustedCriticKey(
            values[0], values[1], values[2], values[3], values[4],
            _database_utc(values[5]), _database_utc(values[6]), _database_utc(values[7]),
        )
    except (TypeError, ValueError):
        return None
    return key if _valid_key(key) else None
