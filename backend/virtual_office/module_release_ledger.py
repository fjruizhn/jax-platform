"""Append-only candidate storage for future Office module review.

This is deliberately *not* a release ledger or a release gate.  It stores
untrusted candidate bytes and claims only.  Publication, approvals, rights
verdicts, effective-release state, authority references, and mutable heads
belong to later, separately reviewed contracts.
"""
from __future__ import annotations


_LEGACY_TABLES = (
    "office_module_published_event", "office_module_rights_verdict_head",
    "office_module_creative_approval_head", "office_module_rights_verdict",
    "office_module_creative_approval", "office_module_license_evidence",
    "office_module_release_revision", "office_module_release",
)
_APPEND_ONLY = (
    "office_module_candidate", "office_module_candidate_revision",
    "office_module_candidate_evidence",
)

MODULE_CANDIDATE_DDL = (
    "CREATE TABLE IF NOT EXISTS office_module_candidate ("
    "candidate_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,"
    "module_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "module_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_candidate_identity (module_id,module_version),"
    "CONSTRAINT chk_office_module_candidate_identity CHECK (module_id<>'' AND module_version<>'')"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_candidate_revision ("
    "candidate_revision_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,"
    "candidate_id BIGINT UNSIGNED NOT NULL,revision_no INT UNSIGNED NOT NULL,"
    "manifest_sha256 BINARY(32) NOT NULL,manifest_bytes LONGBLOB NOT NULL,"
    "claimed_artifact_set_digest BINARY(32) NOT NULL,"
    "claimed_signing_key_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_candidate_revision_no (candidate_id,revision_no),"
    "UNIQUE KEY uq_office_module_candidate_revision_identity (candidate_revision_id,candidate_id),"
    "KEY idx_office_module_candidate_revision_artifact (candidate_id,claimed_artifact_set_digest),"
    "CONSTRAINT fk_office_module_candidate_revision_candidate FOREIGN KEY (candidate_id) REFERENCES office_module_candidate(candidate_id),"
    "CONSTRAINT chk_office_module_candidate_revision_payload CHECK (revision_no>0 AND OCTET_LENGTH(manifest_sha256)=32 AND manifest_sha256=UNHEX(SHA2(manifest_bytes,256)) AND OCTET_LENGTH(manifest_bytes)>0 AND OCTET_LENGTH(claimed_artifact_set_digest)=32 AND claimed_signing_key_id<>'')"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_candidate_evidence ("
    "candidate_evidence_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,"
    "candidate_revision_id BIGINT UNSIGNED NOT NULL,candidate_id BIGINT UNSIGNED NOT NULL,"
    "evidence_kind VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "evidence_sha256 BINARY(32) NOT NULL,evidence_bytes LONGBLOB NOT NULL,"
    "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_candidate_evidence_entry (candidate_revision_id,evidence_kind,evidence_sha256),"
    "KEY idx_office_module_candidate_evidence_revision (candidate_revision_id,evidence_kind,candidate_evidence_id),"
    "CONSTRAINT fk_office_module_candidate_evidence_revision FOREIGN KEY (candidate_revision_id,candidate_id) REFERENCES office_module_candidate_revision(candidate_revision_id,candidate_id),"
    "CONSTRAINT chk_office_module_candidate_evidence_payload CHECK (evidence_kind<>'' AND OCTET_LENGTH(evidence_sha256)=32 AND evidence_sha256=UNHEX(SHA2(evidence_bytes,256)) AND OCTET_LENGTH(evidence_bytes)>0)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
)

_TABLE_COLUMNS = {
    "office_module_candidate": ("candidate_id", "module_id", "module_version", "created_at"),
    "office_module_candidate_revision": ("candidate_revision_id", "candidate_id", "revision_no", "manifest_sha256", "manifest_bytes", "claimed_artifact_set_digest", "claimed_signing_key_id", "created_at"),
    "office_module_candidate_evidence": ("candidate_evidence_id", "candidate_revision_id", "candidate_id", "evidence_kind", "evidence_sha256", "evidence_bytes", "created_at"),
}
_INDEXES = {
    ("office_module_candidate", "PRIMARY"): (True, ("candidate_id",)),
    ("office_module_candidate", "uq_office_module_candidate_identity"): (True, ("module_id", "module_version")),
    ("office_module_candidate_revision", "PRIMARY"): (True, ("candidate_revision_id",)),
    ("office_module_candidate_revision", "uq_office_module_candidate_revision_no"): (True, ("candidate_id", "revision_no")),
    ("office_module_candidate_revision", "uq_office_module_candidate_revision_identity"): (True, ("candidate_revision_id", "candidate_id")),
    ("office_module_candidate_revision", "idx_office_module_candidate_revision_artifact"): (False, ("candidate_id", "claimed_artifact_set_digest")),
    ("office_module_candidate_evidence", "PRIMARY"): (True, ("candidate_evidence_id",)),
    ("office_module_candidate_evidence", "uq_office_module_candidate_evidence_entry"): (True, ("candidate_revision_id", "evidence_kind", "evidence_sha256")),
    ("office_module_candidate_evidence", "idx_office_module_candidate_evidence_revision"): (False, ("candidate_revision_id", "evidence_kind", "candidate_evidence_id")),
}
_FOREIGN_KEYS = {
    "fk_office_module_candidate_revision_candidate": ("office_module_candidate_revision", ("candidate_id",), "office_module_candidate", ("candidate_id",)),
    "fk_office_module_candidate_evidence_revision": ("office_module_candidate_evidence", ("candidate_revision_id", "candidate_id"), "office_module_candidate_revision", ("candidate_revision_id", "candidate_id")),
}
_CHECKS = {
    "chk_office_module_candidate_identity", "chk_office_module_candidate_revision_payload",
    "chk_office_module_candidate_evidence_payload",
}
_CHECK_CLAUSES = {
    "chk_office_module_candidate_identity": "`module_id` <> '' and `module_version` <> ''",
    "chk_office_module_candidate_revision_payload": "`revision_no` > 0 and octet_length(`manifest_sha256`) = 32 and `manifest_sha256` = unhex(sha2(`manifest_bytes`,256)) and octet_length(`manifest_bytes`) > 0 and octet_length(`claimed_artifact_set_digest`) = 32 and `claimed_signing_key_id` <> ''",
    "chk_office_module_candidate_evidence_payload": "`evidence_kind` <> '' and octet_length(`evidence_sha256`) = 32 and `evidence_sha256` = unhex(sha2(`evidence_bytes`,256)) and octet_length(`evidence_bytes`) > 0",
}


async def _table_exists(cur, table: str) -> bool:
    await cur.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
    return await cur.fetchone() is not None


async def _remove_empty_legacy_schema(cur) -> None:
    """Remove the never-published predecessor only when it contains no rows."""
    for table in _LEGACY_TABLES:
        if await _table_exists(cur, table):
            await cur.execute(f"SELECT 1 FROM `{table}` LIMIT 1")
            if await cur.fetchone() is not None:
                raise RuntimeError("Office module predecessor contains rows; refusing destructive candidate-storage migration")
    # The predecessor had cyclic release/revision references.  It is known
    # empty at this point; disable FK ordering checks only for this bounded
    # cleanup and restore them even when one DDL statement fails.
    await cur.execute("SET FOREIGN_KEY_CHECKS=0")
    try:
        for table in _LEGACY_TABLES:
            if await _table_exists(cur, table):
                await cur.execute(f"DROP TABLE `{table}`")
    finally:
        await cur.execute("SET FOREIGN_KEY_CHECKS=1")


async def apply_module_candidate_storage_migrations(cur) -> None:
    """Install inert candidate storage and attest it before readiness is stamped."""
    if any([await _table_exists(cur, table) for table in _LEGACY_TABLES]):
        await _remove_empty_legacy_schema(cur)
    for statement in MODULE_CANDIDATE_DDL:
        await cur.execute(statement)
    for table in _APPEND_ONLY:
        for operation in ("UPDATE", "DELETE"):
            trigger = f"no_{operation.lower()}_{table}"
            await cur.execute(
                f"CREATE TRIGGER IF NOT EXISTS `{trigger}` BEFORE {operation} ON `{table}` "
                f"FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'"
            )
    await assert_module_candidate_storage_metadata(cur)


async def assert_module_candidate_storage_metadata(cur) -> None:
    """Fail closed on table, index, FK, CHECK, or trigger substitution."""
    for table, expected in _TABLE_COLUMNS.items():
        await cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (table,))
        if tuple(row[0] for row in await cur.fetchall()) != expected:
            raise RuntimeError(f"Office module candidate columns for {table} are invalid")
    for (table, index), (unique, columns) in _INDEXES.items():
        await cur.execute("SELECT NON_UNIQUE,COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s ORDER BY SEQ_IN_INDEX", (table, index))
        rows = await cur.fetchall()
        if not rows or any(int(row[0]) != int(not unique) for row in rows) or tuple(row[1] for row in rows) != columns:
            raise RuntimeError(f"Office module candidate index {table}.{index} is invalid")
    await cur.execute("SELECT CONSTRAINT_NAME FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME LIKE 'chk_office_module_candidate_%'")
    if {str(row[0]) for row in await cur.fetchall()} != _CHECKS:
        raise RuntimeError("Office module candidate checks are invalid")
    await cur.execute("SELECT CONSTRAINT_NAME,CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME LIKE 'chk_office_module_candidate_%'")
    found_checks = {str(row[0]): str(row[1]).lower() for row in await cur.fetchall()}
    if found_checks != _CHECK_CLAUSES:
        raise RuntimeError("Office module candidate CHECK expressions are invalid")
    for name, (table, local, referenced_table, referenced) in _FOREIGN_KEYS.items():
        await cur.execute("SELECT k.COLUMN_NAME,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,r.UPDATE_RULE,r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.TABLE_NAME=k.TABLE_NAME WHERE k.CONSTRAINT_SCHEMA=DATABASE() AND k.TABLE_NAME=%s AND k.CONSTRAINT_NAME=%s ORDER BY k.ORDINAL_POSITION", (table, name))
        rows = await cur.fetchall()
        if not rows or tuple(row[0] for row in rows) != local or rows[0][1] != referenced_table or tuple(row[2] for row in rows) != referenced or (rows[0][3], rows[0][4]) != ("RESTRICT", "RESTRICT"):
            raise RuntimeError(f"Office module candidate foreign key {name} is invalid")
    for table in _APPEND_ONLY:
        await cur.execute("SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE=%s", (table,))
        found = {row[0]: tuple(row[1:]) for row in await cur.fetchall()}
        expected = {
            f"no_update_{table}": (table, "BEFORE", "UPDATE", "ROW", f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'"),
            f"no_delete_{table}": (table, "BEFORE", "DELETE", "ROW", f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'"),
        }
        if found != expected:
            raise RuntimeError(f"Office module candidate triggers for {table} are invalid")
