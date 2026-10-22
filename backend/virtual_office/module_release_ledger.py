"""Physical integrity contract for Office module releases.

This module deliberately has no write service and performs no publication.
It supplies only the schema and readiness attestation consumed by the future
release gate.  The mutable rows are CAS heads; every decision and payload they
reference is append-only.
"""
from __future__ import annotations


_APPEND_ONLY = (
    "office_module_release_revision",
    "office_module_license_evidence",
    "office_module_creative_approval",
    "office_module_rights_verdict",
    "office_module_published_event",
)


MODULE_RELEASE_DDL = (
    "CREATE TABLE IF NOT EXISTS office_module_release ("
    "release_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,"
    "module_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "module_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "created_by_user_id INT NOT NULL,created_authorization_event_id BIGINT UNSIGNED NOT NULL,"
    "created_mfa_proof_event_id BIGINT UNSIGNED NOT NULL,"
    "state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'DRAFT',"
    "current_revision_id BIGINT UNSIGNED NULL,effective_published_event_id BIGINT UNSIGNED NULL,"
    "row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,"
    "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_release_identity (module_id,module_version),"
    "KEY idx_office_module_release_creator (created_by_user_id,state,release_id),"
    "CONSTRAINT fk_office_module_release_creator FOREIGN KEY (created_by_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_module_release_creator_authorization FOREIGN KEY (created_authorization_event_id) REFERENCES office_authorization_event(event_id),"
    "CONSTRAINT fk_office_module_release_creator_mfa FOREIGN KEY (created_mfa_proof_event_id) REFERENCES office_mfa_security_event(event_id),"
    "CONSTRAINT chk_office_module_release_identity CHECK (module_id<>'' AND module_version<>'' AND row_version>0),"
    "CONSTRAINT chk_office_module_release_state CHECK ((BINARY state=BINARY 'DRAFT' AND effective_published_event_id IS NULL) OR (BINARY state=BINARY 'PUBLISHED' AND current_revision_id IS NOT NULL AND effective_published_event_id IS NOT NULL) OR (BINARY state=BINARY 'REVOKED' AND current_revision_id IS NOT NULL AND effective_published_event_id IS NOT NULL))"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_release_revision ("
    "revision_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,release_id BIGINT UNSIGNED NOT NULL,"
    "revision_no INT UNSIGNED NOT NULL,artifact_set_digest BINARY(32) NOT NULL,"
    "manifest_schema VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,manifest_digest BINARY(32) NOT NULL,"
    "manifest_bytes LONGBLOB NOT NULL,signing_key_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,manifest_signature LONGBLOB NOT NULL,"
    "created_by_user_id INT NOT NULL,created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_release_revision_no (release_id,revision_no),"
    "UNIQUE KEY uq_office_module_release_revision_release (revision_id,release_id),"
    "UNIQUE KEY uq_office_module_release_revision_binding (revision_id,release_id,artifact_set_digest),"
    "KEY idx_office_module_release_revision_artifact (release_id,artifact_set_digest),"
    "CONSTRAINT fk_office_module_release_revision_release FOREIGN KEY (release_id) REFERENCES office_module_release(release_id),"
    "CONSTRAINT fk_office_module_release_revision_creator FOREIGN KEY (created_by_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT chk_office_module_release_revision_manifest CHECK (BINARY manifest_schema=BINARY 'manifest/v2' AND OCTET_LENGTH(artifact_set_digest)=32 AND OCTET_LENGTH(manifest_digest)=32 AND manifest_digest=UNHEX(SHA2(manifest_bytes,256)) AND OCTET_LENGTH(manifest_bytes)>0 AND signing_key_id<>'' AND OCTET_LENGTH(manifest_signature)>0)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_license_evidence ("
    "license_evidence_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,revision_id BIGINT UNSIGNED NOT NULL,release_id BIGINT UNSIGNED NOT NULL,artifact_set_digest BINARY(32) NOT NULL,"
    "evidence_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,evidence_digest BINARY(32) NOT NULL,evidence_bytes LONGBLOB NOT NULL,"
    "recorded_by_user_id INT NOT NULL,recorded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_license_evidence_entry (revision_id,evidence_type,evidence_digest),"
    "UNIQUE KEY uq_office_module_license_evidence_binding (license_evidence_id,revision_id,artifact_set_digest,evidence_type),"
    "KEY idx_office_module_license_evidence_revision (revision_id,evidence_type,license_evidence_id),"
    "CONSTRAINT fk_office_module_license_evidence_revision FOREIGN KEY (revision_id,release_id,artifact_set_digest) REFERENCES office_module_release_revision(revision_id,release_id,artifact_set_digest),"
    "CONSTRAINT fk_office_module_license_evidence_recorder FOREIGN KEY (recorded_by_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT chk_office_module_license_evidence_typed CHECK (BINARY evidence_type IN (BINARY 'LICENSE_TEXT',BINARY 'RIGHTS_GRANT',BINARY 'THIRD_PARTY_NOTICE') AND OCTET_LENGTH(evidence_digest)=32 AND evidence_digest=UNHEX(SHA2(evidence_bytes,256)) AND OCTET_LENGTH(evidence_bytes)>0)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_creative_approval ("
    "creative_approval_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,revision_id BIGINT UNSIGNED NOT NULL,release_id BIGINT UNSIGNED NOT NULL,artifact_set_digest BINARY(32) NOT NULL,"
    "outcome VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,actor_user_id INT NOT NULL,authorization_event_id BIGINT UNSIGNED NOT NULL,mfa_proof_event_id BIGINT UNSIGNED NOT NULL,"
    "recorded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_creative_approval_binding (creative_approval_id,revision_id,outcome),"
    "KEY idx_office_module_creative_approval_revision (revision_id,outcome,creative_approval_id),"
    "CONSTRAINT fk_office_module_creative_approval_revision FOREIGN KEY (revision_id,release_id,artifact_set_digest) REFERENCES office_module_release_revision(revision_id,release_id,artifact_set_digest),"
    "CONSTRAINT fk_office_module_creative_approval_actor FOREIGN KEY (actor_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_module_creative_approval_authorization FOREIGN KEY (authorization_event_id) REFERENCES office_authorization_event(event_id),"
    "CONSTRAINT fk_office_module_creative_approval_mfa FOREIGN KEY (mfa_proof_event_id) REFERENCES office_mfa_security_event(event_id),"
    "CONSTRAINT chk_office_module_creative_approval_outcome CHECK (BINARY outcome IN (BINARY 'APPROVED',BINARY 'REJECTED'))"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_rights_verdict ("
    "rights_verdict_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,revision_id BIGINT UNSIGNED NOT NULL,release_id BIGINT UNSIGNED NOT NULL,artifact_set_digest BINARY(32) NOT NULL,"
    "outcome VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,license_evidence_id BIGINT UNSIGNED NOT NULL,license_revision_id BIGINT UNSIGNED NOT NULL,license_artifact_set_digest BINARY(32) NOT NULL,license_evidence_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "jacobs_agent_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,jacobs_receipt_digest BINARY(32) NOT NULL,jacobs_receipt_bytes LONGBLOB NOT NULL,jacobs_signing_key_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,jacobs_receipt_signature LONGBLOB NOT NULL,"
    "recorded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_rights_verdict_binding (rights_verdict_id,revision_id,outcome),"
    "KEY idx_office_module_rights_verdict_revision (revision_id,outcome,rights_verdict_id),"
    "CONSTRAINT fk_office_module_rights_verdict_revision FOREIGN KEY (revision_id,release_id,artifact_set_digest) REFERENCES office_module_release_revision(revision_id,release_id,artifact_set_digest),"
    "CONSTRAINT fk_office_module_rights_verdict_license FOREIGN KEY (license_evidence_id,license_revision_id,license_artifact_set_digest,license_evidence_type) REFERENCES office_module_license_evidence(license_evidence_id,revision_id,artifact_set_digest,evidence_type),"
    "CONSTRAINT chk_office_module_rights_verdict_receipt CHECK (BINARY outcome IN (BINARY 'PASS',BINARY 'FAIL',BINARY 'REVIEW_REQUIRED') AND license_revision_id=revision_id AND license_artifact_set_digest=artifact_set_digest AND jacobs_agent_id<>'' AND OCTET_LENGTH(jacobs_receipt_digest)=32 AND jacobs_receipt_digest=UNHEX(SHA2(jacobs_receipt_bytes,256)) AND OCTET_LENGTH(jacobs_receipt_bytes)>0 AND jacobs_signing_key_id<>'' AND OCTET_LENGTH(jacobs_receipt_signature)>0)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_creative_approval_head ("
    "revision_id BIGINT UNSIGNED NOT NULL,approved_approval_id BIGINT UNSIGNED NOT NULL,outcome VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'APPROVED',row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),"
    "PRIMARY KEY (revision_id),UNIQUE KEY uq_office_module_creative_approval_head_binding (revision_id,approved_approval_id,outcome),"
    "CONSTRAINT fk_office_module_creative_approval_head_revision FOREIGN KEY (revision_id) REFERENCES office_module_release_revision(revision_id),"
    "CONSTRAINT fk_office_module_creative_approval_head_approval FOREIGN KEY (approved_approval_id,revision_id,outcome) REFERENCES office_module_creative_approval(creative_approval_id,revision_id,outcome),"
    "CONSTRAINT chk_office_module_creative_approval_head CHECK (BINARY outcome=BINARY 'APPROVED' AND row_version>0)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_rights_verdict_head ("
    "revision_id BIGINT UNSIGNED NOT NULL,pass_verdict_id BIGINT UNSIGNED NOT NULL,outcome VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'PASS',row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),"
    "PRIMARY KEY (revision_id),UNIQUE KEY uq_office_module_rights_verdict_head_binding (revision_id,pass_verdict_id,outcome),"
    "CONSTRAINT fk_office_module_rights_verdict_head_revision FOREIGN KEY (revision_id) REFERENCES office_module_release_revision(revision_id),"
    "CONSTRAINT fk_office_module_rights_verdict_head_verdict FOREIGN KEY (pass_verdict_id,revision_id,outcome) REFERENCES office_module_rights_verdict(rights_verdict_id,revision_id,outcome),"
    "CONSTRAINT chk_office_module_rights_verdict_head CHECK (BINARY outcome=BINARY 'PASS' AND row_version>0)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_published_event ("
    "published_event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,release_id BIGINT UNSIGNED NOT NULL,revision_id BIGINT UNSIGNED NOT NULL,artifact_set_digest BINARY(32) NOT NULL,"
    "creative_approval_id BIGINT UNSIGNED NOT NULL,creative_head_revision_id BIGINT UNSIGNED NOT NULL,creative_head_outcome VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "rights_verdict_id BIGINT UNSIGNED NOT NULL,rights_head_revision_id BIGINT UNSIGNED NOT NULL,rights_head_outcome VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "actor_user_id INT NOT NULL,authorization_event_id BIGINT UNSIGNED NOT NULL,mfa_proof_event_id BIGINT UNSIGNED NOT NULL,recorded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_published_event_binding (published_event_id,release_id),"
    "KEY idx_office_module_published_event_release (release_id,published_event_id),"
    "CONSTRAINT fk_office_module_published_event_revision FOREIGN KEY (revision_id,release_id,artifact_set_digest) REFERENCES office_module_release_revision(revision_id,release_id,artifact_set_digest),"
    "CONSTRAINT fk_office_module_published_event_creator FOREIGN KEY (actor_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_module_published_event_authorization FOREIGN KEY (authorization_event_id) REFERENCES office_authorization_event(event_id),"
    "CONSTRAINT fk_office_module_published_event_mfa FOREIGN KEY (mfa_proof_event_id) REFERENCES office_mfa_security_event(event_id),"
    "CONSTRAINT fk_office_module_published_event_creative_head FOREIGN KEY (creative_head_revision_id,creative_approval_id,creative_head_outcome) REFERENCES office_module_creative_approval_head(revision_id,approved_approval_id,outcome),"
    "CONSTRAINT fk_office_module_published_event_rights_head FOREIGN KEY (rights_head_revision_id,rights_verdict_id,rights_head_outcome) REFERENCES office_module_rights_verdict_head(revision_id,pass_verdict_id,outcome),"
    "CONSTRAINT chk_office_module_published_event_heads CHECK (creative_head_revision_id=revision_id AND rights_head_revision_id=revision_id AND BINARY creative_head_outcome=BINARY 'APPROVED' AND BINARY rights_head_outcome=BINARY 'PASS')"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
)


_LATE_FOREIGN_KEYS = (
    "ALTER TABLE office_module_release ADD CONSTRAINT fk_office_module_release_current_revision FOREIGN KEY (current_revision_id,release_id) REFERENCES office_module_release_revision(revision_id,release_id)",
    "ALTER TABLE office_module_release ADD CONSTRAINT fk_office_module_release_effective_published_event FOREIGN KEY (effective_published_event_id,release_id) REFERENCES office_module_published_event(published_event_id,release_id)",
)

_TABLE_COLUMNS = {
    "office_module_release": ("release_id", "module_id", "module_version", "created_by_user_id", "created_authorization_event_id", "created_mfa_proof_event_id", "state", "current_revision_id", "effective_published_event_id", "row_version", "created_at", "updated_at"),
    "office_module_release_revision": ("revision_id", "release_id", "revision_no", "artifact_set_digest", "manifest_schema", "manifest_digest", "manifest_bytes", "signing_key_id", "manifest_signature", "created_by_user_id", "created_at"),
    "office_module_license_evidence": ("license_evidence_id", "revision_id", "release_id", "artifact_set_digest", "evidence_type", "evidence_digest", "evidence_bytes", "recorded_by_user_id", "recorded_at"),
    "office_module_creative_approval": ("creative_approval_id", "revision_id", "release_id", "artifact_set_digest", "outcome", "actor_user_id", "authorization_event_id", "mfa_proof_event_id", "recorded_at"),
    "office_module_rights_verdict": ("rights_verdict_id", "revision_id", "release_id", "artifact_set_digest", "outcome", "license_evidence_id", "license_revision_id", "license_artifact_set_digest", "license_evidence_type", "jacobs_agent_id", "jacobs_receipt_digest", "jacobs_receipt_bytes", "jacobs_signing_key_id", "jacobs_receipt_signature", "recorded_at"),
    "office_module_creative_approval_head": ("revision_id", "approved_approval_id", "outcome", "row_version", "updated_at"),
    "office_module_rights_verdict_head": ("revision_id", "pass_verdict_id", "outcome", "row_version", "updated_at"),
    "office_module_published_event": ("published_event_id", "release_id", "revision_id", "artifact_set_digest", "creative_approval_id", "creative_head_revision_id", "creative_head_outcome", "rights_verdict_id", "rights_head_revision_id", "rights_head_outcome", "actor_user_id", "authorization_event_id", "mfa_proof_event_id", "recorded_at"),
}

_INDEXES = {
    ("office_module_release", "PRIMARY"): (True, ("release_id",)),
    ("office_module_release", "uq_office_module_release_identity"): (True, ("module_id", "module_version")),
    ("office_module_release_revision", "PRIMARY"): (True, ("revision_id",)),
    ("office_module_release_revision", "uq_office_module_release_revision_no"): (True, ("release_id", "revision_no")),
    ("office_module_release_revision", "uq_office_module_release_revision_release"): (True, ("revision_id", "release_id")),
    ("office_module_release_revision", "uq_office_module_release_revision_binding"): (True, ("revision_id", "release_id", "artifact_set_digest")),
    ("office_module_license_evidence", "PRIMARY"): (True, ("license_evidence_id",)),
    ("office_module_license_evidence", "uq_office_module_license_evidence_entry"): (True, ("revision_id", "evidence_type", "evidence_digest")),
    ("office_module_license_evidence", "uq_office_module_license_evidence_binding"): (True, ("license_evidence_id", "revision_id", "artifact_set_digest", "evidence_type")),
    ("office_module_creative_approval", "PRIMARY"): (True, ("creative_approval_id",)),
    ("office_module_creative_approval", "uq_office_module_creative_approval_binding"): (True, ("creative_approval_id", "revision_id", "outcome")),
    ("office_module_rights_verdict", "PRIMARY"): (True, ("rights_verdict_id",)),
    ("office_module_rights_verdict", "uq_office_module_rights_verdict_binding"): (True, ("rights_verdict_id", "revision_id", "outcome")),
    ("office_module_creative_approval_head", "PRIMARY"): (True, ("revision_id",)),
    ("office_module_creative_approval_head", "uq_office_module_creative_approval_head_binding"): (True, ("revision_id", "approved_approval_id", "outcome")),
    ("office_module_rights_verdict_head", "PRIMARY"): (True, ("revision_id",)),
    ("office_module_rights_verdict_head", "uq_office_module_rights_verdict_head_binding"): (True, ("revision_id", "pass_verdict_id", "outcome")),
    ("office_module_published_event", "PRIMARY"): (True, ("published_event_id",)),
    ("office_module_published_event", "uq_office_module_published_event_binding"): (True, ("published_event_id", "release_id")),
}

_FOREIGN_KEYS = {
    "fk_office_module_release_creator": ("office_module_release", ("created_by_user_id",), "jax_users", ("user_id",)),
    "fk_office_module_release_creator_authorization": ("office_module_release", ("created_authorization_event_id",), "office_authorization_event", ("event_id",)),
    "fk_office_module_release_creator_mfa": ("office_module_release", ("created_mfa_proof_event_id",), "office_mfa_security_event", ("event_id",)),
    "fk_office_module_release_current_revision": ("office_module_release", ("current_revision_id", "release_id"), "office_module_release_revision", ("revision_id", "release_id")),
    "fk_office_module_release_effective_published_event": ("office_module_release", ("effective_published_event_id", "release_id"), "office_module_published_event", ("published_event_id", "release_id")),
    "fk_office_module_release_revision_release": ("office_module_release_revision", ("release_id",), "office_module_release", ("release_id",)),
    "fk_office_module_release_revision_creator": ("office_module_release_revision", ("created_by_user_id",), "jax_users", ("user_id",)),
    "fk_office_module_license_evidence_revision": ("office_module_license_evidence", ("revision_id", "release_id", "artifact_set_digest"), "office_module_release_revision", ("revision_id", "release_id", "artifact_set_digest")),
    "fk_office_module_license_evidence_recorder": ("office_module_license_evidence", ("recorded_by_user_id",), "jax_users", ("user_id",)),
    "fk_office_module_creative_approval_revision": ("office_module_creative_approval", ("revision_id", "release_id", "artifact_set_digest"), "office_module_release_revision", ("revision_id", "release_id", "artifact_set_digest")),
    "fk_office_module_creative_approval_actor": ("office_module_creative_approval", ("actor_user_id",), "jax_users", ("user_id",)),
    "fk_office_module_creative_approval_authorization": ("office_module_creative_approval", ("authorization_event_id",), "office_authorization_event", ("event_id",)),
    "fk_office_module_creative_approval_mfa": ("office_module_creative_approval", ("mfa_proof_event_id",), "office_mfa_security_event", ("event_id",)),
    "fk_office_module_rights_verdict_revision": ("office_module_rights_verdict", ("revision_id", "release_id", "artifact_set_digest"), "office_module_release_revision", ("revision_id", "release_id", "artifact_set_digest")),
    "fk_office_module_rights_verdict_license": ("office_module_rights_verdict", ("license_evidence_id", "license_revision_id", "license_artifact_set_digest", "license_evidence_type"), "office_module_license_evidence", ("license_evidence_id", "revision_id", "artifact_set_digest", "evidence_type")),
    "fk_office_module_creative_approval_head_revision": ("office_module_creative_approval_head", ("revision_id",), "office_module_release_revision", ("revision_id",)),
    "fk_office_module_creative_approval_head_approval": ("office_module_creative_approval_head", ("approved_approval_id", "revision_id", "outcome"), "office_module_creative_approval", ("creative_approval_id", "revision_id", "outcome")),
    "fk_office_module_rights_verdict_head_revision": ("office_module_rights_verdict_head", ("revision_id",), "office_module_release_revision", ("revision_id",)),
    "fk_office_module_rights_verdict_head_verdict": ("office_module_rights_verdict_head", ("pass_verdict_id", "revision_id", "outcome"), "office_module_rights_verdict", ("rights_verdict_id", "revision_id", "outcome")),
    "fk_office_module_published_event_revision": ("office_module_published_event", ("revision_id", "release_id", "artifact_set_digest"), "office_module_release_revision", ("revision_id", "release_id", "artifact_set_digest")),
    "fk_office_module_published_event_creator": ("office_module_published_event", ("actor_user_id",), "jax_users", ("user_id",)),
    "fk_office_module_published_event_authorization": ("office_module_published_event", ("authorization_event_id",), "office_authorization_event", ("event_id",)),
    "fk_office_module_published_event_mfa": ("office_module_published_event", ("mfa_proof_event_id",), "office_mfa_security_event", ("event_id",)),
    "fk_office_module_published_event_creative_head": ("office_module_published_event", ("creative_head_revision_id", "creative_approval_id", "creative_head_outcome"), "office_module_creative_approval_head", ("revision_id", "approved_approval_id", "outcome")),
    "fk_office_module_published_event_rights_head": ("office_module_published_event", ("rights_head_revision_id", "rights_verdict_id", "rights_head_outcome"), "office_module_rights_verdict_head", ("revision_id", "pass_verdict_id", "outcome")),
}

_CHECK_NAMES = frozenset((
    "chk_office_module_release_identity", "chk_office_module_release_state", "chk_office_module_release_revision_manifest",
    "chk_office_module_license_evidence_typed", "chk_office_module_creative_approval_outcome", "chk_office_module_rights_verdict_receipt",
    "chk_office_module_creative_approval_head", "chk_office_module_rights_verdict_head", "chk_office_module_published_event_heads",
))


async def _constraint_exists(cur, name: str) -> bool:
    await cur.execute("SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME=%s", (name,))
    return await cur.fetchone() is not None


async def apply_module_release_ledger_migrations(cur) -> None:
    """Install only physical safeguards; no operation here can publish a module."""
    for statement in MODULE_RELEASE_DDL:
        await cur.execute(statement)
    for statement in _LATE_FOREIGN_KEYS:
        name = statement.split(" ADD CONSTRAINT ", 1)[1].split(" ", 1)[0]
        if not await _constraint_exists(cur, name):
            await cur.execute(statement)
    for table in _APPEND_ONLY:
        for operation in ("UPDATE", "DELETE"):
            trigger = f"no_{operation.lower()}_{table}"
            await cur.execute(f"CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE {operation} ON {table} FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'")
    await assert_module_release_ledger_metadata(cur)


async def assert_module_release_ledger_metadata(cur) -> None:
    """Reject incomplete, substituted, or unguarded release storage."""
    for table, expected_columns in _TABLE_COLUMNS.items():
        await cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (table,))
        if tuple(row[0] for row in await cur.fetchall()) != expected_columns:
            raise RuntimeError(f"Office module release ledger columns for {table} are invalid")
    for (table, index), (unique, columns) in _INDEXES.items():
        await cur.execute("SELECT NON_UNIQUE,COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s ORDER BY SEQ_IN_INDEX", (table, index))
        rows = await cur.fetchall()
        if not rows or any(int(row[0]) != int(not unique) for row in rows) or tuple(row[1] for row in rows) != columns:
            raise RuntimeError(f"Office module release ledger index {table}.{index} is invalid")
    await cur.execute("SELECT CONSTRAINT_NAME FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME LIKE 'chk_office_module_%'")
    if {str(row[0]) for row in await cur.fetchall()} != _CHECK_NAMES:
        raise RuntimeError("Office module release ledger checks are invalid")
    for name, (table, local, referenced_table, referenced) in _FOREIGN_KEYS.items():
        await cur.execute(
            "SELECT k.COLUMN_NAME,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,r.UPDATE_RULE,r.DELETE_RULE "
            "FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
            "ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.TABLE_NAME=k.TABLE_NAME "
            "WHERE k.CONSTRAINT_SCHEMA=DATABASE() AND k.TABLE_NAME=%s AND k.CONSTRAINT_NAME=%s ORDER BY k.ORDINAL_POSITION",
            (table, name),
        )
        rows = await cur.fetchall()
        if (not rows or tuple(row[0] for row in rows) != local or rows[0][1] != referenced_table
                or tuple(row[2] for row in rows) != referenced or (rows[0][3], rows[0][4]) != ("RESTRICT", "RESTRICT")):
            raise RuntimeError(f"Office module release ledger foreign key {name} is invalid")
    for table in _APPEND_ONLY:
        await cur.execute("SELECT TRIGGER_NAME,EVENT_MANIPULATION FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE=%s", (table,))
        if {tuple(row) for row in await cur.fetchall()} != {(f"no_update_{table}", "UPDATE"), (f"no_delete_{table}", "DELETE")}:
            raise RuntimeError(f"Office module release ledger triggers for {table} are invalid")
