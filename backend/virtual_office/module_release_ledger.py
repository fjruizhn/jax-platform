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
    "origin_project_id INT NOT NULL,origin_tenant_id INT NOT NULL,"
    "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_candidate_identity (module_id,module_version),"
    "KEY idx_office_module_candidate_origin (origin_project_id,origin_tenant_id,candidate_id),"
    "CONSTRAINT fk_office_module_candidate_origin_scope FOREIGN KEY (origin_project_id,origin_tenant_id) REFERENCES jax_project_scope(project_id,tenant_id),"
    "CONSTRAINT chk_office_module_candidate_identity CHECK (module_id<>'' AND module_version<>'')"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_candidate_revision ("
    "candidate_revision_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,"
    "candidate_id BIGINT UNSIGNED NOT NULL,revision_no INT UNSIGNED NOT NULL,"
    # Candidate payloads are deliberately bounded to 16 MiB.  They are review
    # inputs, not an artifact transport; larger payloads belong in object
    # storage and are referenced by a later reviewed contract.
    "manifest_sha256 BINARY(32) NOT NULL,manifest_bytes MEDIUMBLOB NOT NULL,"
    "claimed_artifact_set_digest BINARY(32) NOT NULL,"
    "claimed_signing_key_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_candidate_revision_no (candidate_id,revision_no),"
    "UNIQUE KEY uq_office_module_candidate_revision_identity (candidate_revision_id,candidate_id),"
    # Shared with publication staging: candidate attestation must recognize
    # this exact identity index on every subsequent migration pass.
    "UNIQUE KEY uq_office_module_candidate_revision_release_identity (candidate_revision_id,candidate_id,manifest_sha256,claimed_artifact_set_digest),"
    "KEY idx_office_module_candidate_revision_artifact (candidate_id,claimed_artifact_set_digest),"
    "CONSTRAINT fk_office_module_candidate_revision_candidate FOREIGN KEY (candidate_id) REFERENCES office_module_candidate(candidate_id),"
    "CONSTRAINT chk_office_module_candidate_revision_payload CHECK (revision_no>0 AND OCTET_LENGTH(manifest_sha256)=32 AND manifest_sha256=UNHEX(SHA2(manifest_bytes,256)) AND OCTET_LENGTH(manifest_bytes)>0 AND OCTET_LENGTH(manifest_bytes)<=16777215 AND OCTET_LENGTH(claimed_artifact_set_digest)=32 AND claimed_signing_key_id<>'')"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_candidate_evidence ("
    "candidate_evidence_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,"
    "candidate_revision_id BIGINT UNSIGNED NOT NULL,candidate_id BIGINT UNSIGNED NOT NULL,"
    "evidence_kind VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "evidence_sha256 BINARY(32) NOT NULL,evidence_bytes MEDIUMBLOB NOT NULL,"
    "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_candidate_evidence_entry (candidate_revision_id,evidence_kind,evidence_sha256),"
    "KEY idx_office_module_candidate_evidence_revision (candidate_revision_id,evidence_kind,candidate_evidence_id),"
    "KEY idx_office_module_candidate_evidence_revision_candidate (candidate_revision_id,candidate_id),"
    "CONSTRAINT fk_office_module_candidate_evidence_revision FOREIGN KEY (candidate_revision_id,candidate_id) REFERENCES office_module_candidate_revision(candidate_revision_id,candidate_id),"
    "CONSTRAINT chk_office_module_candidate_evidence_payload CHECK (evidence_kind<>'' AND OCTET_LENGTH(evidence_sha256)=32 AND evidence_sha256=UNHEX(SHA2(evidence_bytes,256)) AND OCTET_LENGTH(evidence_bytes)>0 AND OCTET_LENGTH(evidence_bytes)<=16777215)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
)

_TABLE_COLUMNS = {
    "office_module_candidate": ("candidate_id", "module_id", "module_version", "origin_project_id", "origin_tenant_id", "created_at"),
    "office_module_candidate_revision": ("candidate_revision_id", "candidate_id", "revision_no", "manifest_sha256", "manifest_bytes", "claimed_artifact_set_digest", "claimed_signing_key_id", "created_at"),
    "office_module_candidate_evidence": ("candidate_evidence_id", "candidate_revision_id", "candidate_id", "evidence_kind", "evidence_sha256", "evidence_bytes", "created_at"),
}
_COLUMN_SHAPES = {
    "office_module_candidate": (
        ("bigint(20) unsigned", "NO", None, "auto_increment", None, None),
        ("varchar(96)", "NO", None, "", "ascii", "ascii_bin"),
        ("varchar(64)", "NO", None, "", "ascii", "ascii_bin"),
        ("int(11)", "NO", None, "", None, None),
        ("int(11)", "NO", None, "", None, None),
        ("datetime(6)", "NO", "current_timestamp(6)", "", None, None),
    ),
    "office_module_candidate_revision": (
        ("bigint(20) unsigned", "NO", None, "auto_increment", None, None),
        ("bigint(20) unsigned", "NO", None, "", None, None),
        ("int(10) unsigned", "NO", None, "", None, None),
        ("binary(32)", "NO", None, "", None, None),
        ("mediumblob", "NO", None, "", None, None),
        ("binary(32)", "NO", None, "", None, None),
        ("varchar(128)", "NO", None, "", "ascii", "ascii_bin"),
        ("datetime(6)", "NO", "current_timestamp(6)", "", None, None),
    ),
    "office_module_candidate_evidence": (
        ("bigint(20) unsigned", "NO", None, "auto_increment", None, None),
        ("bigint(20) unsigned", "NO", None, "", None, None),
        ("bigint(20) unsigned", "NO", None, "", None, None),
        ("varchar(64)", "NO", None, "", "ascii", "ascii_bin"),
        ("binary(32)", "NO", None, "", None, None),
        ("mediumblob", "NO", None, "", None, None),
        ("datetime(6)", "NO", "current_timestamp(6)", "", None, None),
    ),
}
_INDEXES = {
    ("office_module_candidate", "PRIMARY"): (True, ("candidate_id",)),
    ("office_module_candidate", "uq_office_module_candidate_identity"): (True, ("module_id", "module_version")),
    ("office_module_candidate", "idx_office_module_candidate_origin"): (False, ("origin_project_id", "origin_tenant_id", "candidate_id")),
    ("office_module_candidate_revision", "PRIMARY"): (True, ("candidate_revision_id",)),
    ("office_module_candidate_revision", "uq_office_module_candidate_revision_no"): (True, ("candidate_id", "revision_no")),
    ("office_module_candidate_revision", "uq_office_module_candidate_revision_identity"): (True, ("candidate_revision_id", "candidate_id")),
    ("office_module_candidate_revision", "uq_office_module_candidate_revision_release_identity"): (True, ("candidate_revision_id", "candidate_id", "manifest_sha256", "claimed_artifact_set_digest")),
    ("office_module_candidate_revision", "idx_office_module_candidate_revision_artifact"): (False, ("candidate_id", "claimed_artifact_set_digest")),
    ("office_module_candidate_evidence", "PRIMARY"): (True, ("candidate_evidence_id",)),
    ("office_module_candidate_evidence", "uq_office_module_candidate_evidence_entry"): (True, ("candidate_revision_id", "evidence_kind", "evidence_sha256")),
    ("office_module_candidate_evidence", "idx_office_module_candidate_evidence_revision"): (False, ("candidate_revision_id", "evidence_kind", "candidate_evidence_id")),
    ("office_module_candidate_evidence", "idx_office_module_candidate_evidence_revision_candidate"): (False, ("candidate_revision_id", "candidate_id")),
}
_FOREIGN_KEYS = {
    "fk_office_module_candidate_origin_scope": ("office_module_candidate", ("origin_project_id", "origin_tenant_id"), "jax_project_scope", ("project_id", "tenant_id")),
    "fk_office_module_candidate_revision_candidate": ("office_module_candidate_revision", ("candidate_id",), "office_module_candidate", ("candidate_id",)),
    "fk_office_module_candidate_evidence_revision": ("office_module_candidate_evidence", ("candidate_revision_id", "candidate_id"), "office_module_candidate_revision", ("candidate_revision_id", "candidate_id")),
}

_PREDECESSOR_CANDIDATE_COLUMNS = ("candidate_id", "module_id", "module_version", "created_at")
_PREDECESSOR_EVIDENCE_FK_INDEX = "fk_office_module_candidate_evidence_revision"
_RELEASE_IDENTITY_INDEX = ("office_module_candidate_revision", "uq_office_module_candidate_revision_release_identity")


async def _index_set(cur) -> dict[tuple[str, str], tuple[bool, tuple[str, ...]]]:
    await cur.execute(
        "SELECT TABLE_NAME,INDEX_NAME,NON_UNIQUE,COLUMN_NAME "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME IN ('office_module_candidate','office_module_candidate_revision','office_module_candidate_evidence') "
        "ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX"
    )
    found: dict[tuple[str, str], tuple[bool, tuple[str, ...]]] = {}
    for table, index, non_unique, column in await cur.fetchall():
        key = (str(table), str(index))
        unique, columns = found.get(key, (not bool(non_unique), ()))
        if unique != (not bool(non_unique)):
            raise RuntimeError("Office module candidate index set is invalid")
        found[key] = (unique, (*columns, str(column)))
    return found


async def _canonicalize_evidence_foreign_key_index(cur) -> None:
    """Replace only the exact v13 implicit FK index with the named v15 one."""
    named = ("office_module_candidate_evidence", "idx_office_module_candidate_evidence_revision_candidate")
    legacy = ("office_module_candidate_evidence", _PREDECESSOR_EVIDENCE_FK_INDEX)
    found = await _index_set(cur)
    if named in found:
        return
    expected_legacy = dict(_INDEXES)
    expected_legacy.pop(named)
    expected_legacy[legacy] = (False, ("candidate_revision_id", "candidate_id"))
    if found != expected_legacy:
        return
    # This is a narrowly defined interrupted-v13 state, not a repair path.
    # Before replacing its implicit index, attest every other table invariant,
    # including the exact foreign-key target and RESTRICT actions.  Otherwise
    # an attacker could pair the familiar implicit index with a substituted FK
    # and have this v15 migration normalize it into a trusted schema.
    await assert_module_candidate_storage_metadata(
        cur, expected_indexes_override=expected_legacy)
    await cur.execute(
        "ALTER TABLE office_module_candidate_evidence "
        "DROP FOREIGN KEY fk_office_module_candidate_evidence_revision,"
        "DROP INDEX fk_office_module_candidate_evidence_revision,"
        "ADD KEY idx_office_module_candidate_evidence_revision_candidate (candidate_revision_id,candidate_id),"
        "ADD CONSTRAINT fk_office_module_candidate_evidence_revision "
        "FOREIGN KEY (candidate_revision_id,candidate_id) "
        "REFERENCES office_module_candidate_revision(candidate_revision_id,candidate_id)"
    )


async def _candidate_table_columns(cur) -> tuple[str, ...]:
    await cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_module_candidate' "
        "ORDER BY ORDINAL_POSITION"
    )
    return tuple(str(row[0]) for row in await cur.fetchall())


async def _table_is_empty(cur, table: str) -> bool:
    await cur.execute(f"SELECT 1 FROM `{table}` LIMIT 1")
    return await cur.fetchone() is None


async def _upgrade_empty_candidate_predecessor(cur) -> None:
    """Add origin binding only to the exact, entirely empty v13 candidate set."""
    present = {table: await _table_exists(cur, table) for table in _APPEND_ONLY}
    if not any(present.values()):
        return
    if not all(present.values()):
        raise RuntimeError("Office module candidate predecessor has a partial table set")
    columns = await _candidate_table_columns(cur)
    if columns == _TABLE_COLUMNS["office_module_candidate"]:
        return
    if columns != _PREDECESSOR_CANDIDATE_COLUMNS:
        raise RuntimeError("Office module candidate predecessor shape requires an explicit data-preserving migration")
    if not all([await _table_is_empty(cur, table) for table in _APPEND_ONLY]):
        raise RuntimeError("Office module candidate predecessor rows require an explicit origin-project mapping")
    # The old child tables must be an exact, attestable predecessor.  Creating
    # a replacement candidate table around an unknown child shape would turn a
    # schema drift into an unreviewed data migration.
    expected_indexes = dict(_INDEXES)
    expected_indexes.pop(("office_module_candidate", "idx_office_module_candidate_origin"))
    expected_indexes.pop(("office_module_candidate_evidence", "idx_office_module_candidate_evidence_revision_candidate"))
    expected_indexes[("office_module_candidate_evidence", _PREDECESSOR_EVIDENCE_FK_INDEX)] = (
        False, ("candidate_revision_id", "candidate_id"))
    if _RELEASE_IDENTITY_INDEX not in await _index_set(cur):
        expected_indexes.pop(_RELEASE_IDENTITY_INDEX)
    await assert_module_candidate_storage_metadata(
        cur, expected_candidate_columns=_PREDECESSOR_CANDIDATE_COLUMNS,
        expected_indexes_override=expected_indexes)
    await cur.execute(
        "ALTER TABLE office_module_candidate "
        "ADD COLUMN origin_project_id INT NOT NULL AFTER module_version,"
        "ADD COLUMN origin_tenant_id INT NOT NULL AFTER origin_project_id,"
        "ADD KEY idx_office_module_candidate_origin (origin_project_id,origin_tenant_id,candidate_id),"
        "ADD CONSTRAINT fk_office_module_candidate_origin_scope "
        "FOREIGN KEY (origin_project_id,origin_tenant_id) "
        "REFERENCES jax_project_scope(project_id,tenant_id)"
    )


async def _upgrade_missing_release_identity_index(cur) -> None:
    """Resume only the exact empty #231 cutpoint before staging added its key."""
    found = await _index_set(cur)
    if _RELEASE_IDENTITY_INDEX in found:
        return
    if not all([await _table_is_empty(cur, table) for table in _APPEND_ONLY]):
        raise RuntimeError("Office module candidate interrupted predecessor has rows")
    expected = dict(_INDEXES)
    expected.pop(_RELEASE_IDENTITY_INDEX)
    named = ("office_module_candidate_evidence", "idx_office_module_candidate_evidence_revision_candidate")
    implicit = ("office_module_candidate_evidence", _PREDECESSOR_EVIDENCE_FK_INDEX)
    if implicit in found:
        expected.pop(named)
        expected[implicit] = (False, ("candidate_revision_id", "candidate_id"))
    # The index set, all columns, FKs, checks, triggers and engine are attested
    # before an ALTER can convert a partial or substituted table into v15.
    await assert_module_candidate_storage_metadata(cur, expected_indexes_override=expected)
    await cur.execute(
        "ALTER TABLE office_module_candidate_revision "
        "ADD UNIQUE KEY uq_office_module_candidate_revision_release_identity "
        "(candidate_revision_id,candidate_id,manifest_sha256,claimed_artifact_set_digest)"
    )
_CHECKS = {
    "chk_office_module_candidate_identity", "chk_office_module_candidate_revision_payload",
    "chk_office_module_candidate_evidence_payload",
}
_CHECK_CLAUSES = {
    "chk_office_module_candidate_identity": "`module_id` <> '' and `module_version` <> ''",
    "chk_office_module_candidate_revision_payload": "`revision_no` > 0 and octet_length(`manifest_sha256`) = 32 and `manifest_sha256` = unhex(sha2(`manifest_bytes`,256)) and octet_length(`manifest_bytes`) > 0 and octet_length(`manifest_bytes`) <= 16777215 and octet_length(`claimed_artifact_set_digest`) = 32 and `claimed_signing_key_id` <> ''",
    "chk_office_module_candidate_evidence_payload": "`evidence_kind` <> '' and octet_length(`evidence_sha256`) = 32 and `evidence_sha256` = unhex(sha2(`evidence_bytes`,256)) and octet_length(`evidence_bytes`) > 0 and octet_length(`evidence_bytes`) <= 16777215",
}


async def _table_exists(cur, table: str) -> bool:
    await cur.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
    return await cur.fetchone() is not None


async def apply_module_candidate_storage_migrations(cur) -> None:
    """Install inert candidate storage and attest it before readiness is stamped."""
    # The old release-ledger spike never reached a supported migration path.
    # Its names may also belong to another incomplete deployment, so this
    # migrator never deletes or reshapes them.  An operator must provide a
    # separately reviewed, data-preserving migration if any are present.
    for table in _LEGACY_TABLES:
        if not await _table_exists(cur, table):
            continue
        # The dormant v1 staging head deliberately reuses this one historic
        # name.  Shape-check just enough here to retain the candidate
        # migrator's legacy fail-closed property; its full attestation lives
        # with the staging schema after this function returns.
        if table == "office_module_release":
            await cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
            columns = {row[0] for row in await cur.fetchall()}
            if columns == {"candidate_id", "head_version", "release_event_id", "candidate_revision_id", "manifest_sha256", "artifact_set_digest", "status"}:
                continue
        raise RuntimeError("Office module legacy tables require an explicit data-preserving migration")
    await _upgrade_empty_candidate_predecessor(cur)
    for statement in MODULE_CANDIDATE_DDL:
        await cur.execute(statement)
    await _upgrade_missing_release_identity_index(cur)
    await _canonicalize_evidence_foreign_key_index(cur)
    for table in _APPEND_ONLY:
        for operation in ("UPDATE", "DELETE"):
            trigger = f"no_{operation.lower()}_{table}"
            await cur.execute(
                f"CREATE TRIGGER IF NOT EXISTS `{trigger}` BEFORE {operation} ON `{table}` "
                f"FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'"
            )
    await assert_module_candidate_storage_metadata(cur)


async def assert_module_candidate_storage_metadata(
    cur, *, allow_redacted_trigger_body: bool = False,
    expected_candidate_columns: tuple[str, ...] | None = None,
    expected_indexes_override: dict[tuple[str, str], tuple[bool, tuple[str, ...]]] | None = None,
) -> None:
    """Fail closed on table, index, FK, CHECK, or trigger substitution."""
    expected_columns = dict(_TABLE_COLUMNS)
    expected_shapes = dict(_COLUMN_SHAPES)
    expected_indexes = dict(_INDEXES)
    expected_foreign_keys = dict(_FOREIGN_KEYS)
    if expected_candidate_columns is not None:
        if expected_candidate_columns != _PREDECESSOR_CANDIDATE_COLUMNS:
            raise ValueError("unknown Office module candidate predecessor")
        expected_columns["office_module_candidate"] = expected_candidate_columns
        expected_shapes["office_module_candidate"] = _COLUMN_SHAPES["office_module_candidate"][:3] + _COLUMN_SHAPES["office_module_candidate"][5:]
        expected_indexes.pop(("office_module_candidate", "idx_office_module_candidate_origin"))
        expected_indexes.pop(("office_module_candidate_evidence", "idx_office_module_candidate_evidence_revision_candidate"))
        expected_indexes[("office_module_candidate_evidence", _PREDECESSOR_EVIDENCE_FK_INDEX)] = (False, ("candidate_revision_id", "candidate_id"))
        expected_foreign_keys.pop("fk_office_module_candidate_origin_scope")
    if expected_indexes_override is not None:
        expected_indexes = dict(expected_indexes_override)
    for table, expected in expected_columns.items():
        await cur.execute("SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA,CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (table,))
        rows = await cur.fetchall()
        if tuple(row[0] for row in rows) != expected or tuple(tuple(row[1:]) for row in rows) != expected_shapes[table]:
            raise RuntimeError(f"Office module candidate columns for {table} are invalid")
        await cur.execute("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
        if tuple(await cur.fetchone() or ()) != ("InnoDB",):
            raise RuntimeError(f"Office module candidate engine for {table} is invalid")
    for (table, index), (unique, columns) in expected_indexes.items():
        await cur.execute(
            "SELECT NON_UNIQUE,COLUMN_NAME,SUB_PART,INDEX_TYPE,COLLATION,IGNORED "
            "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME=%s AND INDEX_NAME=%s ORDER BY SEQ_IN_INDEX",
            (table, index),
        )
        rows = await cur.fetchall()
        if (
            not rows
            or any(int(row[0]) != int(not unique) for row in rows)
            or tuple(row[1] for row in rows) != columns
            or any(row[2] is not None or str(row[3]).upper() != "BTREE" or row[4] != "A" or str(row[5]).upper() != "NO" for row in rows)
        ):
            raise RuntimeError(f"Office module candidate index {table}.{index} is invalid")
    found_indexes = await _index_set(cur)
    if found_indexes != expected_indexes:
        raise RuntimeError("Office module candidate index set is invalid")
    await cur.execute("SELECT tc.TABLE_NAME,tc.CONSTRAINT_NAME,cc.CHECK_CLAUSE FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME LIKE 'office_module_candidate%' AND tc.CONSTRAINT_TYPE='CHECK'")
    rows = await cur.fetchall()
    expected_check_tables = {
        "chk_office_module_candidate_identity": "office_module_candidate",
        "chk_office_module_candidate_revision_payload": "office_module_candidate_revision",
        "chk_office_module_candidate_evidence_payload": "office_module_candidate_evidence",
    }
    if {str(row[1]) for row in rows} != _CHECKS or {str(row[1]): str(row[0]) for row in rows} != expected_check_tables:
        raise RuntimeError("Office module candidate checks are invalid")
    found_checks = {str(row[1]): str(row[2]).lower() for row in rows}
    if found_checks != _CHECK_CLAUSES:
        raise RuntimeError("Office module candidate CHECK expressions are invalid")
    await cur.execute("SELECT DATABASE()")
    schema = (await cur.fetchone())[0]
    found_foreign_keys = set()
    for name, (table, local, referenced_table, referenced) in expected_foreign_keys.items():
        await cur.execute("SELECT k.COLUMN_NAME,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,r.UPDATE_RULE,r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.TABLE_NAME=k.TABLE_NAME WHERE k.CONSTRAINT_SCHEMA=DATABASE() AND k.TABLE_NAME=%s AND k.CONSTRAINT_NAME=%s ORDER BY k.ORDINAL_POSITION", (table, name))
        rows = await cur.fetchall()
        await cur.execute("SELECT REFERENCED_TABLE_SCHEMA FROM information_schema.KEY_COLUMN_USAGE WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND CONSTRAINT_NAME=%s LIMIT 1", (table, name))
        referenced_schema = await cur.fetchone()
        if not rows or tuple(row[0] for row in rows) != local or rows[0][1] != referenced_table or tuple(row[2] for row in rows) != referenced or (rows[0][3], rows[0][4]) != ("RESTRICT", "RESTRICT") or referenced_schema is None or referenced_schema[0] != schema:
            raise RuntimeError(f"Office module candidate foreign key {name} is invalid")
        found_foreign_keys.add((table, name))
    await cur.execute("SELECT TABLE_NAME,CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME LIKE 'office_module_candidate%' AND REFERENCED_TABLE_NAME IS NOT NULL")
    if {(row[0], row[1]) for row in await cur.fetchall()} != found_foreign_keys:
        raise RuntimeError("Office module candidate foreign-key set is invalid")
    for table in _APPEND_ONLY:
        await cur.execute("SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE=%s", (table,))
        found = {row[0]: tuple(row[1:]) for row in await cur.fetchall()}
        expected = {
            f"no_update_{table}": (table, "BEFORE", "UPDATE", "ROW", f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'"),
            f"no_delete_{table}": (table, "BEFORE", "DELETE", "ROW", f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'"),
        }
        if allow_redacted_trigger_body:
            expected = {
                name: (*metadata[:-1], None if found.get(name, (None,))[-1] is None else metadata[-1])
                for name, metadata in expected.items()
            }
        if found != expected:
            raise RuntimeError(f"Office module candidate triggers for {table} are invalid")
