from __future__ import annotations

import hashlib

OFFICE_DDL = (
    "CREATE TABLE IF NOT EXISTS office_account_profile (user_id INT NOT NULL PRIMARY KEY, global_role VARCHAR(24) NOT NULL DEFAULT 'COLLABORATOR', status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', version BIGINT NOT NULL DEFAULT 1, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6), CONSTRAINT fk_office_account_profile_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT chk_office_account_profile_role CHECK (global_role IN ('COLLABORATOR','GENERAL_ADMIN','OFFICE_SUPERUSER')), CONSTRAINT chk_office_account_profile_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_admin_delegation (delegation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, capability VARCHAR(40) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', granted_by INT NOT NULL, version BIGINT NOT NULL DEFAULT 1, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_admin_delegation (user_id,capability), KEY idx_office_admin_delegation_scope (user_id,capability,status), CONSTRAINT fk_office_admin_delegation_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_admin_delegation_granted_by FOREIGN KEY (granted_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_admin_delegation_capability CHECK (capability IN ('ADMINISTER_OFFICE')), CONSTRAINT chk_office_admin_delegation_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_project_grant (grant_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, user_id INT NOT NULL, grant_role VARCHAR(24) NOT NULL, audience VARCHAR(24) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', version BIGINT NOT NULL DEFAULT 1, granted_by INT NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_project_grant_subject (project_id,user_id), KEY idx_office_project_grant_scope (project_id,user_id,status,audience), KEY idx_office_project_grant_list (user_id,status,audience,project_id,grant_role), CONSTRAINT fk_office_project_grant_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_project_grant_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_project_grant_granted_by FOREIGN KEY (granted_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_project_grant_role CHECK (grant_role IN ('PROJECT_ADMIN','COLLABORATOR')), CONSTRAINT chk_office_project_grant_audience CHECK (audience IN ('INTERNAL','CLIENT_PUBLISHED')), CONSTRAINT chk_office_project_grant_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_recipient_group (recipient_group_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, tenant_id INT NOT NULL, group_kind VARCHAR(16) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', created_by INT NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_recipient_group_project_kind (project_id,group_kind), UNIQUE KEY uq_office_recipient_group_id_project (recipient_group_id,project_id), KEY idx_office_recipient_group_scope (project_id,status), CONSTRAINT fk_office_recipient_group_scope FOREIGN KEY (project_id,tenant_id) REFERENCES jax_project_scope(project_id,tenant_id), CONSTRAINT fk_office_recipient_group_created_by FOREIGN KEY (created_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_recipient_group_kind CHECK (group_kind='CLIENT'), CONSTRAINT chk_office_recipient_group_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_external_relation (relation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, user_id INT NOT NULL, recipient_group_id BIGINT UNSIGNED NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', version BIGINT NOT NULL DEFAULT 1, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_external_relation_subject (project_id,user_id), KEY idx_office_external_relation_scope (project_id,user_id,status,recipient_group_id), KEY idx_office_external_relation_list (user_id,status,project_id,recipient_group_id), CONSTRAINT fk_office_external_relation_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_external_relation_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_external_relation_group FOREIGN KEY (recipient_group_id) REFERENCES office_recipient_group(recipient_group_id), CONSTRAINT fk_office_external_relation_group_project FOREIGN KEY (recipient_group_id,project_id) REFERENCES office_recipient_group(recipient_group_id,project_id), CONSTRAINT chk_office_external_relation_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_professional_grant (professional_grant_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, user_id INT NOT NULL, capability VARCHAR(64) NOT NULL, basis VARCHAR(128) NOT NULL, evidence_ref VARCHAR(255) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', granted_by INT NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_professional_grant (project_id,user_id,capability), KEY idx_office_professional_grant_scope (project_id,user_id,capability,status), CONSTRAINT fk_office_professional_grant_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_professional_grant_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_professional_grant_granted_by FOREIGN KEY (granted_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_professional_grant_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_authorization_event (event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, actor_user_id INT NOT NULL, operation VARCHAR(40) NOT NULL, requested_project_id INT NULL, project_id INT NULL, tenant_id INT NULL, result VARCHAR(16) NOT NULL, reason_code VARCHAR(64) NOT NULL, correlation_id VARCHAR(128) NULL, occurred_at DATETIME(6) NOT NULL, KEY idx_office_authorization_event_scope (project_id,tenant_id,occurred_at), KEY idx_office_authorization_event_actor (actor_user_id,occurred_at), CONSTRAINT fk_office_authorization_event_actor FOREIGN KEY (actor_user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_authorization_event_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_authorization_event_tenant FOREIGN KEY (tenant_id) REFERENCES jax_tenants(tenant_id), CONSTRAINT fk_office_authorization_event_scope FOREIGN KEY (project_id,tenant_id) REFERENCES jax_project_scope(project_id,tenant_id), CONSTRAINT chk_office_authorization_event_result CHECK (result IN ('ALLOWED','DENIED','RECORDED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_authorization_event BEFORE UPDATE ON office_authorization_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_authorization_event append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_authorization_event BEFORE DELETE ON office_authorization_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_authorization_event append-only'",
    "CREATE TABLE IF NOT EXISTS office_schema_revision (component VARCHAR(64) NOT NULL PRIMARY KEY, revision VARCHAR(64) NOT NULL, applied_at DATETIME(6) NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
)

RUNTIME_COMPONENT = "virtual-office"
RUNTIME_REVISION = "office-runtime-v6-bootstrap-state"

# MFA values are always either Fernet ciphertext or SHA-256 digests.  Raw
# enrollment/challenge/recovery capabilities never enter durable storage.
OFFICE_MFA_DDL = (
    "CREATE TABLE IF NOT EXISTS office_mfa_credential (user_id INT NOT NULL PRIMARY KEY, state VARCHAR(24) NOT NULL, secret_ciphertext TEXT NULL, pending_secret_ciphertext TEXT NULL, pending_enrollment_id BINARY(32) NULL, pending_expires_at DATETIME(6) NULL, confirmed_at DATETIME(6) NULL, created_at DATETIME(6) NOT NULL, updated_at DATETIME(6) NOT NULL, CONSTRAINT fk_office_mfa_credential_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT chk_office_mfa_credential_state CHECK (state IN ('PENDING','ACTIVE','RECOVERY_REQUIRED','DISABLED')), CONSTRAINT chk_office_mfa_credential_shape CHECK ((state='PENDING' AND secret_ciphertext IS NULL AND pending_secret_ciphertext IS NOT NULL AND pending_enrollment_id IS NOT NULL AND pending_expires_at IS NOT NULL) OR (state='ACTIVE' AND secret_ciphertext IS NOT NULL AND ((pending_secret_ciphertext IS NULL AND pending_enrollment_id IS NULL AND pending_expires_at IS NULL) OR (pending_secret_ciphertext IS NOT NULL AND pending_enrollment_id IS NOT NULL AND pending_expires_at IS NOT NULL))) OR (state IN ('RECOVERY_REQUIRED','DISABLED') AND secret_ciphertext IS NULL AND pending_secret_ciphertext IS NULL AND pending_enrollment_id IS NULL AND pending_expires_at IS NULL))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_mfa_totp_use (user_id INT NOT NULL, mfa_version BIGINT NOT NULL, time_counter BIGINT NOT NULL, purpose VARCHAR(24) NOT NULL, used_at DATETIME(6) NOT NULL, PRIMARY KEY (user_id,mfa_version,time_counter), CONSTRAINT fk_office_mfa_totp_use_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT chk_office_mfa_totp_use_version CHECK (mfa_version >= 0), CONSTRAINT chk_office_mfa_totp_use_purpose CHECK (purpose IN ('LOGIN','STEP_UP','ENROLLMENT','RECOVERY'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_mfa_recovery_code (recovery_code_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, mfa_version BIGINT NOT NULL, code_hash BINARY(32) NOT NULL, used_at DATETIME(6) NULL, created_at DATETIME(6) NOT NULL, UNIQUE KEY uq_office_mfa_recovery_code_hash (code_hash), KEY idx_office_mfa_recovery_code_user (user_id,mfa_version,used_at), CONSTRAINT fk_office_mfa_recovery_code_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT chk_office_mfa_recovery_code_version CHECK (mfa_version >= 0)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_mfa_challenge (token_hash BINARY(32) NOT NULL PRIMARY KEY, challenge_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT UNIQUE, user_id INT NOT NULL, purpose VARCHAR(24) NOT NULL, expected_token_version BIGINT NOT NULL, expected_mfa_version BIGINT NOT NULL, attempt_count INT NOT NULL DEFAULT 0, max_attempts INT NOT NULL, expires_at DATETIME(6) NOT NULL, state VARCHAR(16) NOT NULL DEFAULT 'ISSUED', used_at DATETIME(6) NULL, created_at DATETIME(6) NOT NULL, UNIQUE KEY uq_office_mfa_challenge_subject (challenge_id,user_id), KEY idx_office_mfa_challenge_user (user_id,purpose,expected_token_version,expected_mfa_version,state,expires_at), CONSTRAINT fk_office_mfa_challenge_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT chk_office_mfa_challenge_purpose CHECK (purpose IN ('LOGIN','STEP_UP','ENROLLMENT','RECOVERY')), CONSTRAINT chk_office_mfa_challenge_versions CHECK (expected_token_version >= 0 AND expected_mfa_version >= 0), CONSTRAINT chk_office_mfa_challenge_attempts CHECK (attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts), CONSTRAINT chk_office_mfa_challenge_state CHECK (state IN ('ISSUED','CONSUMED','EXHAUSTED','EXPIRED','INVALIDATED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_mfa_security_event (event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, event_kind VARCHAR(24) NOT NULL, actor_user_id INT NULL, principal_kind VARCHAR(32) NOT NULL, challenge_id BIGINT UNSIGNED NULL, subject_user_id INT NULL, operation VARCHAR(40) NULL, purpose VARCHAR(24) NULL, token_version BIGINT NULL, mfa_version BIGINT NULL, previous_token_version BIGINT NULL, previous_mfa_version BIGINT NULL, proof_event_id BIGINT UNSIGNED NULL, authorization_event_id BIGINT UNSIGNED NULL, result VARCHAR(16) NOT NULL, server_correlation_id BINARY(16) NULL, occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), fence_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL, UNIQUE KEY uq_office_mfa_security_event_fence (fence_key), KEY idx_office_mfa_security_event_proof (subject_user_id,purpose,token_version,mfa_version,occurred_at,event_id), KEY idx_office_mfa_security_event_challenge (challenge_id,subject_user_id), CONSTRAINT fk_office_mfa_security_event_actor FOREIGN KEY (actor_user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_mfa_security_event_subject FOREIGN KEY (subject_user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_mfa_security_event_challenge_subject FOREIGN KEY (challenge_id,subject_user_id) REFERENCES office_mfa_challenge(challenge_id,user_id), CONSTRAINT fk_office_mfa_security_event_proof FOREIGN KEY (proof_event_id) REFERENCES office_mfa_security_event(event_id), CONSTRAINT fk_office_mfa_security_event_authorization FOREIGN KEY (authorization_event_id) REFERENCES office_authorization_event(event_id), CONSTRAINT chk_office_mfa_security_event_shape CHECK ((event_kind='MFA_PROOF' AND operation='MFA_PROOF' AND subject_user_id IS NOT NULL AND purpose IN ('LOGIN','STEP_UP','ENROLLMENT','RECOVERY') AND token_version >= 0 AND mfa_version >= 0 AND previous_token_version IS NULL AND previous_mfa_version IS NULL AND proof_event_id IS NULL AND authorization_event_id IS NULL AND result IN ('VERIFIED','DENIED') AND ((principal_kind='USER_SESSION' AND actor_user_id IS NOT NULL AND challenge_id IS NULL) OR (principal_kind IN ('PASSWORD_PREAUTH','RECOVERY_CAPABILITY','ENROLLMENT_CAPABILITY') AND actor_user_id IS NULL AND challenge_id IS NOT NULL))) OR (event_kind='MFA_STATE_CHANGE' AND subject_user_id IS NOT NULL AND operation IN ('ENROLLMENT_CONFIRM','RECOVERY_CONSUME','MFA_DISABLE','MFA_RESET') AND purpose IN ('ENROLLMENT','RECOVERY') AND token_version >= 0 AND mfa_version >= 0 AND previous_token_version >= 0 AND previous_mfa_version >= 0 AND proof_event_id IS NOT NULL AND result='RECORDED' AND ((principal_kind='USER_SESSION' AND actor_user_id IS NOT NULL) OR (principal_kind IN ('RECOVERY_CAPABILITY','ENROLLMENT_CAPABILITY') AND actor_user_id IS NULL AND challenge_id IS NOT NULL))) OR (event_kind='SYSTEM_FENCE' AND actor_user_id IS NULL AND principal_kind='SYSTEM_MIGRATOR' AND challenge_id IS NULL AND subject_user_id IS NULL AND operation IS NULL AND purpose IS NULL AND token_version IS NULL AND mfa_version IS NULL AND previous_token_version IS NULL AND previous_mfa_version IS NULL AND proof_event_id IS NULL AND authorization_event_id IS NULL AND result='RECORDED' AND server_correlation_id IS NULL AND fence_key IS NOT NULL AND BINARY fence_key=BINARY 'office-mfa-fence-v1'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_mfa_security_event BEFORE UPDATE ON office_mfa_security_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_mfa_security_event append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_mfa_security_event BEFORE DELETE ON office_mfa_security_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_mfa_security_event append-only'",
)

# This follows the MFA ledger because its durable proof reference is an FK.
# ``credential_audit_id`` deliberately stays signed: user_admin_audit.id is
# BIGINT, unlike the unsigned Office MFA ledger IDs.
OFFICE_BOOTSTRAP_DDL = (
    "CREATE TABLE IF NOT EXISTS office_bootstrap_state (ceremony_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY, target_user_id INT NOT NULL, identity_actor_user_id INT NOT NULL, identity_proof_event_id BIGINT UNSIGNED NOT NULL, credential_audit_id BIGINT NOT NULL, basis_ref VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, completed_at DATETIME(6) NOT NULL, CONSTRAINT fk_office_bootstrap_target FOREIGN KEY (target_user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_bootstrap_actor FOREIGN KEY (identity_actor_user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_bootstrap_proof FOREIGN KEY (identity_proof_event_id) REFERENCES office_mfa_security_event(event_id), CONSTRAINT fk_office_bootstrap_credential_audit FOREIGN KEY (credential_audit_id) REFERENCES user_admin_audit(id), CONSTRAINT chk_office_bootstrap_identity CHECK (target_user_id=identity_actor_user_id), CONSTRAINT chk_office_bootstrap_basis CHECK (basis_ref <> '')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_bootstrap_state BEFORE UPDATE ON office_bootstrap_state FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_bootstrap_state append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_bootstrap_state BEFORE DELETE ON office_bootstrap_state FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_bootstrap_state append-only'",
)

# Exact MariaDB 12.3.3 CHECK_CLAUSE renderings, measured from the constraint
# created below.  Normalization deliberately preserves boolean operators and
# literals; accepting a bag of words would turn a permissive OR into a guard.
_CANONICAL_AUDIT_SHAPE = """`event_kind` = 'AUTHORIZATION' and `actor_user_id` is not null and `actor_principal` is null and `fence_key` is null and `result` in ('ALLOWED','DENIED') and `operation` <> 'AUDIT_FENCE_CANARY' or `event_kind` = 'SYSTEM_FENCE' and `actor_user_id` is null and `actor_principal` is not null and cast(`actor_principal` as char charset binary) = cast('system:office-schema-migrator' as char charset binary) and `fence_key` is not null and cast(`fence_key` as char charset binary) = cast('office-audit-fence-v1' as char charset binary) and `operation` = 'AUDIT_FENCE_CANARY' and `requested_project_id` is null and `project_id` is null and `tenant_id` is null and `result` = 'RECORDED' and `reason_code` = 'office_audit_fence_v1' and `correlation_id` is null"""
_CANONICAL_AUDIT_RESULT = "`result` in ('ALLOWED','DENIED','RECORDED')"

# SHA-256 values of the whitespace-normalized CHECK_CLAUSE values emitted by
# MariaDB 12.3.3 for the DDL above.  They intentionally do not normalize case,
# parentheses, operators, or string literals: those are part of the guard.
_CANONICAL_MFA_CHECK_DIGESTS = {
    "chk_office_mfa_security_event_fence": "2d6660b9a27057373a0ad21bbd9439aecae1c45288b562da51c4940fc28d28cf",
    "chk_office_mfa_security_event_shape": "8d4c5bf6f0e328d7a4760fb904bc9c5daf5f7678d41ee5d5523b3daece2b820e",
    "chk_office_mfa_security_event_truth": "36919ea1fd3b714a368ff49ac5c8416bd4e412d2f460aab9e8af567f0498803b",
}

_CANONICAL_V4_MFA_CHECK_DIGESTS = {
    "chk_office_mfa_security_event_fence": "2d6660b9a27057373a0ad21bbd9439aecae1c45288b562da51c4940fc28d28cf",
    "chk_office_mfa_security_event_shape": "8920714b7142b37c55ffc599c0a205bda7c16c1eab5cc30ca172a4aa1dca2ae4",
    "chk_office_mfa_security_event_truth": "a1f06b9717b4349ec37bc52ff16013e9653668b84771e66eb63ab93d4f3a291d",
}

_MFA_V5_CHECK_SQL = {
    "chk_office_mfa_security_event_fence": "CHECK ((event_kind='SYSTEM_FENCE' AND fence_key IS NOT NULL AND BINARY fence_key=BINARY 'office-mfa-fence-v1') OR (event_kind IN ('MFA_PROOF','MFA_STATE_CHANGE') AND fence_key IS NULL))",
    "chk_office_mfa_security_event_shape": "CHECK ((event_kind='MFA_PROOF' AND operation='MFA_PROOF' AND subject_user_id IS NOT NULL AND purpose IN ('LOGIN','STEP_UP','ENROLLMENT','RECOVERY') AND token_version IS NOT NULL AND token_version>=0 AND mfa_version IS NOT NULL AND mfa_version>=0 AND previous_token_version IS NULL AND previous_mfa_version IS NULL AND proof_event_id IS NULL AND authorization_event_id IS NULL AND result IN ('VERIFIED','DENIED') AND server_correlation_id IS NOT NULL) OR (event_kind='MFA_STATE_CHANGE' AND subject_user_id IS NOT NULL AND operation IN ('ENROLLMENT_CONFIRM','RECOVERY_CONSUME','RECOVERY_REGENERATE','MFA_DISABLE','MFA_RESET') AND token_version IS NOT NULL AND token_version>=0 AND mfa_version IS NOT NULL AND mfa_version>=0 AND previous_token_version IS NOT NULL AND previous_token_version>=0 AND previous_mfa_version IS NOT NULL AND previous_mfa_version>=0 AND proof_event_id IS NOT NULL AND result='RECORDED' AND server_correlation_id IS NOT NULL) OR (event_kind='SYSTEM_FENCE' AND actor_user_id IS NULL AND principal_kind='SYSTEM_MIGRATOR' AND challenge_id IS NULL AND subject_user_id IS NULL AND operation IS NULL AND purpose IS NULL AND token_version IS NULL AND mfa_version IS NULL AND previous_token_version IS NULL AND previous_mfa_version IS NULL AND proof_event_id IS NULL AND authorization_event_id IS NULL AND result='RECORDED' AND server_correlation_id IS NULL AND BINARY fence_key=BINARY 'office-mfa-fence-v1'))",
    "chk_office_mfa_security_event_truth": "CHECK (COALESCE((BINARY event_kind=BINARY 'SYSTEM_FENCE' AND actor_user_id IS NULL AND BINARY principal_kind=BINARY 'SYSTEM_MIGRATOR' AND challenge_id IS NULL AND subject_user_id IS NULL AND operation IS NULL AND purpose IS NULL AND token_version IS NULL AND mfa_version IS NULL AND previous_token_version IS NULL AND previous_mfa_version IS NULL AND proof_event_id IS NULL AND authorization_event_id IS NULL AND BINARY result=BINARY 'RECORDED' AND server_correlation_id IS NULL AND BINARY fence_key=BINARY 'office-mfa-fence-v1') OR (BINARY event_kind=BINARY 'MFA_PROOF' AND BINARY operation=BINARY 'MFA_PROOF' AND ((BINARY purpose=BINARY 'LOGIN' AND BINARY principal_kind=BINARY 'PASSWORD_PREAUTH' AND actor_user_id IS NULL AND challenge_id IS NOT NULL) OR (BINARY purpose=BINARY 'STEP_UP' AND BINARY principal_kind=BINARY 'USER_SESSION' AND actor_user_id=subject_user_id AND challenge_id IS NULL) OR (BINARY purpose=BINARY 'ENROLLMENT' AND BINARY principal_kind=BINARY 'ENROLLMENT_CAPABILITY' AND actor_user_id IS NULL AND challenge_id IS NOT NULL) OR (BINARY purpose=BINARY 'RECOVERY' AND BINARY principal_kind=BINARY 'RECOVERY_CAPABILITY' AND actor_user_id IS NULL AND challenge_id IS NOT NULL)) AND authorization_event_id IS NULL) OR (BINARY event_kind=BINARY 'MFA_STATE_CHANGE' AND ((BINARY operation=BINARY 'ENROLLMENT_CONFIRM' AND BINARY principal_kind=BINARY 'ENROLLMENT_CAPABILITY' AND actor_user_id IS NULL AND challenge_id IS NOT NULL AND BINARY purpose=BINARY 'ENROLLMENT') OR (BINARY operation=BINARY 'RECOVERY_CONSUME' AND BINARY principal_kind=BINARY 'RECOVERY_CAPABILITY' AND actor_user_id IS NULL AND challenge_id IS NOT NULL AND BINARY purpose=BINARY 'RECOVERY') OR (BINARY operation=BINARY 'RECOVERY_REGENERATE' AND BINARY principal_kind=BINARY 'USER_SESSION' AND actor_user_id=subject_user_id AND challenge_id IS NULL AND BINARY purpose=BINARY 'RECOVERY') OR (BINARY operation=BINARY 'MFA_DISABLE' AND BINARY principal_kind=BINARY 'USER_SESSION' AND actor_user_id=subject_user_id AND challenge_id IS NULL AND BINARY purpose=BINARY 'STEP_UP') OR (BINARY operation=BINARY 'MFA_RESET' AND BINARY principal_kind=BINARY 'USER_SESSION' AND actor_user_id IS NOT NULL AND actor_user_id<>subject_user_id AND challenge_id IS NULL AND BINARY purpose=BINARY 'STEP_UP' AND authorization_event_id IS NOT NULL)) AND ((BINARY operation=BINARY 'MFA_RESET') OR authorization_event_id IS NULL)),FALSE))",
}

_MFA_CRITICAL_COLUMNS = {
    ("office_mfa_challenge", "challenge_id"): ("bigint", "bigint(20) unsigned", "NO", None, None, "auto_increment", None),
    ("office_mfa_security_event", "event_id"): ("bigint", "bigint(20) unsigned", "NO", None, None, "auto_increment", None),
    ("office_mfa_security_event", "fence_key"): ("varchar", "varchar(64)", "YES", "ascii", "ascii_bin", "", "NULL"),
}

_MFA_FOREIGN_KEYS = {
    "fk_office_mfa_security_event_actor": (("actor_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
    "fk_office_mfa_security_event_subject": (("subject_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
    "fk_office_mfa_security_event_challenge_subject": (("challenge_id", "subject_user_id"), "office_mfa_challenge", ("challenge_id", "user_id"), "RESTRICT", "RESTRICT"),
    "fk_office_mfa_security_event_proof": (("proof_event_id",), "office_mfa_security_event", ("event_id",), "RESTRICT", "RESTRICT"),
    "fk_office_mfa_security_event_authorization": (("authorization_event_id",), "office_authorization_event", ("event_id",), "RESTRICT", "RESTRICT"),
}

_MFA_TRIGGER_METADATA = {
    "no_update_office_mfa_security_event": ("office_mfa_security_event", "BEFORE", "UPDATE", "ROW", "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_mfa_security_event append-only'"),
    "no_delete_office_mfa_security_event": ("office_mfa_security_event", "BEFORE", "DELETE", "ROW", "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_mfa_security_event append-only'"),
}

_BOOTSTRAP_FOREIGN_KEYS = {
    "fk_office_bootstrap_target": (("target_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
    "fk_office_bootstrap_actor": (("identity_actor_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
    "fk_office_bootstrap_proof": (("identity_proof_event_id",), "office_mfa_security_event", ("event_id",), "RESTRICT", "RESTRICT"),
    "fk_office_bootstrap_credential_audit": (("credential_audit_id",), "user_admin_audit", ("id",), "RESTRICT", "RESTRICT"),
}

_BOOTSTRAP_TRIGGER_METADATA = {
    "no_update_office_bootstrap_state": (
        "office_bootstrap_state", "BEFORE", "UPDATE", "ROW",
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_bootstrap_state append-only'",
    ),
    "no_delete_office_bootstrap_state": (
        "office_bootstrap_state", "BEFORE", "DELETE", "ROW",
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_bootstrap_state append-only'",
    ),
}

_BOOTSTRAP_CHECKS = {
    "chk_office_bootstrap_identity": "`target_user_id` = `identity_actor_user_id`",
    "chk_office_bootstrap_basis": "`basis_ref` <> ''",
}

_MFA_UNIQUE_INDEXES = {
    ("office_mfa_challenge", "PRIMARY"): ("token_hash",),
    ("office_mfa_challenge", "challenge_id"): ("challenge_id",),
    ("office_mfa_challenge", "uq_office_mfa_challenge_subject"): ("challenge_id", "user_id"),
    ("office_mfa_security_event", "PRIMARY"): ("event_id",),
    ("office_mfa_security_event", "uq_office_mfa_security_event_fence"): ("fence_key",),
}

_MFA_BUDGET_INDEX = ("user_id", "purpose", "expected_token_version", "expected_mfa_version", "state", "expires_at")
_MFA_V4_BUDGET_INDEX = ("user_id", "state", "expires_at")
_MFA_CHALLENGE_STATE_V4 = "873de927db998ad7d6664ee6e849c3b09f6f3ec57afd20f1316fc90d8784eb9a"
_MFA_CHALLENGE_STATE_V5 = "2ea7f4621b9352e2c26ae6f5bf2826ccbcd3bda1c748fe0b1dcf6ae625444dc3"
_MFA_CREDENTIAL_SHAPE_V4 = "62aabe4d405a17df1389ad19c54d2380074d85346a1a9e09bc905e296e96e01c"
_MFA_CREDENTIAL_SHAPE_V5 = "8f6da6c31260662ef5833b216f54b834491089325e817ae1ceb7547f3935d8c9"
_MFA_CREDENTIAL_SHAPE_V4_SQL = "CHECK ((state='PENDING' AND secret_ciphertext IS NULL AND pending_secret_ciphertext IS NOT NULL AND pending_enrollment_id IS NOT NULL AND pending_expires_at IS NOT NULL) OR (state='ACTIVE' AND secret_ciphertext IS NOT NULL AND pending_secret_ciphertext IS NULL AND pending_enrollment_id IS NULL AND pending_expires_at IS NULL) OR (state IN ('RECOVERY_REQUIRED','DISABLED') AND secret_ciphertext IS NULL AND pending_secret_ciphertext IS NULL AND pending_enrollment_id IS NULL AND pending_expires_at IS NULL))"
_MFA_CREDENTIAL_SHAPE_V5_SQL = "CHECK ((state='PENDING' AND secret_ciphertext IS NULL AND pending_secret_ciphertext IS NOT NULL AND pending_enrollment_id IS NOT NULL AND pending_expires_at IS NOT NULL) OR (state='ACTIVE' AND secret_ciphertext IS NOT NULL AND ((pending_secret_ciphertext IS NULL AND pending_enrollment_id IS NULL AND pending_expires_at IS NULL) OR (pending_secret_ciphertext IS NOT NULL AND pending_enrollment_id IS NOT NULL AND pending_expires_at IS NOT NULL))) OR (state IN ('RECOVERY_REQUIRED','DISABLED') AND secret_ciphertext IS NULL AND pending_secret_ciphertext IS NULL AND pending_enrollment_id IS NULL AND pending_expires_at IS NULL))"
_MFA_CHALLENGE_STATE_V4_SQL = "CHECK (state IN ('ISSUED','CONSUMED','EXHAUSTED','EXPIRED'))"
_MFA_CHALLENGE_STATE_V5_SQL = "CHECK (state IN ('ISSUED','CONSUMED','EXHAUSTED','EXPIRED','INVALIDATED'))"


def _normalize_mariadb_check(clause: str | None) -> str | None:
    return None if clause is None else " ".join(clause.split())


def _check_digest(clause: str | None) -> str | None:
    normalized = _normalize_mariadb_check(clause)
    return None if normalized is None else hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _is_canonical_mfa_check(name: str, clause: str | None) -> bool:
    expected = _CANONICAL_MFA_CHECK_DIGESTS.get(name)
    return _check_digest(clause) == expected


def _is_canonical_audit_shape(clause: str | None) -> bool:
    return _normalize_mariadb_check(clause) == _normalize_mariadb_check(_CANONICAL_AUDIT_SHAPE)


def _is_canonical_audit_result(clause: str | None) -> bool:
    return _normalize_mariadb_check(clause) == _normalize_mariadb_check(_CANONICAL_AUDIT_RESULT)


async def _column_exists(cur, table: str, column: str) -> bool:
    await cur.execute("SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s", (table, column))
    return await cur.fetchone() is not None


async def _table_exists(cur, table: str) -> bool:
    await cur.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
    return await cur.fetchone() is not None


async def _index_exists(cur, table: str, index: str) -> bool:
    await cur.execute("SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s", (table, index))
    return await cur.fetchone() is not None


async def _index_columns(cur, table: str, index: str) -> tuple[str, ...]:
    await cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME=%s AND INDEX_NAME=%s ORDER BY SEQ_IN_INDEX",
        (table, index),
    )
    return tuple(row[0] for row in await cur.fetchall())


_OFFICE_LIST_INDEXES = {
    ("office_project_grant", "idx_office_project_grant_list"): (
        "user_id", "status", "audience", "project_id", "grant_role",
    ),
    ("office_external_relation", "idx_office_external_relation_list"): (
        "user_id", "status", "project_id", "recipient_group_id",
    ),
}


async def _ensure_office_list_indexes(cur) -> None:
    """Resume only the exact user-leading list indexes measured for 3C1."""
    for (table, index), columns in _OFFICE_LIST_INDEXES.items():
        if not await _index_exists(cur, table, index):
            await cur.execute(
                "ALTER TABLE " + table + " ADD KEY " + index + " (" + ",".join(columns) + ")"
            )
        if await _index_columns(cur, table, index) != columns:
            raise RuntimeError("Office project list index is incompatible")


async def _constraint_exists(cur, name: str) -> bool:
    await cur.execute("SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME=%s", (name,))
    return await cur.fetchone() is not None


async def _check_clause(cur, name: str) -> str | None:
    await cur.execute("SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME=%s", (name,))
    row = await cur.fetchone()
    return None if row is None else str(row[0])


async def _preflight_mfa_v5_rows(cur) -> None:
    """Refuse old canonical history which the stricter v5 truth cannot retain."""
    truth = _MFA_V5_CHECK_SQL["chk_office_mfa_security_event_truth"]
    expression = truth.removeprefix("CHECK (").removesuffix(")")
    await cur.execute(f"SELECT 1 FROM office_mfa_security_event WHERE NOT ({expression}) LIMIT 1")
    if await cur.fetchone() is not None:
        raise RuntimeError("Office MFA v4 evidence violates v5 truth; historic evidence is not rewritten")


async def _upgrade_mfa_budget_index(cur) -> None:
    name = "idx_office_mfa_challenge_user"
    replacement = "idx_office_mfa_challenge_budget_v5"
    canonical_exists = await _index_exists(cur, "office_mfa_challenge", name)
    replacement_exists = await _index_exists(cur, "office_mfa_challenge", replacement)
    if canonical_exists and _is_canonical_mfa_budget_index(await _mfa_budget_index_metadata(cur, name)):
        if replacement_exists:
            raise RuntimeError("Office MFA challenge replacement budget index is unexpectedly present")
        return
    if replacement_exists and not _is_canonical_mfa_budget_index(await _mfa_budget_index_metadata(cur, replacement)):
        raise RuntimeError("Office MFA challenge replacement budget index is incompatible")
    if not canonical_exists:
        # This is the one legacy partial state produced by the old three-DDL
        # upgrade: the exact temporary v5 index exists but its canonical name
        # was never restored.  It contains no data rewrite and is safe to
        # resume only with the exact measured temporary index.
        if not replacement_exists:
            raise RuntimeError("Office MFA challenge budget index is absent; historic evidence is not rewritten")
        await cur.execute(f"ALTER TABLE office_mfa_challenge RENAME INDEX {replacement} TO {name}")
        return
    if not _is_canonical_mfa_v4_budget_index(await _mfa_budget_index_metadata(cur, name)):
        raise RuntimeError("Office MFA challenge budget index is incompatible; historic evidence is not rewritten")
    if not replacement_exists:
        await cur.execute("ALTER TABLE office_mfa_challenge ADD KEY idx_office_mfa_challenge_budget_v5 (user_id,purpose,expected_token_version,expected_mfa_version,state,expires_at)")
    # MariaDB documents comma-separated ALTER specifications, RENAME INDEX,
    # and atomic ALTER TABLE for InnoDB.  Keep the destructive name swap in
    # one ALTER; a crash leaves either old+temporary or canonical, never an
    # un-attested missing canonical index.
    await cur.execute(f"ALTER TABLE office_mfa_challenge DROP INDEX {name}, RENAME INDEX {replacement} TO {name}")


async def _upgrade_credential_shape(cur) -> None:
    await _upgrade_mfa_table_check(
        cur, table="office_mfa_credential", name="chk_office_mfa_credential_shape",
        old_digest=_MFA_CREDENTIAL_SHAPE_V4,
        new_digest=_MFA_CREDENTIAL_SHAPE_V5, old_statement=_MFA_CREDENTIAL_SHAPE_V4_SQL,
        new_statement=_MFA_CREDENTIAL_SHAPE_V5_SQL,
    )


async def _mfa_budget_index_metadata(cur, index: str) -> tuple[tuple[object, ...], ...]:
    await cur.execute(
        "SELECT SEQ_IN_INDEX,NON_UNIQUE,SUB_PART,COLLATION,INDEX_TYPE,IGNORED,COLUMN_NAME "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='office_mfa_challenge' AND INDEX_NAME=%s ORDER BY SEQ_IN_INDEX",
        (index,),
    )
    return tuple(await cur.fetchall())


def _is_canonical_mfa_budget_index(rows: tuple[tuple[object, ...], ...]) -> bool:
    return (
        len(rows) == len(_MFA_BUDGET_INDEX)
        and tuple(row[0] for row in rows) == tuple(range(1, len(_MFA_BUDGET_INDEX) + 1))
        and all(int(row[1]) == 1 and row[2] is None and row[3] == "A" and row[4] == "BTREE" and row[5] == "NO" for row in rows)
        and tuple(row[6] for row in rows) == _MFA_BUDGET_INDEX
    )


def _is_canonical_mfa_v4_budget_index(rows: tuple[tuple[object, ...], ...]) -> bool:
    return (
        len(rows) == len(_MFA_V4_BUDGET_INDEX)
        and tuple(row[0] for row in rows) == tuple(range(1, len(_MFA_V4_BUDGET_INDEX) + 1))
        and all(int(row[1]) == 1 and row[2] is None and row[3] == "A" and row[4] == "BTREE" and row[5] == "NO" for row in rows)
        and tuple(row[6] for row in rows) == _MFA_V4_BUDGET_INDEX
    )


async def _preflight_mfa_check_rows(cur, table: str, old_statement: str) -> None:
    expression = old_statement.removeprefix("CHECK (").removesuffix(")")
    await cur.execute(f"SELECT 1 FROM {table} WHERE NOT ({expression}) LIMIT 1")
    if await cur.fetchone() is not None:
        raise RuntimeError(f"Office MFA {table} rows are incompatible; historic evidence is not rewritten")


async def _upgrade_mfa_table_check(
    cur, *, table: str, name: str, old_digest: str, new_digest: str,
    old_statement: str, new_statement: str,
) -> None:
    """Upgrade a CHECK with an attested recovery path for old interrupted DDL."""
    error = (
        "Office MFA challenge state is incompatible; historic evidence is not rewritten"
        if name == "chk_office_mfa_challenge_state"
        else f"Office MFA constraint {name} is incompatible; historic evidence is not rewritten"
    )
    digest = _check_digest(await _check_clause(cur, name))
    if digest == new_digest:
        return
    if digest != old_digest:
        raise RuntimeError(error)
    await _preflight_mfa_check_rows(cur, table, old_statement)
    await cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} {new_statement}")
    if _check_digest(await _check_clause(cur, name)) != new_digest:
        raise RuntimeError(error)


async def _assert_mfa_ledger_metadata(cur) -> None:
    """Fail closed if existing MFA ledger metadata is not the measured DDL."""
    for name, expected in _CANONICAL_MFA_CHECK_DIGESTS.items():
        if not _is_canonical_mfa_check(name, await _check_clause(cur, name)):
            raise RuntimeError(f"Office MFA constraint {name} is incompatible; historic evidence is not rewritten")
    if _check_digest(await _check_clause(cur, "chk_office_mfa_credential_shape")) != _MFA_CREDENTIAL_SHAPE_V5:
        raise RuntimeError("Office MFA credential shape is incompatible")
    if _check_digest(await _check_clause(cur, "chk_office_mfa_challenge_state")) != _MFA_CHALLENGE_STATE_V5:
        raise RuntimeError("Office MFA challenge state is incompatible")
    for (table, column), expected in _MFA_CRITICAL_COLUMNS.items():
        await cur.execute(
            "SELECT DATA_TYPE,COLUMN_TYPE,IS_NULLABLE,CHARACTER_SET_NAME,COLLATION_NAME,EXTRA,COLUMN_DEFAULT "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
            (table, column),
        )
        row = await cur.fetchone()
        if row is None or tuple(row) != expected:
            raise RuntimeError(f"Office MFA column {table}.{column} is incompatible")
    for (table, index), expected_columns in _MFA_UNIQUE_INDEXES.items():
        await cur.execute(
            "SELECT NON_UNIQUE,COLUMN_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s ORDER BY SEQ_IN_INDEX",
            (table, index),
        )
        rows = await cur.fetchall()
        if not rows or any(int(row[0]) != 0 for row in rows) or tuple(row[1] for row in rows) != expected_columns:
            raise RuntimeError(f"Office MFA unique index {table}.{index} is incompatible")
    if not _is_canonical_mfa_budget_index(await _mfa_budget_index_metadata(cur, "idx_office_mfa_challenge_user")):
        raise RuntimeError("Office MFA challenge budget index is incompatible")
    await _assert_mfa_ledger_foreign_keys(cur)


async def _assert_mfa_ledger_foreign_keys(cur) -> None:
    await cur.execute("SELECT DATABASE()")
    schema_row = await cur.fetchone()
    schema = None if schema_row is None else str(schema_row[0])
    if not schema:
        raise RuntimeError("Office MFA foreign key schema is unavailable")
    for name, expected in _MFA_FOREIGN_KEYS.items():
        await cur.execute(
            "SELECT k.COLUMN_NAME,k.REFERENCED_TABLE_SCHEMA,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,"
            "r.UPDATE_RULE,r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k "
            "JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA "
            "AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.TABLE_NAME=k.TABLE_NAME "
            "WHERE k.CONSTRAINT_SCHEMA=DATABASE() AND k.TABLE_NAME='office_mfa_security_event' "
            "AND k.CONSTRAINT_NAME=%s ORDER BY k.ORDINAL_POSITION",
            (name,),
        )
        rows = await cur.fetchall()
        if not rows:
            raise RuntimeError(f"Office MFA foreign key {name} is incompatible")
        local = tuple(row[0] for row in rows)
        referenced_schema = rows[0][1]
        referenced_table = rows[0][2]
        referenced = tuple(row[3] for row in rows)
        rules = (rows[0][4], rows[0][5])
        if referenced_schema != schema or (local, referenced_table, referenced, *rules) != expected:
            raise RuntimeError(f"Office MFA foreign key {name} is incompatible")


async def assert_mfa_ledger_runtime_metadata(cur, *, allow_redacted_trigger_body: bool = False) -> None:
    await _assert_mfa_ledger_metadata(cur)
    await cur.execute(
        "SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT "
        "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() "
        "AND TRIGGER_NAME IN ('no_update_office_mfa_security_event','no_delete_office_mfa_security_event')"
    )
    found = {row[0]: tuple(row[1:]) for row in await cur.fetchall()}
    if allow_redacted_trigger_body:
        found = {
            name: (*metadata[:-1], None if metadata[-1] is None else metadata[-1])
            for name, metadata in found.items()
        }
        expected = {
            name: (*metadata[:-1], None if found.get(name, (None,))[-1] is None else metadata[-1])
            for name, metadata in _MFA_TRIGGER_METADATA.items()
        }
    else:
        expected = _MFA_TRIGGER_METADATA
    if found != expected:
        raise RuntimeError("Office MFA triggers are missing or invalid")


async def assert_bootstrap_runtime_metadata(cur, *, allow_redacted_trigger_body: bool = False) -> None:
    """Attest bootstrap's signed/unsigned foreign keys and append-only guard."""
    await cur.execute(
        "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_bootstrap_state' ORDER BY ORDINAL_POSITION"
    )
    if tuple(await cur.fetchall()) != (
        ("ceremony_key", "varchar(64)", "NO"), ("target_user_id", "int(11)", "NO"),
        ("identity_actor_user_id", "int(11)", "NO"),
        ("identity_proof_event_id", "bigint(20) unsigned", "NO"),
        ("credential_audit_id", "bigint(20)", "NO"), ("basis_ref", "varchar(128)", "NO"),
        ("completed_at", "datetime(6)", "NO"),
    ):
        raise RuntimeError("Office bootstrap state column metadata is invalid")
    await cur.execute(
        "SELECT COLUMN_NAME,NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_bootstrap_state' "
        "AND INDEX_NAME='PRIMARY' ORDER BY SEQ_IN_INDEX"
    )
    if tuple(await cur.fetchall()) != (("ceremony_key", 0),):
        raise RuntimeError("Office bootstrap state primary key is invalid")
    await cur.execute("SELECT DATABASE()")
    schema_row = await cur.fetchone()
    schema = None if schema_row is None else str(schema_row[0])
    if not schema:
        raise RuntimeError("Office bootstrap state schema is unavailable")
    for name, expected in _BOOTSTRAP_FOREIGN_KEYS.items():
        await cur.execute(
            "SELECT k.COLUMN_NAME,k.REFERENCED_TABLE_SCHEMA,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,"
            "r.UPDATE_RULE,r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k "
            "JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA "
            "AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.TABLE_NAME=k.TABLE_NAME "
            "WHERE k.CONSTRAINT_SCHEMA=DATABASE() AND k.TABLE_NAME='office_bootstrap_state' "
            "AND k.CONSTRAINT_NAME=%s ORDER BY k.ORDINAL_POSITION",
            (name,),
        )
        rows = await cur.fetchall()
        if not rows or rows[0][1] != schema or (
            tuple(row[0] for row in rows), rows[0][2], tuple(row[3] for row in rows),
            rows[0][4], rows[0][5],
        ) != expected:
            raise RuntimeError("Office bootstrap state foreign keys are invalid")
    await cur.execute(
        "SELECT CONSTRAINT_NAME,CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME IN "
        "('chk_office_bootstrap_identity','chk_office_bootstrap_basis')"
    )
    checks = {str(name): _normalize_mariadb_check(str(clause)) for name, clause in await cur.fetchall()}
    if checks != {name: _normalize_mariadb_check(clause) for name, clause in _BOOTSTRAP_CHECKS.items()}:
        raise RuntimeError("Office bootstrap state checks are invalid")
    await cur.execute(
        "SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT "
        "FROM information_schema.TRIGGERS "
        "WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME IN "
        "('no_update_office_bootstrap_state','no_delete_office_bootstrap_state')"
    )
    found = {row[0]: tuple(row[1:]) for row in await cur.fetchall()}
    if allow_redacted_trigger_body:
        found = {
            name: (*metadata[:-1], None if metadata[-1] is None else metadata[-1])
            for name, metadata in found.items()
        }
        expected = {
            name: (*metadata[:-1], None if found.get(name, (None,))[-1] is None else metadata[-1])
            for name, metadata in _BOOTSTRAP_TRIGGER_METADATA.items()
        }
    else:
        expected = _BOOTSTRAP_TRIGGER_METADATA
    if found != expected:
        raise RuntimeError("Office bootstrap state triggers are invalid")


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
    if not await _column_exists(cursor, "jax_users", "mfa_version"):
        await cursor.execute("ALTER TABLE jax_users ADD COLUMN mfa_version BIGINT NOT NULL DEFAULT 0, ADD CONSTRAINT chk_jax_users_mfa_version CHECK (mfa_version >= 0)")
    for statement in OFFICE_DDL:
        await cursor.execute(statement)
    await _ensure_office_list_indexes(cursor)
    ledger_existed = await _table_exists(cursor, "office_mfa_security_event")
    for statement in OFFICE_MFA_DDL:
        if "office_mfa_security_event" not in statement:
            await cursor.execute(statement)
    if await _table_exists(cursor, "office_mfa_security_event"):
        existing_ledger = {
            name: _check_digest(await _check_clause(cursor, name))
            for name in _MFA_V5_CHECK_SQL
        }
        if existing_ledger == _CANONICAL_V4_MFA_CHECK_DIGESTS:
            await _preflight_mfa_v5_rows(cursor)
    legacy_challenge_surrogate = not await _column_exists(cursor, "office_mfa_challenge", "challenge_id")
    if legacy_challenge_surrogate:
        await cursor.execute("ALTER TABLE office_mfa_challenge ADD COLUMN challenge_id BIGINT UNSIGNED NULL AUTO_INCREMENT UNIQUE AFTER token_hash")
    if not await _index_exists(cursor, "office_mfa_challenge", "uq_office_mfa_challenge_subject"):
        await cursor.execute("ALTER TABLE office_mfa_challenge ADD UNIQUE KEY uq_office_mfa_challenge_subject (challenge_id,user_id)")
    await _upgrade_mfa_table_check(
        cursor, table="office_mfa_challenge", name="chk_office_mfa_challenge_state",
        old_digest=_MFA_CHALLENGE_STATE_V4,
        new_digest=_MFA_CHALLENGE_STATE_V5, old_statement=_MFA_CHALLENGE_STATE_V4_SQL,
        new_statement=_MFA_CHALLENGE_STATE_V5_SQL,
    )
    await _upgrade_mfa_budget_index(cursor)
    proof_index = "idx_office_mfa_security_event_proof"
    proof_columns = ("subject_user_id", "purpose", "token_version", "mfa_version", "occurred_at", "event_id")
    if ledger_existed and await _index_exists(cursor, "office_mfa_security_event", proof_index):
        if await _index_columns(cursor, "office_mfa_security_event", proof_index) != proof_columns:
            await cursor.execute(f"ALTER TABLE office_mfa_security_event DROP INDEX {proof_index}")
            await cursor.execute(
                "ALTER TABLE office_mfa_security_event ADD KEY "
                "idx_office_mfa_security_event_proof (subject_user_id,purpose,token_version,mfa_version,occurred_at,event_id)"
            )
    if not ledger_existed:
        ledger_statement = next(statement for statement in OFFICE_MFA_DDL if statement.startswith("CREATE TABLE IF NOT EXISTS office_mfa_security_event"))
        prefix, suffix = ledger_statement.split(" CONSTRAINT chk_office_mfa_security_event_shape ", 1)
        _, engine = suffix.rsplit(" ENGINE=", 1)
        await cursor.execute(
            prefix
            + " CONSTRAINT chk_office_mfa_security_event_fence " + _MFA_V5_CHECK_SQL["chk_office_mfa_security_event_fence"]
            + ", CONSTRAINT chk_office_mfa_security_event_shape " + _MFA_V5_CHECK_SQL["chk_office_mfa_security_event_shape"]
            + ", CONSTRAINT chk_office_mfa_security_event_truth " + _MFA_V5_CHECK_SQL["chk_office_mfa_security_event_truth"]
            + ") ENGINE=" + engine
        )
    for statement in OFFICE_MFA_DDL:
        if statement.startswith("CREATE TRIGGER"):
            await cursor.execute(statement)
    for statement in OFFICE_BOOTSTRAP_DDL:
        await cursor.execute(statement)
    await assert_bootstrap_runtime_metadata(cursor)
    await _upgrade_credential_shape(cursor)
    ledger = {name: _check_digest(await _check_clause(cursor, name)) for name in _MFA_V5_CHECK_SQL}
    if ledger == _CANONICAL_V4_MFA_CHECK_DIGESTS:
        await _preflight_mfa_v5_rows(cursor)
        await cursor.execute(
            "ALTER TABLE office_mfa_security_event "
            "DROP CONSTRAINT chk_office_mfa_security_event_shape, "
            "DROP CONSTRAINT chk_office_mfa_security_event_truth, "
            "ADD CONSTRAINT chk_office_mfa_security_event_shape " + _MFA_V5_CHECK_SQL["chk_office_mfa_security_event_shape"] + ", "
            "ADD CONSTRAINT chk_office_mfa_security_event_truth " + _MFA_V5_CHECK_SQL["chk_office_mfa_security_event_truth"]
        )
    elif ledger != _CANONICAL_MFA_CHECK_DIGESTS:
        raise RuntimeError("Office MFA ledger CHECK history is incomplete; historic evidence is not rewritten")
    if legacy_challenge_surrogate and not await _constraint_exists(cursor, "fk_office_mfa_security_event_challenge_subject"):
        await cursor.execute(
            "ALTER TABLE office_mfa_security_event ADD CONSTRAINT "
            "fk_office_mfa_security_event_challenge_subject FOREIGN KEY (challenge_id,subject_user_id) "
            "REFERENCES office_mfa_challenge(challenge_id,user_id)"
        )
    await _assert_mfa_ledger_metadata(cursor)
    await assert_mfa_ledger_runtime_metadata(cursor)
    await cursor.execute("SELECT event_id FROM office_mfa_security_event WHERE fence_key='office-mfa-fence-v1'")
    if not await cursor.fetchone():
        await cursor.execute("INSERT INTO office_mfa_security_event (event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) VALUES ('SYSTEM_FENCE',NULL,'SYSTEM_MIGRATOR',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'RECORDED',NULL,'office-mfa-fence-v1',UTC_TIMESTAMP(6))")
    # The rejected development journal never authorizes a schema state.  It is
    # removed only after all measured physical contracts and runtime probes
    # have passed; missing or mixed constraints return before this point.
    await cursor.execute("DROP TABLE IF EXISTS office_mfa_schema_transition")
    if await _table_exists(cursor, "office_mfa_schema_transition"):
        raise RuntimeError("Office MFA schema transition table was not removed")
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
