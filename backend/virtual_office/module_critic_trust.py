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
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re


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
        ("key_id", "varchar(128)", "NO"), ("principal_ref", "varchar(128)", "NO"),
        ("purpose", "varchar(32)", "NO"), ("algorithm", "varchar(16)", "NO"),
        ("public_key", "binary(32)", "NO"), ("not_before", "datetime(6)", "NO"),
        ("not_after", "datetime(6)", "YES"), ("created_at", "datetime(6)", "NO"),
    ),
    "office_trusted_critic_key_revocation": (
        ("key_id", "varchar(128)", "NO"), ("revoked_at", "datetime(6)", "NO"),
        ("recorded_at", "datetime(6)", "NO"),
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
    await cur.execute(
        "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (table,)
    )
    found = tuple(await cur.fetchall())
    expected = _TABLE_COLUMNS[table]
    predecessor = tuple(
        (*column[:2], "NO") if column[0] == "not_after" else column
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


async def _assert_critic_trust_anchor_metadata(
    cur, *, expected_columns, expected_clauses, tables=None, allow_trigger_prefix: bool = False,
) -> None:
    """Attest an exact schema generation; callers choose the known generation."""
    tables = tuple(expected_columns) if tables is None else tuple(tables)
    for table in tables:
        expected = expected_columns[table]
        await cur.execute(
            "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (table,)
        )
        if tuple(await cur.fetchall()) != expected:
            raise RuntimeError(f"Office critic trust columns for {table} are invalid")
        await cur.execute(
            "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,)
        )
        if tuple(await cur.fetchone() or ()) != ("InnoDB",):
            raise RuntimeError(f"Office critic trust engine for {table} is invalid")
    for (table, index), (unique, columns) in _INDEXES.items():
        if table not in tables:
            continue
        await cur.execute(
            "SELECT NON_UNIQUE,COLUMN_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s ORDER BY SEQ_IN_INDEX", (table, index)
        )
        rows = await cur.fetchall()
        if not rows or any(int(row[0]) != int(not unique) for row in rows) or tuple(row[1] for row in rows) != columns:
            raise RuntimeError(f"Office critic trust index {table}.{index} is invalid")
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
    if "office_trusted_critic_key_revocation" in tables:
        await cur.execute(
        "SELECT k.COLUMN_NAME,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,r.UPDATE_RULE,r.DELETE_RULE "
        "FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
        "ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME "
        "AND r.TABLE_NAME=k.TABLE_NAME WHERE k.CONSTRAINT_SCHEMA=DATABASE() "
        "AND k.TABLE_NAME='office_trusted_critic_key_revocation' "
        "AND k.REFERENCED_TABLE_SCHEMA=DATABASE() "
        "AND k.CONSTRAINT_NAME='fk_office_trusted_critic_key_revocation_key'"
        )
        if tuple(await cur.fetchall()) != (("key_id", "office_trusted_critic_key", "key_id", "RESTRICT", "RESTRICT"),):
            raise RuntimeError("Office critic trust foreign key is invalid")
    found = {}
    for table in tables:
        await cur.execute(
            "SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT "
            "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE=%s", (table,)
        )
        found.update({row[0]: tuple(row[1:]) for row in await cur.fetchall()})
    definitions = _trigger_definitions(tables)
    expected = {
        name: (table, "BEFORE", operation, "ROW", statement)
        for name, table, operation, statement in definitions
    }
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
    except Exception:
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
