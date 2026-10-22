"""Inert v2 Office release records.

The v1 staging tables deliberately remain untouched.  This version records
the three authorities separately and makes no effective publication claim.
It is intentionally not wired into the web process or the runtime migrator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


PENDING = "PENDING_CONTRACT"
TABLES = (
    "office_module_artifact_snapshot_v2", "office_module_publication_request_v2",
    "office_module_creative_approval_v2", "office_module_rights_verdict_v2",
    "office_module_release_operation_v2", "office_module_release_receipt_v2",
    "office_module_effective_release_head_v2",
)
STAMP_TABLE = "office_module_release_v2_schema_stamp"
_STAMP_COMPONENT = "office-module-release-v2"
_STAMP_VERSION = "v2"

# No table here is writable by the ordinary Office web principal.  The
# provisioner must continue to reject these names even before a future,
# separately-reviewed writer capability exists.
FORBIDDEN_RUNTIME_TABLES = frozenset((*TABLES, STAMP_TABLE))

DDL = (
    "CREATE TABLE IF NOT EXISTS office_module_artifact_snapshot_v2 ("
    "snapshot_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,candidate_revision_id BIGINT UNSIGNED NOT NULL,candidate_id BIGINT UNSIGNED NOT NULL,manifest_sha256 BINARY(32) NOT NULL,module_package_digest BINARY(32) NOT NULL,project_id INT NOT NULL,tenant_id INT NOT NULL,module_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,module_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_snapshot_v2 (snapshot_id,project_id,tenant_id,module_id,module_version),KEY idx_office_module_snapshot_v2_revision (candidate_revision_id,candidate_id,manifest_sha256,module_package_digest),KEY idx_office_module_snapshot_v2_candidate_scope (candidate_id,project_id,tenant_id,module_id,module_version),"
    "CONSTRAINT fk_office_module_snapshot_v2_revision FOREIGN KEY (candidate_revision_id,candidate_id,manifest_sha256,module_package_digest) REFERENCES office_module_candidate_revision(candidate_revision_id,candidate_id,manifest_sha256,claimed_artifact_set_digest),CONSTRAINT fk_office_module_snapshot_v2_candidate_scope FOREIGN KEY (candidate_id,project_id,tenant_id,module_id,module_version) REFERENCES office_module_candidate(candidate_id,origin_project_id,origin_tenant_id,module_id,module_version),CONSTRAINT chk_office_module_snapshot_v2_digest CHECK (OCTET_LENGTH(module_package_digest)=32 AND module_id<>'' AND module_version<>'')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin",
    "CREATE TABLE IF NOT EXISTS office_module_publication_request_v2 ("
    "publication_request_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,snapshot_id BIGINT UNSIGNED NOT NULL,project_id INT NOT NULL,tenant_id INT NOT NULL,module_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,module_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,requested_by_user_id INT NOT NULL,mfa_proof_event_id BIGINT UNSIGNED NULL,state VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_request_v2 (publication_request_id,project_id,tenant_id,module_id,module_version),KEY idx_office_module_request_v2_snapshot (snapshot_id,project_id,tenant_id,module_id,module_version),KEY idx_office_module_request_v2_user (requested_by_user_id),KEY idx_office_module_request_v2_mfa (mfa_proof_event_id),CONSTRAINT fk_office_module_request_v2_snapshot FOREIGN KEY (snapshot_id,project_id,tenant_id,module_id,module_version) REFERENCES office_module_artifact_snapshot_v2(snapshot_id,project_id,tenant_id,module_id,module_version),CONSTRAINT fk_office_module_request_v2_user FOREIGN KEY (requested_by_user_id) REFERENCES jax_users(user_id),CONSTRAINT fk_office_module_request_v2_mfa FOREIGN KEY (mfa_proof_event_id) REFERENCES office_mfa_security_event(event_id),CONSTRAINT chk_office_module_request_v2_state CHECK (state='PENDING_CONTRACT')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin",
    "CREATE TABLE IF NOT EXISTS office_module_creative_approval_v2 ("
    "creative_approval_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,publication_request_id BIGINT UNSIGNED NOT NULL,project_id INT NOT NULL,tenant_id INT NOT NULL,module_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,module_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,human_user_id INT NOT NULL,mfa_proof_event_id BIGINT UNSIGNED NULL,decision VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_creative_approval_v2 (creative_approval_id,publication_request_id,project_id,tenant_id,module_id,module_version),KEY idx_office_module_creative_approval_v2_request (publication_request_id,project_id,tenant_id,module_id,module_version),KEY idx_office_module_creative_approval_v2_human (human_user_id),KEY idx_office_module_creative_approval_v2_mfa (mfa_proof_event_id),CONSTRAINT fk_office_module_creative_approval_v2_request FOREIGN KEY (publication_request_id,project_id,tenant_id,module_id,module_version) REFERENCES office_module_publication_request_v2(publication_request_id,project_id,tenant_id,module_id,module_version),CONSTRAINT fk_office_module_creative_approval_v2_human FOREIGN KEY (human_user_id) REFERENCES jax_users(user_id),CONSTRAINT fk_office_module_creative_approval_v2_mfa FOREIGN KEY (mfa_proof_event_id) REFERENCES office_mfa_security_event(event_id),CONSTRAINT chk_office_module_creative_approval_v2_state CHECK (decision='PENDING_CONTRACT')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin",
    "CREATE TABLE IF NOT EXISTS office_module_rights_verdict_v2 ("
    "rights_verdict_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,publication_request_id BIGINT UNSIGNED NOT NULL,project_id INT NOT NULL,tenant_id INT NOT NULL,module_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,module_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,critic_principal VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,evidence_set_sha256 BINARY(32) NOT NULL,verification_state VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,decision VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_rights_verdict_v2 (rights_verdict_id,publication_request_id,project_id,tenant_id,module_id,module_version),KEY idx_office_module_rights_verdict_v2_request (publication_request_id,project_id,tenant_id,module_id,module_version),CONSTRAINT fk_office_module_rights_verdict_v2_request FOREIGN KEY (publication_request_id,project_id,tenant_id,module_id,module_version) REFERENCES office_module_publication_request_v2(publication_request_id,project_id,tenant_id,module_id,module_version),CONSTRAINT chk_office_module_rights_verdict_v2_state CHECK (critic_principal<>'' AND OCTET_LENGTH(evidence_set_sha256)=32 AND verification_state='PENDING_CONTRACT' AND decision='PENDING_CONTRACT')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin",
    "CREATE TABLE IF NOT EXISTS office_module_release_operation_v2 ("
    "release_operation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,publication_request_id BIGINT UNSIGNED NOT NULL,project_id INT NOT NULL,tenant_id INT NOT NULL,module_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,module_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,creative_approval_id BIGINT UNSIGNED NOT NULL,rights_verdict_id BIGINT UNSIGNED NOT NULL,office_release_signer VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,state VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_release_operation_v2 (release_operation_id,project_id,tenant_id,module_id,module_version),KEY idx_office_module_release_operation_v2_request (publication_request_id,project_id,tenant_id,module_id,module_version),KEY idx_office_module_release_operation_v2_creative (creative_approval_id,publication_request_id,project_id,tenant_id,module_id,module_version),KEY idx_office_module_release_operation_v2_rights (rights_verdict_id,publication_request_id,project_id,tenant_id,module_id,module_version),CONSTRAINT fk_office_module_release_operation_v2_request FOREIGN KEY (publication_request_id,project_id,tenant_id,module_id,module_version) REFERENCES office_module_publication_request_v2(publication_request_id,project_id,tenant_id,module_id,module_version),CONSTRAINT fk_office_module_release_operation_v2_creative FOREIGN KEY (creative_approval_id,publication_request_id,project_id,tenant_id,module_id,module_version) REFERENCES office_module_creative_approval_v2(creative_approval_id,publication_request_id,project_id,tenant_id,module_id,module_version),CONSTRAINT fk_office_module_release_operation_v2_rights FOREIGN KEY (rights_verdict_id,publication_request_id,project_id,tenant_id,module_id,module_version) REFERENCES office_module_rights_verdict_v2(rights_verdict_id,publication_request_id,project_id,tenant_id,module_id,module_version),CONSTRAINT chk_office_module_release_operation_v2_state CHECK (office_release_signer<>'' AND state='PENDING_CONTRACT')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin",
    "CREATE TABLE IF NOT EXISTS office_module_release_receipt_v2 ("
    "release_receipt_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,release_operation_id BIGINT UNSIGNED NOT NULL,project_id INT NOT NULL,tenant_id INT NOT NULL,module_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,module_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,receipt_sha256 BINARY(32) NULL,receipt_signature MEDIUMBLOB NULL,state VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_release_receipt_v2 (release_receipt_id,project_id,tenant_id,module_id,module_version),KEY idx_office_module_release_receipt_v2_operation (release_operation_id,project_id,tenant_id,module_id,module_version),CONSTRAINT fk_office_module_release_receipt_v2_operation FOREIGN KEY (release_operation_id,project_id,tenant_id,module_id,module_version) REFERENCES office_module_release_operation_v2(release_operation_id,project_id,tenant_id,module_id,module_version),CONSTRAINT chk_office_module_release_receipt_v2_state CHECK (state='PENDING_CONTRACT' AND receipt_sha256 IS NULL AND receipt_signature IS NULL)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin",
    "CREATE TABLE IF NOT EXISTS office_module_effective_release_head_v2 ("
    "project_id INT NOT NULL,tenant_id INT NOT NULL,module_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,module_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,release_receipt_id BIGINT UNSIGNED NOT NULL,status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),PRIMARY KEY(project_id,tenant_id,module_id,module_version),KEY idx_office_module_effective_head_v2_receipt (release_receipt_id,project_id,tenant_id,module_id,module_version),CONSTRAINT fk_office_module_effective_head_v2_receipt FOREIGN KEY (release_receipt_id,project_id,tenant_id,module_id,module_version) REFERENCES office_module_release_receipt_v2(release_receipt_id,project_id,tenant_id,module_id,module_version),CONSTRAINT chk_office_module_effective_head_v2_state CHECK (module_id<>'' AND module_version<>'' AND status='PENDING_CONTRACT')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin",
)

# Names are part of the durable contract.  Keeping these derivable from the
# reviewed DDL prevents a migration cutpoint from silently accepting an extra
# authority edge, index, or moved CHECK.
_DDL_BY_TABLE = {re.search(r"CREATE TABLE IF NOT EXISTS ([a-z0-9_]+)", statement).group(1): statement for statement in DDL}
_EXPECTED_COLUMNS = {
    "office_module_artifact_snapshot_v2": ("snapshot_id","candidate_revision_id","candidate_id","manifest_sha256","module_package_digest","project_id","tenant_id","module_id","module_version","created_at"),
    "office_module_publication_request_v2": ("publication_request_id","snapshot_id","project_id","tenant_id","module_id","module_version","requested_by_user_id","mfa_proof_event_id","state","created_at"),
    "office_module_creative_approval_v2": ("creative_approval_id","publication_request_id","project_id","tenant_id","module_id","module_version","human_user_id","mfa_proof_event_id","decision","created_at"),
    "office_module_rights_verdict_v2": ("rights_verdict_id","publication_request_id","project_id","tenant_id","module_id","module_version","critic_principal","evidence_set_sha256","verification_state","decision","created_at"),
    "office_module_release_operation_v2": ("release_operation_id","publication_request_id","project_id","tenant_id","module_id","module_version","creative_approval_id","rights_verdict_id","office_release_signer","state","created_at"),
    "office_module_release_receipt_v2": ("release_receipt_id","release_operation_id","project_id","tenant_id","module_id","module_version","receipt_sha256","receipt_signature","state","created_at"),
    "office_module_effective_release_head_v2": ("project_id","tenant_id","module_id","module_version","release_receipt_id","status","created_at"),
}
_EXPECTED_COLUMN_SHAPES = {
    "snapshot_id": ("bigint(20) unsigned", "NO", None, "auto_increment", None, None),
    "publication_request_id": ("bigint(20) unsigned", "NO", None, "auto_increment", None, None),
    "creative_approval_id": ("bigint(20) unsigned", "NO", None, "auto_increment", None, None),
    "rights_verdict_id": ("bigint(20) unsigned", "NO", None, "auto_increment", None, None),
    "release_operation_id": ("bigint(20) unsigned", "NO", None, "auto_increment", None, None),
    "release_receipt_id": ("bigint(20) unsigned", "NO", None, "auto_increment", None, None),
    "candidate_revision_id": ("bigint(20) unsigned", "NO", None, "", None, None),
    "candidate_id": ("bigint(20) unsigned", "NO", None, "", None, None),
    "snapshot_id_ref": ("bigint(20) unsigned", "NO", None, "", None, None),
    "publication_request_id_ref": ("bigint(20) unsigned", "NO", None, "", None, None),
    "creative_approval_id_ref": ("bigint(20) unsigned", "NO", None, "", None, None),
    "rights_verdict_id_ref": ("bigint(20) unsigned", "NO", None, "", None, None),
    "release_operation_id_ref": ("bigint(20) unsigned", "NO", None, "", None, None),
    "release_receipt_id_ref": ("bigint(20) unsigned", "NO", None, "", None, None),
    "project_id": ("int(11)", "NO", None, "", None, None), "tenant_id": ("int(11)", "NO", None, "", None, None),
    "manifest_sha256": ("binary(32)", "NO", None, "", None, None), "module_package_digest": ("binary(32)", "NO", None, "", None, None),
    "evidence_set_sha256": ("binary(32)", "NO", None, "", None, None),
    "module_id": ("varchar(96)", "NO", None, "", "ascii", "ascii_bin"), "module_version": ("varchar(64)", "NO", None, "", "ascii", "ascii_bin"),
    "critic_principal": ("varchar(128)", "NO", None, "", "ascii", "ascii_bin"), "office_release_signer": ("varchar(128)", "NO", None, "", "ascii", "ascii_bin"),
    "state": ("varchar(24)", "NO", None, "", "ascii", "ascii_bin"), "status": ("varchar(24)", "NO", None, "", "ascii", "ascii_bin"), "decision": ("varchar(24)", "NO", None, "", "ascii", "ascii_bin"), "verification_state": ("varchar(24)", "NO", None, "", "ascii", "ascii_bin"),
    "requested_by_user_id": ("int(11)", "NO", None, "", None, None), "human_user_id": ("int(11)", "NO", None, "", None, None),
    "mfa_proof_event_id": ("bigint(20) unsigned", "YES", "NULL", "", None, None), "receipt_sha256": ("binary(32)", "YES", "NULL", "", None, None), "receipt_signature": ("mediumblob", "YES", "NULL", "", None, None),
    "created_at": ("datetime(6)", "NO", "current_timestamp(6)", "", None, None),
}
def _column_shape(table: str, name: str):
    if name in {"snapshot_id", "publication_request_id", "creative_approval_id", "rights_verdict_id", "release_operation_id", "release_receipt_id"} and _EXPECTED_COLUMNS[table][0] != name:
        return _EXPECTED_COLUMN_SHAPES[f"{name}_ref"]
    return _EXPECTED_COLUMN_SHAPES[name]
def _names(pattern: str, table: str) -> frozenset[str]: return frozenset(re.findall(pattern, _DDL_BY_TABLE[table]))
_EXPECTED_FKS = {table: _names(r"CONSTRAINT (fk_[a-z0-9_]+) FOREIGN KEY", table) for table in TABLES}
_EXPECTED_CHECKS = {table: _names(r"CONSTRAINT (chk_[a-z0-9_]+) CHECK", table) for table in TABLES}
_EXPECTED_INDEXES = {table: (frozenset({"PRIMARY"}) if "PRIMARY KEY" in _DDL_BY_TABLE[table] else frozenset()) | _names(r"(?:UNIQUE KEY|KEY) ([a-z0-9_]+)", table) for table in TABLES}
_EXPECTED_FK_EDGES = {}
for _table, _ddl in _DDL_BY_TABLE.items():
    for _name, _local, _target, _remote in re.findall(r"CONSTRAINT (fk_[a-z0-9_]+) FOREIGN KEY \(([^)]*)\) REFERENCES ([a-z0-9_]+)\(([^)]*)\)", _ddl):
        _EXPECTED_FK_EDGES[(_table, _name)] = (tuple(_local.split(",")), _target, tuple(_remote.split(",")))
_EXPECTED_INDEX_SHAPES = {}
for _table, _ddl in _DDL_BY_TABLE.items():
    _primary = re.search(r"PRIMARY KEY\(([^)]*)\)", _ddl)
    if _primary: _EXPECTED_INDEX_SHAPES[(_table, "PRIMARY")] = (True, tuple(_primary.group(1).split(",")))
    elif "PRIMARY KEY" in _ddl: _EXPECTED_INDEX_SHAPES[(_table, "PRIMARY")] = (True, (_EXPECTED_COLUMNS[_table][0],))
    for _kind, _name, _columns in re.findall(r"(UNIQUE KEY|KEY) ([a-z0-9_]+) \(([^)]*)\)", _ddl):
        _EXPECTED_INDEX_SHAPES[(_table, _name)] = (_kind == "UNIQUE KEY", tuple(_columns.split(",")))
def _check_clauses(ddl: str) -> dict[str, str]:
    """Extract balanced CHECK bodies from the canonical DDL, not a regex prefix."""
    result: dict[str, str] = {}
    for match in re.finditer(r"CONSTRAINT (chk_[a-z0-9_]+) CHECK ", ddl):
        start = match.end()
        if ddl[start] != "(": raise AssertionError("checked DDL is malformed")
        depth = 0
        for end in range(start, len(ddl)):
            depth += ddl[end] == "("
            depth -= ddl[end] == ")"
            if depth == 0:
                result[match.group(1)] = ddl[start + 1:end]
                break
        else: raise AssertionError("checked DDL is unbalanced")
    return result
def _normalize_clause(value: str) -> str: return re.sub(r"[`\s]", "", value).lower()
_EXPECTED_CHECK_CLAUSES = {table: {name: _normalize_clause(body) for name, body in _check_clauses(ddl).items()} for table, ddl in _DDL_BY_TABLE.items()}

class ReleaseReceiptRejected(ValueError): pass

@dataclass(frozen=True)
class BoundReleaseReceipt:
    module_package_digest: str
    project_id: int
    tenant_id: int
    creative_approval_id: int
    rights_verdict_id: int
    office_release_signer: str

_HEX = re.compile(r"sha256:[0-9a-f]{64}\Z")

def validate_release_receipt(value: Any, *, module_package_digest: str, project_id: int, tenant_id: int, human_approval_id: int, critic_rights_id: int, human_principal: str) -> BoundReleaseReceipt:
    """Pure structural binding only; it verifies neither signatures nor rights."""
    if not isinstance(value, dict) or set(value) != {"domain", "module_package_digest", "project_id", "tenant_id", "creative_approval_id", "rights_verdict_id", "critic_principal", "office_release_signer"}:
        raise ReleaseReceiptRejected("release receipt shape is invalid")
    if value["domain"] != "axioma.office.module-release-receipt/v1": raise ReleaseReceiptRejected("release receipt domain is invalid")
    if any(key in value for key in ("authorized_by", "model", "selector")): raise ReleaseReceiptRejected("release receipt has forbidden authority")
    for name in ("module_package_digest",):
        if not isinstance(value[name], str) or not _HEX.fullmatch(value[name]): raise ReleaseReceiptRejected("release receipt digest is invalid")
    fields = ("module_package_digest", "project_id", "tenant_id", "creative_approval_id", "rights_verdict_id")
    if any(type(item) is not int or item <= 0 for item in (project_id, tenant_id, human_approval_id, critic_rights_id)):
        raise ReleaseReceiptRejected("release receipt expected authority inputs are invalid")
    expected = (module_package_digest, project_id, tenant_id, human_approval_id, critic_rights_id)
    if any(type(value[field]) is not int or value[field] <= 0 for field in fields[1:]) or tuple(value[field] for field in fields) != expected: raise ReleaseReceiptRejected("release receipt does not bind its authority inputs")
    signer = value["office_release_signer"]
    critic = value["critic_principal"]
    if not isinstance(critic, str) or not critic.startswith("critic:") or critic == "critic:" or critic == human_principal: raise ReleaseReceiptRejected("release critic is not distinct authority")
    if not isinstance(signer, str) or not signer.startswith("office-release:") or signer == "office-release:" or signer in {human_principal, critic}: raise ReleaseReceiptRejected("release signer is not distinct Office authority")
    return BoundReleaseReceipt(*expected, signer)

async def apply_inert_release_v2_migrations(cur) -> None:
    """Resumable only for a creation cutpoint; metadata attestation closes drift."""
    await cur.execute("SELECT GET_LOCK(CONCAT('office-release-v2:',DATABASE()),30)")
    if await cur.fetchone() != (1,): raise RuntimeError("inert release v2 migration lock is unavailable")
    primary_error = None
    try:
        await _apply_inert_release_v2_locked(cur)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        await cur.execute("SELECT RELEASE_LOCK(CONCAT('office-release-v2:',DATABASE()))")
        if await cur.fetchone() != (1,) and primary_error is None: raise RuntimeError("inert release v2 migration lock release failed")

async def _apply_inert_release_v2_locked(cur) -> None:
    """Advance only exact, empty table-and-trigger prefixes; never normalize drift."""
    await cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN (%s,%s,%s,%s,%s,%s,%s)", TABLES)
    present = {row[0] for row in await cur.fetchall()}
    await cur.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (STAMP_TABLE,))
    stamp_exists = await cur.fetchone() is not None
    if present == set(TABLES):
        await assert_inert_release_v2_metadata(cur)
        if not stamp_exists:
            for table in TABLES:
                await cur.execute(f"SELECT 1 FROM `{table}` LIMIT 1")
                if await cur.fetchone() is not None: raise RuntimeError("inert release v2 pre-stamp tables have rows")
        await _ensure_release_stamp(cur)
        return
    if stamp_exists:
        raise RuntimeError("inert release v2 stamp is premature")
    for index, (table, statement) in enumerate(zip(TABLES, DDL)):
        await _assert_release_prefix(cur, index)
        await cur.execute(statement)
        for operation in ("UPDATE", "DELETE"):
            await cur.execute(f"CREATE TRIGGER IF NOT EXISTS no_{operation.lower()}_{table} BEFORE {operation} ON {table} FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'")
        await _assert_release_prefix(cur, index + 1)
    await _ensure_release_stamp(cur)
    await assert_inert_release_v2_metadata(cur)

async def _assert_release_stamp(cur) -> None:
    await cur.execute("SELECT ENGINE,TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (STAMP_TABLE,))
    if await cur.fetchone() != ("InnoDB", "utf8mb4_bin"): raise RuntimeError("inert release v2 stamp metadata is invalid")
    await cur.execute("SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA,CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (STAMP_TABLE,))
    if await cur.fetchall() != (("component","varchar(64)","NO",None,"","ascii","ascii_bin"),("version","varchar(16)","NO",None,"","ascii","ascii_bin"),("created_at","datetime(6)","NO","current_timestamp(6)","",None,None)): raise RuntimeError("inert release v2 stamp columns are invalid")
    await cur.execute("SELECT INDEX_NAME,NON_UNIQUE,COLUMN_NAME,INDEX_TYPE,SUB_PART,COLLATION,IGNORED FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY INDEX_NAME,SEQ_IN_INDEX", (STAMP_TABLE,))
    if await cur.fetchall() != (("PRIMARY",0,"component","BTREE",None,"A","NO"),): raise RuntimeError("inert release v2 stamp index is invalid")
    await cur.execute("SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL", (STAMP_TABLE,))
    if await cur.fetchall(): raise RuntimeError("inert release v2 stamp foreign keys are invalid")
    await cur.execute("SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE=%s", (STAMP_TABLE,))
    if await cur.fetchall(): raise RuntimeError("inert release v2 stamp triggers are invalid")
    await cur.execute("SELECT tc.CONSTRAINT_NAME,cc.CHECK_CLAUSE FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME=%s AND tc.CONSTRAINT_TYPE='CHECK'", (STAMP_TABLE,))
    if await cur.fetchall() != (("chk_office_module_release_v2_stamp", f"`component` = '{_STAMP_COMPONENT}' and `version` = '{_STAMP_VERSION}'"),): raise RuntimeError("inert release v2 stamp check is invalid")
    await cur.execute(f"SELECT component,version FROM {STAMP_TABLE}")
    if await cur.fetchall() != ((_STAMP_COMPONENT, _STAMP_VERSION),): raise RuntimeError("inert release v2 stamp is invalid")

async def _ensure_release_stamp(cur) -> None:
    await cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (STAMP_TABLE,))
    columns = tuple(row[0] for row in await cur.fetchall())
    if columns and columns != ("component", "version", "created_at"):
        raise RuntimeError("inert release v2 stamp columns are invalid")
    await cur.execute(f"CREATE TABLE IF NOT EXISTS {STAMP_TABLE} (component VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,version VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),CONSTRAINT chk_office_module_release_v2_stamp CHECK (component='{_STAMP_COMPONENT}' AND version='{_STAMP_VERSION}')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin")
    await cur.execute(f"INSERT IGNORE INTO {STAMP_TABLE} (component,version) VALUES (%s,%s)", (_STAMP_COMPONENT, _STAMP_VERSION))
    await _assert_release_stamp(cur)

async def _assert_release_prefix(cur, count: int) -> None:
    """A partial release schema is safe only as the exact empty prefix we create."""
    for index, table in enumerate(TABLES):
        await cur.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
        exists = await cur.fetchone() is not None
        if index >= count:
            if exists: raise RuntimeError("inert release v2 migration cutpoint is invalid")
            continue
        if not exists: raise RuntimeError("inert release v2 migration prefix is invalid")
        await cur.execute(f"SELECT 1 FROM `{table}` LIMIT 1")
        if await cur.fetchone() is not None: raise RuntimeError("inert release v2 interrupted prefix has rows")
    if count:
        await _assert_inert_release_v2_tables(cur, TABLES[:count])

async def assert_inert_release_v2_metadata(cur) -> None:
    await _assert_inert_release_v2_tables(cur, TABLES)

async def _assert_inert_release_v2_tables(cur, tables) -> None:
    for table in tables:
        await cur.execute("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
        if await cur.fetchone() != ("InnoDB",): raise RuntimeError(f"inert release v2 table {table} is invalid")
        await cur.execute("SELECT TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
        if await cur.fetchone() != ("utf8mb4_bin",): raise RuntimeError(f"inert release v2 collation {table} is invalid")
        await cur.execute("SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA,CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (table,))
        rows = await cur.fetchall()
        if tuple(row[0] for row in rows) != _EXPECTED_COLUMNS[table] or tuple(tuple(row[1:]) for row in rows) != tuple(_column_shape(table, name) for name in _EXPECTED_COLUMNS[table]): raise RuntimeError(f"inert release v2 columns {table} are invalid")
        await cur.execute("SELECT INDEX_NAME,NON_UNIQUE,COLUMN_NAME,COLLATION,SUB_PART,IGNORED,INDEX_TYPE FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY INDEX_NAME,SEQ_IN_INDEX", (table,))
        found_indexes: dict[str, tuple[bool, tuple[str, ...]]] = {}
        for name, non_unique, column, collation, sub_part, ignored, index_type in await cur.fetchall():
            if collation != "A" or sub_part is not None or str(ignored).upper() not in {"NO", "0"} or str(index_type).upper() != "BTREE": raise RuntimeError(f"inert release v2 index shape {table} is invalid")
            unique, columns = found_indexes.get(name, (not bool(non_unique), ()))
            if unique != (not bool(non_unique)): raise RuntimeError(f"inert release v2 index uniqueness {table} is invalid")
            found_indexes[name] = (unique, (*columns, column))
        expected_indexes = {name: shape for (indexed_table, name), shape in _EXPECTED_INDEX_SHAPES.items() if indexed_table == table}
        if found_indexes != expected_indexes: raise RuntimeError(f"inert release v2 indexes {table} are invalid")
        await cur.execute("SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL", (table,))
        if frozenset(row[0] for row in await cur.fetchall()) != _EXPECTED_FKS[table]: raise RuntimeError(f"inert release v2 foreign keys {table} are invalid")
        for name in _EXPECTED_FKS[table]:
            await cur.execute("SELECT k.COLUMN_NAME,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,r.UPDATE_RULE,r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.TABLE_NAME=k.TABLE_NAME WHERE k.CONSTRAINT_SCHEMA=DATABASE() AND k.TABLE_NAME=%s AND k.CONSTRAINT_NAME=%s ORDER BY k.ORDINAL_POSITION", (table, name))
            rows = await cur.fetchall(); local, target, remote = _EXPECTED_FK_EDGES[(table, name)]
            await cur.execute("SELECT REFERENCED_TABLE_SCHEMA FROM information_schema.KEY_COLUMN_USAGE WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND CONSTRAINT_NAME=%s LIMIT 1", (table, name))
            schema = await cur.fetchone()
            if tuple(row[0] for row in rows) != local or not rows or rows[0][1] != target or tuple(row[2] for row in rows) != remote or any((row[3], row[4]) != ("RESTRICT", "RESTRICT") for row in rows) or schema != ((await _database_name(cur)),): raise RuntimeError(f"inert release v2 foreign key {name} is invalid")
        await cur.execute("SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND CONSTRAINT_TYPE='CHECK'", (table,))
        if frozenset(row[0] for row in await cur.fetchall()) != _EXPECTED_CHECKS[table]: raise RuntimeError(f"inert release v2 checks {table} are invalid")
        await cur.execute("SELECT CONSTRAINT_NAME,CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME IN (SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND CONSTRAINT_TYPE='CHECK')", (table,))
        if {name: _normalize_clause(clause) for name, clause in await cur.fetchall()} != _EXPECTED_CHECK_CLAUSES[table]: raise RuntimeError(f"inert release v2 check clauses {table} are invalid")
        await cur.execute("SELECT CURRENT_USER(),@@SESSION.sql_mode")
        definer, sql_mode = await cur.fetchone()
        await cur.execute("SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT,DEFINER,SQL_MODE FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE=%s ORDER BY TRIGGER_NAME", (table,))
        expected_triggers = ((f"no_delete_{table}", table, "BEFORE", "DELETE", "ROW", f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'", definer, sql_mode), (f"no_update_{table}", table, "BEFORE", "UPDATE", "ROW", f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'", definer, sql_mode))
        if tuple(tuple(row) for row in await cur.fetchall()) != expected_triggers: raise RuntimeError(f"inert release v2 triggers {table} are invalid")

async def _database_name(cur) -> str:
    await cur.execute("SELECT DATABASE()")
    return (await cur.fetchone())[0]
