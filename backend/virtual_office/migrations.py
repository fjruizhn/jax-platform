from __future__ import annotations

OFFICE_DDL = (
    "CREATE TABLE IF NOT EXISTS office_account_profile (user_id INT NOT NULL PRIMARY KEY, global_role VARCHAR(24) NOT NULL DEFAULT 'COLLABORATOR', status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', version BIGINT NOT NULL DEFAULT 1, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6), CONSTRAINT fk_office_account_profile_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT chk_office_account_profile_role CHECK (global_role IN ('COLLABORATOR','GENERAL_ADMIN','OFFICE_SUPERUSER')), CONSTRAINT chk_office_account_profile_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_admin_delegation (delegation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, capability VARCHAR(40) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', granted_by INT NOT NULL, version BIGINT NOT NULL DEFAULT 1, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_admin_delegation (user_id,capability), KEY idx_office_admin_delegation_scope (user_id,capability,status), CONSTRAINT fk_office_admin_delegation_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_admin_delegation_granted_by FOREIGN KEY (granted_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_admin_delegation_capability CHECK (capability IN ('ADMINISTER_OFFICE')), CONSTRAINT chk_office_admin_delegation_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_project_grant (grant_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, user_id INT NOT NULL, grant_role VARCHAR(24) NOT NULL, audience VARCHAR(24) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', version BIGINT NOT NULL DEFAULT 1, granted_by INT NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_project_grant_subject (project_id,user_id), KEY idx_office_project_grant_scope (project_id,user_id,status,audience), CONSTRAINT fk_office_project_grant_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_project_grant_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_project_grant_granted_by FOREIGN KEY (granted_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_project_grant_role CHECK (grant_role IN ('PROJECT_ADMIN','COLLABORATOR')), CONSTRAINT chk_office_project_grant_audience CHECK (audience IN ('INTERNAL','CLIENT_PUBLISHED')), CONSTRAINT chk_office_project_grant_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_recipient_group (recipient_group_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, tenant_id INT NOT NULL, group_kind VARCHAR(16) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', created_by INT NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_recipient_group_project_kind (project_id,group_kind), UNIQUE KEY uq_office_recipient_group_id_project (recipient_group_id,project_id), KEY idx_office_recipient_group_scope (project_id,status), CONSTRAINT fk_office_recipient_group_scope FOREIGN KEY (project_id,tenant_id) REFERENCES jax_project_scope(project_id,tenant_id), CONSTRAINT fk_office_recipient_group_created_by FOREIGN KEY (created_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_recipient_group_kind CHECK (group_kind='CLIENT'), CONSTRAINT chk_office_recipient_group_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_external_relation (relation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, user_id INT NOT NULL, recipient_group_id BIGINT UNSIGNED NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', version BIGINT NOT NULL DEFAULT 1, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_external_relation_subject (project_id,user_id), KEY idx_office_external_relation_scope (project_id,user_id,status,recipient_group_id), CONSTRAINT fk_office_external_relation_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_external_relation_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_external_relation_group FOREIGN KEY (recipient_group_id) REFERENCES office_recipient_group(recipient_group_id), CONSTRAINT fk_office_external_relation_group_project FOREIGN KEY (recipient_group_id,project_id) REFERENCES office_recipient_group(recipient_group_id,project_id), CONSTRAINT chk_office_external_relation_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_professional_grant (professional_grant_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, user_id INT NOT NULL, capability VARCHAR(64) NOT NULL, basis VARCHAR(128) NOT NULL, evidence_ref VARCHAR(255) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', granted_by INT NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_professional_grant (project_id,user_id,capability), KEY idx_office_professional_grant_scope (project_id,user_id,capability,status), CONSTRAINT fk_office_professional_grant_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_professional_grant_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_professional_grant_granted_by FOREIGN KEY (granted_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_professional_grant_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_authorization_event (event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, actor_user_id INT NOT NULL, operation VARCHAR(40) NOT NULL, requested_project_id INT NULL, project_id INT NULL, tenant_id INT NULL, result VARCHAR(16) NOT NULL, reason_code VARCHAR(64) NOT NULL, correlation_id VARCHAR(128) NULL, occurred_at DATETIME(6) NOT NULL, KEY idx_office_authorization_event_scope (project_id,tenant_id,occurred_at), KEY idx_office_authorization_event_actor (actor_user_id,occurred_at), CONSTRAINT fk_office_authorization_event_actor FOREIGN KEY (actor_user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_authorization_event_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_authorization_event_tenant FOREIGN KEY (tenant_id) REFERENCES jax_tenants(tenant_id), CONSTRAINT fk_office_authorization_event_scope FOREIGN KEY (project_id,tenant_id) REFERENCES jax_project_scope(project_id,tenant_id), CONSTRAINT chk_office_authorization_event_result CHECK (result IN ('ALLOWED','DENIED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_authorization_event BEFORE UPDATE ON office_authorization_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_authorization_event append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_authorization_event BEFORE DELETE ON office_authorization_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_authorization_event append-only'",
    "CREATE TABLE IF NOT EXISTS office_schema_revision (component VARCHAR(64) NOT NULL PRIMARY KEY, revision VARCHAR(64) NOT NULL, applied_at DATETIME(6) NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
)

RUNTIME_COMPONENT = "virtual-office"
RUNTIME_REVISION = "office-runtime-v2"

# Exact MariaDB 12.3.3 CHECK_CLAUSE renderings, measured from the constraint
# created below.  Normalization deliberately preserves boolean operators and
# literals; accepting a bag of words would turn a permissive OR into a guard.
_CANONICAL_AUDIT_SHAPE = """`event_kind` = 'AUTHORIZATION' and `actor_user_id` is not null and `actor_principal` is null and `fence_key` is null and `result` in ('ALLOWED','DENIED') and `operation` <> 'AUDIT_FENCE_CANARY' or `event_kind` = 'SYSTEM_FENCE' and `actor_user_id` is null and `actor_principal` is not null and cast(`actor_principal` as char charset binary) = cast('system:office-schema-migrator' as char charset binary) and `fence_key` is not null and cast(`fence_key` as char charset binary) = cast('office-audit-fence-v1' as char charset binary) and `operation` = 'AUDIT_FENCE_CANARY' and `requested_project_id` is null and `project_id` is null and `tenant_id` is null and `result` = 'RECORDED' and `reason_code` = 'office_audit_fence_v1' and `correlation_id` is null"""
_CANONICAL_AUDIT_RESULT = "`result` in ('ALLOWED','DENIED','RECORDED')"


def _normalize_mariadb_check(clause: str | None) -> str | None:
    return None if clause is None else " ".join(clause.split())


def _is_canonical_audit_shape(clause: str | None) -> bool:
    return _normalize_mariadb_check(clause) == _normalize_mariadb_check(_CANONICAL_AUDIT_SHAPE)


def _is_canonical_audit_result(clause: str | None) -> bool:
    return _normalize_mariadb_check(clause) == _normalize_mariadb_check(_CANONICAL_AUDIT_RESULT)


async def _column_exists(cur, table: str, column: str) -> bool:
    await cur.execute("SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s", (table, column))
    return await cur.fetchone() is not None


async def _index_exists(cur, table: str, index: str) -> bool:
    await cur.execute("SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s", (table, index))
    return await cur.fetchone() is not None


async def _constraint_exists(cur, name: str) -> bool:
    await cur.execute("SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME=%s", (name,))
    return await cur.fetchone() is not None


async def _check_clause(cur, name: str) -> str | None:
    await cur.execute("SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME=%s", (name,))
    row = await cur.fetchone()
    return None if row is None else str(row[0])


async def _ensure_ascii_bin(cur, column: str) -> None:
    await cur.execute(
        "SELECT CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_authorization_event' AND COLUMN_NAME=%s",
        (column,),
    )
    metadata = await cur.fetchone()
    if metadata is None:
        raise RuntimeError(f"Office audit column {column} is absent")
    if tuple(metadata) == ("ascii", "ascii_bin"):
        return
    # Reject lossy conversion before ALTER; audit rows are evidence and are
    # never rewritten merely to make an upgrade convenient.
    await cur.execute(
        f"SELECT 1 FROM office_authorization_event WHERE {column} IS NOT NULL "
        f"AND BINARY {column} != BINARY CONVERT(CONVERT({column} USING ascii) USING utf8mb4) LIMIT 1"
    )
    if await cur.fetchone() is not None:
        raise RuntimeError(f"Office audit column {column} contains non-ASCII historic evidence")
    await cur.execute(
        f"ALTER TABLE office_authorization_event MODIFY COLUMN {column} VARCHAR(64) "
        "CHARACTER SET ascii COLLATE ascii_bin NULL"
    )
    await cur.execute(
        "SELECT CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_authorization_event' AND COLUMN_NAME=%s",
        (column,),
    )
    if tuple(await cur.fetchone() or ()) != ("ascii", "ascii_bin"):
        raise RuntimeError(f"Office audit column {column} did not upgrade to ascii_bin")


async def invalidate_office_runtime_revision(cur) -> None:
    """Remove readiness before a runner can apply a new Office revision."""
    await cur.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_schema_revision'")
    if await cur.fetchone() is not None:
        if await _column_exists(cur, "office_schema_revision", "component"):
            await cur.execute("DELETE FROM office_schema_revision WHERE component=%s", (RUNTIME_COMPONENT,))
        else:
            # Pre-component table has only Office's legacy readiness row.
            await cur.execute("DELETE FROM office_schema_revision")


async def stamp_office_runtime_revision(cur) -> None:
    """Mark the component ready only after the entire platform runner succeeds."""
    await cur.execute(
        "INSERT INTO office_schema_revision (component,revision,applied_at) VALUES (%s,%s,NOW(6)) "
        "ON DUPLICATE KEY UPDATE revision=VALUES(revision), applied_at=VALUES(applied_at)",
        (RUNTIME_COMPONENT, RUNTIME_REVISION),
    )


async def apply_office_migrations(cursor) -> None:
    """Apply only Office-owned additive DDL after JAX's namespace migration."""
    if not await _column_exists(cursor, "jax_project_scope", "authorization_namespace"):
        raise RuntimeError("JAX authorization_namespace migration is required before Virtual Office")
    if not await _column_exists(cursor, "jax_users", "account_class"):
        await cursor.execute("ALTER TABLE jax_users ADD COLUMN account_class VARCHAR(24) NOT NULL DEFAULT 'INTERNAL', ADD CONSTRAINT chk_jax_users_account_class CHECK (account_class IN ('INTERNAL','OFFICE_INTERNAL','EXTERNAL_CLIENT'))")
    for statement in OFFICE_DDL:
        await cursor.execute(statement)
    if not await _column_exists(cursor, "office_schema_revision", "component"):
        await cursor.execute("ALTER TABLE office_schema_revision ADD COLUMN component VARCHAR(64) NULL FIRST")
        await cursor.execute("UPDATE office_schema_revision SET component=%s WHERE component IS NULL", (RUNTIME_COMPONENT,))
        await cursor.execute("ALTER TABLE office_schema_revision MODIFY component VARCHAR(64) NOT NULL, DROP PRIMARY KEY, ADD PRIMARY KEY (component)")
    if not await _index_exists(cursor, "office_recipient_group", "uq_office_recipient_group_id_project"):
        await cursor.execute("ALTER TABLE office_recipient_group ADD UNIQUE KEY uq_office_recipient_group_id_project (recipient_group_id,project_id)")
    if not await _constraint_exists(cursor, "fk_office_external_relation_group_project"):
        await cursor.execute("ALTER TABLE office_external_relation ADD CONSTRAINT fk_office_external_relation_group_project FOREIGN KEY (recipient_group_id,project_id) REFERENCES office_recipient_group(recipient_group_id,project_id)")
    if not await _constraint_exists(cursor, "fk_office_authorization_event_scope"):
        await cursor.execute("ALTER TABLE office_authorization_event ADD CONSTRAINT fk_office_authorization_event_scope FOREIGN KEY (project_id,tenant_id) REFERENCES jax_project_scope(project_id,tenant_id)")
    if not await _constraint_exists(cursor, "fk_office_authorization_event_project"):
        await cursor.execute("ALTER TABLE office_authorization_event ADD CONSTRAINT fk_office_authorization_event_project FOREIGN KEY (project_id) REFERENCES projects(id)")
    if not await _constraint_exists(cursor, "fk_office_authorization_event_tenant"):
        await cursor.execute("ALTER TABLE office_authorization_event ADD CONSTRAINT fk_office_authorization_event_tenant FOREIGN KEY (tenant_id) REFERENCES jax_tenants(tenant_id)")
    delegation_clause = await _check_clause(cursor, "chk_office_admin_delegation_capability")
    if delegation_clause is not None and "CREATE_OFFICE_PROJECT" in delegation_clause:
        await cursor.execute("ALTER TABLE office_admin_delegation DROP CONSTRAINT chk_office_admin_delegation_capability")
        delegation_clause = None
    if delegation_clause is None:
        await cursor.execute("ALTER TABLE office_admin_delegation ADD CONSTRAINT chk_office_admin_delegation_capability CHECK (capability IN ('ADMINISTER_OFFICE'))")
    account_clause = await _check_clause(cursor, "chk_jax_users_account_class")
    if account_clause is not None and "OFFICE_INTERNAL" not in account_clause:
        await cursor.execute("ALTER TABLE jax_users DROP CONSTRAINT chk_jax_users_account_class")
        account_clause = None
    if account_clause is None:
        await cursor.execute("ALTER TABLE jax_users ADD CONSTRAINT chk_jax_users_account_class CHECK (account_class IN ('INTERNAL','OFFICE_INTERNAL','EXTERNAL_CLIENT'))")
    # Runtime fence fields are additive and intentionally use no pseudo-user:
    # SYSTEM_FENCE has NULL actor_user_id and a canonical system principal.
    await cursor.execute("ALTER TABLE office_authorization_event MODIFY actor_user_id INT NULL")
    await cursor.execute("ALTER TABLE office_authorization_event ADD COLUMN IF NOT EXISTS requested_project_id INT NULL AFTER operation, ADD COLUMN IF NOT EXISTS actor_principal VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL AFTER actor_user_id, ADD COLUMN IF NOT EXISTS event_kind VARCHAR(24) NOT NULL DEFAULT 'AUTHORIZATION' AFTER actor_principal, ADD COLUMN IF NOT EXISTS fence_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL AFTER event_kind")
    if not await _index_exists(cursor, "office_authorization_event", "uq_office_authorization_event_fence"):
        await cursor.execute("ALTER TABLE office_authorization_event ADD UNIQUE KEY uq_office_authorization_event_fence (fence_key)")
    await _ensure_ascii_bin(cursor, "actor_principal")
    await _ensure_ascii_bin(cursor, "fence_key")
    result_clause = await _check_clause(cursor, "chk_office_authorization_event_result")
    if result_clause is not None and not _is_canonical_audit_result(result_clause):
        raise RuntimeError("Office audit result constraint is incompatible; historic evidence is not rewritten")
    if result_clause is None:
        await cursor.execute("ALTER TABLE office_authorization_event ADD CONSTRAINT chk_office_authorization_event_result CHECK (result IN ('ALLOWED','DENIED','RECORDED'))")
    shape_clause = await _check_clause(cursor, "chk_office_authorization_event_shape")
    if shape_clause is not None and not _is_canonical_audit_shape(shape_clause):
        raise RuntimeError("Office audit shape constraint is incompatible; historic evidence is not rewritten")
    if shape_clause is None:
        await cursor.execute(
            "ALTER TABLE office_authorization_event ADD CONSTRAINT chk_office_authorization_event_shape CHECK ("
            "(event_kind='AUTHORIZATION' AND actor_user_id IS NOT NULL AND actor_principal IS NULL AND fence_key IS NULL "
            "AND result IN ('ALLOWED','DENIED') AND operation <> 'AUDIT_FENCE_CANARY') "
            "OR (event_kind='SYSTEM_FENCE' AND actor_user_id IS NULL "
            "AND actor_principal IS NOT NULL AND BINARY actor_principal=BINARY 'system:office-schema-migrator' "
            "AND fence_key IS NOT NULL AND BINARY fence_key=BINARY 'office-audit-fence-v1' "
            "AND operation='AUDIT_FENCE_CANARY' AND requested_project_id IS NULL AND project_id IS NULL AND tenant_id IS NULL "
            "AND result='RECORDED' AND reason_code='office_audit_fence_v1' AND correlation_id IS NULL)"
            ")"
        )
    await cursor.execute("SELECT actor_user_id,actor_principal,event_kind,fence_key,operation,project_id,tenant_id,result,reason_code,correlation_id FROM office_authorization_event WHERE fence_key='office-audit-fence-v1'")
    row = await cursor.fetchone()
    canonical = (None, 'system:office-schema-migrator', 'SYSTEM_FENCE', 'office-audit-fence-v1', 'AUDIT_FENCE_CANARY', None, None, 'RECORDED', 'office_audit_fence_v1', None)
    if row is None:
        await cursor.execute("INSERT INTO office_authorization_event (actor_user_id,actor_principal,event_kind,fence_key,operation,project_id,tenant_id,result,reason_code,correlation_id,occurred_at) VALUES (NULL,'system:office-schema-migrator','SYSTEM_FENCE','office-audit-fence-v1','AUDIT_FENCE_CANARY',NULL,NULL,'RECORDED','office_audit_fence_v1',NULL,NOW(6))")
    elif tuple(row) != canonical:
        raise RuntimeError("Office audit fence is not canonical")
