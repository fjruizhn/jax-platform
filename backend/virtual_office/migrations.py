from __future__ import annotations

import hashlib
import re
from db.runtime_privileges import MANIFEST_VERSION, runtime_table_privilege_digest

OFFICE_DDL = (
    "CREATE TABLE IF NOT EXISTS office_account_profile (user_id INT NOT NULL PRIMARY KEY, global_role VARCHAR(24) NOT NULL DEFAULT 'COLLABORATOR', status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', version BIGINT NOT NULL DEFAULT 1, bound_by_user_id INT NULL, credential_audit_id BIGINT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6), CONSTRAINT fk_office_account_profile_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_account_profile_bound_by FOREIGN KEY (bound_by_user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_account_profile_credential_audit FOREIGN KEY (credential_audit_id) REFERENCES user_admin_audit(id), CONSTRAINT chk_office_account_profile_role CHECK (global_role IN ('COLLABORATOR','GENERAL_ADMIN','OFFICE_SUPERUSER')), CONSTRAINT chk_office_account_profile_status CHECK (status IN ('ACTIVE','REVOKED')), CONSTRAINT chk_office_account_profile_binding CHECK ((bound_by_user_id IS NULL AND credential_audit_id IS NULL) OR (bound_by_user_id IS NOT NULL AND credential_audit_id IS NOT NULL))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_admin_delegation (delegation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, tenant_id INT NOT NULL, user_id INT NOT NULL, capability VARCHAR(40) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', granted_by INT NOT NULL, version BIGINT NOT NULL DEFAULT 1, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_admin_delegation (tenant_id,user_id,capability), KEY idx_office_admin_delegation_scope (tenant_id,user_id,capability,status), KEY idx_office_admin_delegation_user (user_id), CONSTRAINT fk_office_admin_delegation_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_admin_delegation_tenant FOREIGN KEY (tenant_id) REFERENCES jax_tenants(tenant_id), CONSTRAINT fk_office_admin_delegation_granted_by FOREIGN KEY (granted_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_admin_delegation_capability CHECK (capability IN ('ADMINISTER_OFFICE')), CONSTRAINT chk_office_admin_delegation_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_project_grant (grant_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, user_id INT NOT NULL, grant_role VARCHAR(24) NOT NULL, audience VARCHAR(24) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', version BIGINT NOT NULL DEFAULT 1, granted_by INT NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_project_grant_subject (project_id,user_id), KEY idx_office_project_grant_scope (project_id,user_id,status,audience), KEY idx_office_project_grant_list (user_id,status,audience,project_id,grant_role), CONSTRAINT fk_office_project_grant_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_project_grant_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_project_grant_granted_by FOREIGN KEY (granted_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_project_grant_role CHECK (grant_role IN ('PROJECT_ADMIN','COLLABORATOR')), CONSTRAINT chk_office_project_grant_audience CHECK (audience IN ('INTERNAL','CLIENT_PUBLISHED')), CONSTRAINT chk_office_project_grant_status CHECK (status IN ('ACTIVE','REVOKED')), CONSTRAINT chk_office_project_grant_published_role CHECK (audience='INTERNAL' OR grant_role='COLLABORATOR')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_recipient_group (recipient_group_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, tenant_id INT NOT NULL, group_kind VARCHAR(16) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', created_by INT NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_recipient_group_project_kind (project_id,group_kind), UNIQUE KEY uq_office_recipient_group_id_project (recipient_group_id,project_id), KEY idx_office_recipient_group_scope (project_id,status), CONSTRAINT fk_office_recipient_group_scope FOREIGN KEY (project_id,tenant_id) REFERENCES jax_project_scope(project_id,tenant_id), CONSTRAINT fk_office_recipient_group_created_by FOREIGN KEY (created_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_recipient_group_kind CHECK (group_kind='CLIENT'), CONSTRAINT chk_office_recipient_group_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_external_relation (relation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, user_id INT NOT NULL, recipient_group_id BIGINT UNSIGNED NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', version BIGINT NOT NULL DEFAULT 1, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_external_relation_subject (project_id,user_id), UNIQUE KEY uq_office_external_relation_receipt (relation_id,project_id,user_id,recipient_group_id), KEY idx_office_external_relation_scope (project_id,user_id,status,recipient_group_id), KEY idx_office_external_relation_list (user_id,status,project_id,recipient_group_id), CONSTRAINT fk_office_external_relation_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_external_relation_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_external_relation_group FOREIGN KEY (recipient_group_id) REFERENCES office_recipient_group(recipient_group_id), CONSTRAINT fk_office_external_relation_group_project FOREIGN KEY (recipient_group_id,project_id) REFERENCES office_recipient_group(recipient_group_id,project_id), CONSTRAINT chk_office_external_relation_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_professional_grant (professional_grant_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, user_id INT NOT NULL, capability VARCHAR(64) NOT NULL, basis VARCHAR(128) NOT NULL, evidence_ref VARCHAR(255) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', granted_by INT NOT NULL, version BIGINT NOT NULL DEFAULT 1, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), UNIQUE KEY uq_office_professional_grant (project_id,user_id,capability), KEY idx_office_professional_grant_scope (project_id,user_id,capability,status), CONSTRAINT fk_office_professional_grant_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_professional_grant_user FOREIGN KEY (user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_professional_grant_granted_by FOREIGN KEY (granted_by) REFERENCES jax_users(user_id), CONSTRAINT chk_office_professional_grant_status CHECK (status IN ('ACTIVE','REVOKED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_authorization_event (event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, actor_user_id INT NOT NULL, operation VARCHAR(40) NOT NULL, requested_project_id INT NULL, project_id INT NULL, tenant_id INT NULL, result VARCHAR(16) NOT NULL, reason_code VARCHAR(64) NOT NULL, correlation_id VARCHAR(128) NULL, occurred_at DATETIME(6) NOT NULL, KEY idx_office_authorization_event_scope (project_id,tenant_id,occurred_at), KEY idx_office_authorization_event_actor (actor_user_id,occurred_at), CONSTRAINT fk_office_authorization_event_actor FOREIGN KEY (actor_user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_authorization_event_project FOREIGN KEY (project_id) REFERENCES projects(id), CONSTRAINT fk_office_authorization_event_tenant FOREIGN KEY (tenant_id) REFERENCES jax_tenants(tenant_id), CONSTRAINT fk_office_authorization_event_scope FOREIGN KEY (project_id,tenant_id) REFERENCES jax_project_scope(project_id,tenant_id), CONSTRAINT chk_office_authorization_event_result CHECK (result IN ('ALLOWED','DENIED','RECORDED'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_authorization_event BEFORE UPDATE ON office_authorization_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_authorization_event append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_authorization_event BEFORE DELETE ON office_authorization_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_authorization_event append-only'",
    "CREATE TABLE IF NOT EXISTS office_schema_revision (component VARCHAR(64) NOT NULL PRIMARY KEY, revision VARCHAR(64) NOT NULL, applied_at DATETIME(6) NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
)

RUNTIME_COMPONENT = "virtual-office"
# The readiness stamp is deliberately coupled to the normalized table ACL.
# A changed privilege body must force the migrator to restamp before a web
# process can consider the schema/runtime pair ready.
# v12 binds the per-table write contract after v11 introduced the pending
# publication staging.  Its digest forces a migrator restamp.
RUNTIME_REVISION = f"office-v13c-{MANIFEST_VERSION}-{runtime_table_privilege_digest()[:16]}"

# The Office catalogue is deliberately a binding to the central provider/model
# catalogue.  A browser never supplies a URL, credential or model id.  The
# fingerprint makes a changed provider, model or credential identity unusable
# until an administrator explicitly re-attests and enables the engine.
OFFICE_ENGINE_DDL = (
    "CREATE TABLE IF NOT EXISTS office_engine ("
    "engine_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,"
    "display_name_key VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,model_ref INT NOT NULL,credential_id INT NOT NULL,"
    "transport ENUM('http_openai_compat') NOT NULL,allowed_data_classifications JSON NOT NULL,"
    "binding_fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,enabled BOOLEAN NOT NULL DEFAULT FALSE,"
    "activation_epoch BIGINT UNSIGNED NOT NULL DEFAULT 0,version BIGINT NOT NULL DEFAULT 1,"
    "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),"
    "KEY idx_office_engine_enabled_key (enabled,engine_key),"
    "CONSTRAINT fk_office_engine_model FOREIGN KEY (model_ref) REFERENCES model(id),CONSTRAINT fk_office_engine_credential FOREIGN KEY (credential_id) REFERENCES credential(id),"
    "CONSTRAINT chk_office_engine_key CHECK (engine_key<>''),CONSTRAINT chk_office_engine_display_name_key CHECK (display_name_key<>''),"
    "CONSTRAINT chk_office_engine_classifications CHECK (JSON_TYPE(allowed_data_classifications)='ARRAY' AND JSON_LENGTH(allowed_data_classifications)>0),"
    "CONSTRAINT chk_office_engine_fingerprint CHECK (binding_fingerprint REGEXP '^[0-9a-f]{64}$'),"
    "CONSTRAINT chk_office_engine_epoch CHECK (activation_epoch>=0),CONSTRAINT chk_office_engine_version CHECK (version>=1)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
)

OFFICE_AGENT_RUNTIME_DDL = (
    "CREATE TABLE IF NOT EXISTS office_agent_runtime_config ("
    "config_id TINYINT UNSIGNED NOT NULL PRIMARY KEY,config_version SMALLINT UNSIGNED NOT NULL DEFAULT 1,"
    "max_output_tokens SMALLINT UNSIGNED NOT NULL DEFAULT 2048,max_input_bytes INT UNSIGNED NOT NULL DEFAULT 32768,"
    "max_instructions_bytes INT UNSIGNED NOT NULL DEFAULT 32768,max_response_bytes INT UNSIGNED NOT NULL DEFAULT 1048576,"
    "window_seconds INT UNSIGNED NOT NULL DEFAULT 3600,actor_runs_per_window SMALLINT UNSIGNED NOT NULL DEFAULT 30,"
    "project_runs_per_window SMALLINT UNSIGNED NOT NULL DEFAULT 300,actor_concurrency TINYINT UNSIGNED NOT NULL DEFAULT 2,"
    "project_concurrency TINYINT UNSIGNED NOT NULL DEFAULT 8,timeout_seconds TINYINT UNSIGNED NOT NULL DEFAULT 45,"
    "stale_seconds TINYINT UNSIGNED NOT NULL DEFAULT 60,updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),"
    "CONSTRAINT chk_office_agent_runtime_config_singleton CHECK (config_id=1),CONSTRAINT chk_office_agent_runtime_config_version CHECK (config_version>=1),"
    "CONSTRAINT chk_office_agent_runtime_config_bounds CHECK (max_output_tokens BETWEEN 1 AND 2048 AND max_input_bytes BETWEEN 1 AND 32768 AND max_instructions_bytes BETWEEN 1 AND 32768 AND max_response_bytes BETWEEN 1 AND 1048576 AND window_seconds BETWEEN 60 AND 3600 AND actor_runs_per_window BETWEEN 1 AND 30 AND project_runs_per_window BETWEEN 1 AND 300 AND actor_concurrency BETWEEN 1 AND 2 AND project_concurrency BETWEEN 1 AND 8 AND timeout_seconds BETWEEN 1 AND 45 AND stale_seconds BETWEEN 6 AND 60 AND stale_seconds>=timeout_seconds+5)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "INSERT IGNORE INTO office_agent_runtime_config (config_id,config_version) VALUES (1,1)",
    "CREATE TRIGGER IF NOT EXISTS guard_office_agent_runtime_config_update BEFORE UPDATE ON office_agent_runtime_config FOR EACH ROW BEGIN IF NEW.config_id<>1 OR NEW.config_version<>OLD.config_version+1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office agent runtime config version must advance exactly once'; END IF; END",
)

OFFICE_AGENT_DDL = (
    "CREATE TABLE IF NOT EXISTS office_agent ("
    "agent_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,project_id INT NOT NULL,agent_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "display_name VARCHAR(128) NOT NULL,instructions TEXT NOT NULL,data_classification VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "status ENUM('ACTIVE','DISABLED') NOT NULL DEFAULT 'ACTIVE',version BIGINT UNSIGNED NOT NULL DEFAULT 1,created_by INT NOT NULL,"
    "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_agent_project_key (project_id,agent_key),KEY idx_office_agent_project (project_id,status,agent_id),KEY idx_office_agent_project_list (project_id,agent_id),"
    "CONSTRAINT fk_office_agent_project FOREIGN KEY (project_id) REFERENCES projects(id),CONSTRAINT fk_office_agent_created_by FOREIGN KEY (created_by) REFERENCES jax_users(user_id),"
    "CONSTRAINT chk_office_agent_key CHECK (agent_key<>''),CONSTRAINT chk_office_agent_name CHECK (display_name<>''),CONSTRAINT chk_office_agent_classification CHECK (data_classification REGEXP '^[A-Z][A-Z0-9_]{0,63}$'),CONSTRAINT chk_office_agent_version CHECK (version>=1)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_agent_binding ("
    "binding_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,agent_id BIGINT UNSIGNED NOT NULL,engine_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "binding_version BIGINT UNSIGNED NOT NULL,active BOOLEAN NOT NULL DEFAULT TRUE,active_agent_id BIGINT UNSIGNED GENERATED ALWAYS AS (IF(active,agent_id,NULL)) STORED,bound_by INT NOT NULL,bound_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_agent_binding_version (agent_id,binding_version),UNIQUE KEY uq_office_agent_binding_active_agent (active_agent_id),KEY idx_office_agent_binding_active (agent_id,active,binding_version),"
    "CONSTRAINT fk_office_agent_binding_agent FOREIGN KEY (agent_id) REFERENCES office_agent(agent_id),CONSTRAINT fk_office_agent_binding_engine FOREIGN KEY (engine_key) REFERENCES office_engine(engine_key),CONSTRAINT fk_office_agent_binding_by FOREIGN KEY (bound_by) REFERENCES jax_users(user_id),"
    "CONSTRAINT chk_office_agent_binding_version CHECK (binding_version>=1)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_agent_run ("
    "run_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,agent_id BIGINT UNSIGNED NOT NULL,project_id INT NOT NULL,requested_by INT NOT NULL,authorization_event_id BIGINT UNSIGNED NOT NULL,agent_version BIGINT UNSIGNED NOT NULL,binding_id BIGINT UNSIGNED NOT NULL,binding_version BIGINT UNSIGNED NOT NULL,"
    "idempotency_key VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,input_sha256 BINARY(32) NOT NULL,instructions_sha256 BINARY(32) NOT NULL,dispatch_snapshot_sha256 BINARY(32) NOT NULL,config_version SMALLINT UNSIGNED NOT NULL,"
    "engine_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,engine_version BIGINT NOT NULL,engine_binding_fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,model_ref INT NOT NULL,model_id VARCHAR(255) NOT NULL,"
    "credential_id INT NOT NULL,credential_fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,provider_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "auth_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,auth_transport VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "status ENUM('DISPATCHING','SUCCEEDED','UNKNOWN') NOT NULL,output_text MEDIUMTEXT NULL,accepted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),deadline_at DATETIME(6) NOT NULL,fence BIGINT UNSIGNED NOT NULL,completed_at DATETIME(6) NULL,"
    "UNIQUE KEY uq_office_agent_run_idempotency (requested_by,agent_id,idempotency_key),KEY idx_office_agent_run_actor_window (requested_by,accepted_at,status,deadline_at),KEY idx_office_agent_run_project_window (project_id,accepted_at,status,deadline_at),KEY idx_office_agent_run_actor_concurrency (requested_by,status,deadline_at),KEY idx_office_agent_run_project_concurrency (project_id,status,deadline_at),"
    "CONSTRAINT fk_office_agent_run_agent FOREIGN KEY (agent_id) REFERENCES office_agent(agent_id),CONSTRAINT fk_office_agent_run_project FOREIGN KEY (project_id) REFERENCES projects(id),CONSTRAINT fk_office_agent_run_requested_by FOREIGN KEY (requested_by) REFERENCES jax_users(user_id),CONSTRAINT fk_office_agent_run_authorization FOREIGN KEY (authorization_event_id) REFERENCES office_authorization_event(event_id),CONSTRAINT fk_office_agent_run_binding FOREIGN KEY (binding_id) REFERENCES office_agent_binding(binding_id),CONSTRAINT fk_office_agent_run_engine FOREIGN KEY (engine_key) REFERENCES office_engine(engine_key),CONSTRAINT fk_office_agent_run_model FOREIGN KEY (model_ref) REFERENCES model(id),CONSTRAINT fk_office_agent_run_credential FOREIGN KEY (credential_id) REFERENCES credential(id),"
    "CONSTRAINT chk_office_agent_run_key CHECK (idempotency_key<>''),CONSTRAINT chk_office_agent_run_snapshot CHECK (agent_version>=1 AND binding_version>=1 AND config_version>=1 AND engine_version>=1 AND engine_binding_fingerprint REGEXP '^[0-9a-f]{64}$' AND credential_fingerprint REGEXP '^[0-9a-f]{64}$' AND provider_id<>'' AND auth_type<>'' AND auth_transport<>'' AND fence>=1 AND deadline_at>=accepted_at),CONSTRAINT chk_office_agent_run_output CHECK ((status='SUCCEEDED' AND output_text IS NOT NULL AND completed_at IS NOT NULL) OR (status='DISPATCHING' AND output_text IS NULL AND completed_at IS NULL) OR (status='UNKNOWN' AND output_text IS NULL AND completed_at IS NOT NULL))"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS no_mutate_office_agent_run_snapshot BEFORE UPDATE ON office_agent_run FOR EACH ROW BEGIN IF OLD.status IN ('SUCCEEDED','UNKNOWN') OR NEW.run_id<>OLD.run_id OR NEW.agent_id<>OLD.agent_id OR NEW.project_id<>OLD.project_id OR NEW.requested_by<>OLD.requested_by OR NEW.authorization_event_id<>OLD.authorization_event_id OR NEW.agent_version<>OLD.agent_version OR NEW.binding_id<>OLD.binding_id OR NEW.binding_version<>OLD.binding_version OR NEW.idempotency_key<>OLD.idempotency_key OR NEW.input_sha256<>OLD.input_sha256 OR NEW.instructions_sha256<>OLD.instructions_sha256 OR NEW.dispatch_snapshot_sha256<>OLD.dispatch_snapshot_sha256 OR NEW.config_version<>OLD.config_version OR NEW.engine_key<>OLD.engine_key OR NEW.engine_version<>OLD.engine_version OR NEW.engine_binding_fingerprint<>OLD.engine_binding_fingerprint OR NEW.model_ref<>OLD.model_ref OR NEW.model_id<>OLD.model_id OR NEW.credential_id<>OLD.credential_id OR NEW.credential_fingerprint<>OLD.credential_fingerprint OR NEW.provider_id<>OLD.provider_id OR NEW.auth_type<>OLD.auth_type OR NEW.auth_transport<>OLD.auth_transport OR NEW.accepted_at<>OLD.accepted_at OR NEW.deadline_at<>OLD.deadline_at OR NEW.fence<>OLD.fence THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_agent_run snapshot or terminal row is immutable'; END IF; END",
)

# Global module governance is deliberately distinct from client artefacts.
# This first schema cut records only pending-contract authority: the canonical
# critic receipt and signer do not exist yet, therefore no effective release
# or publication can be represented.
OFFICE_MODULE_PUBLICATION_STAGING_DDL = (
    "CREATE TABLE IF NOT EXISTS office_module_creative_review_event ("
    "creative_review_event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,candidate_revision_id BIGINT UNSIGNED NOT NULL,candidate_id BIGINT UNSIGNED NOT NULL,manifest_sha256 BINARY(32) NOT NULL,artifact_set_digest BINARY(32) NOT NULL,human_user_id INT NOT NULL,mfa_proof_event_id BIGINT UNSIGNED NOT NULL,decision VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_creative_review_identity (creative_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest),KEY idx_office_module_creative_review_candidate (candidate_id,candidate_revision_id,occurred_at),"
    "CONSTRAINT fk_office_module_creative_review_revision FOREIGN KEY (candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest) REFERENCES office_module_candidate_revision(candidate_revision_id,candidate_id,manifest_sha256,claimed_artifact_set_digest),CONSTRAINT fk_office_module_creative_review_human FOREIGN KEY (human_user_id) REFERENCES jax_users(user_id),CONSTRAINT fk_office_module_creative_review_mfa FOREIGN KEY (mfa_proof_event_id) REFERENCES office_mfa_security_event(event_id),CONSTRAINT chk_office_module_creative_review_decision CHECK (decision='PENDING_CONTRACT')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_creative_review_head ("
    "candidate_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,head_version BIGINT UNSIGNED NOT NULL,creative_review_event_id BIGINT UNSIGNED NOT NULL,candidate_revision_id BIGINT UNSIGNED NOT NULL,manifest_sha256 BINARY(32) NOT NULL,artifact_set_digest BINARY(32) NOT NULL,"
    "UNIQUE KEY uq_office_module_creative_head_event (creative_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest),CONSTRAINT fk_office_module_creative_head_candidate FOREIGN KEY (candidate_id) REFERENCES office_module_candidate(candidate_id),CONSTRAINT fk_office_module_creative_head_event FOREIGN KEY (creative_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest) REFERENCES office_module_creative_review_event(creative_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest),CONSTRAINT chk_office_module_creative_head_version CHECK (head_version>=1)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_rights_review_event ("
    "rights_review_event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,candidate_revision_id BIGINT UNSIGNED NOT NULL,candidate_id BIGINT UNSIGNED NOT NULL,manifest_sha256 BINARY(32) NOT NULL,artifact_set_digest BINARY(32) NOT NULL,critic_invocation_ref VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,verification_state VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,decision VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_rights_review_identity (rights_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest),KEY idx_office_module_rights_review_candidate (candidate_id,candidate_revision_id,occurred_at),CONSTRAINT fk_office_module_rights_review_revision FOREIGN KEY (candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest) REFERENCES office_module_candidate_revision(candidate_revision_id,candidate_id,manifest_sha256,claimed_artifact_set_digest),CONSTRAINT chk_office_module_rights_review_staging CHECK (critic_invocation_ref<>'' AND verification_state='PENDING_CONTRACT' AND decision='PENDING_CONTRACT')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_rights_review_head ("
    "candidate_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,head_version BIGINT UNSIGNED NOT NULL,rights_review_event_id BIGINT UNSIGNED NOT NULL,candidate_revision_id BIGINT UNSIGNED NOT NULL,manifest_sha256 BINARY(32) NOT NULL,artifact_set_digest BINARY(32) NOT NULL,"
    "UNIQUE KEY uq_office_module_rights_head_event (rights_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest),CONSTRAINT fk_office_module_rights_head_candidate FOREIGN KEY (candidate_id) REFERENCES office_module_candidate(candidate_id),CONSTRAINT fk_office_module_rights_head_event FOREIGN KEY (rights_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest) REFERENCES office_module_rights_review_event(rights_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest),CONSTRAINT chk_office_module_rights_head_version CHECK (head_version>=1)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_release_event ("
    "release_event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,candidate_revision_id BIGINT UNSIGNED NOT NULL,candidate_id BIGINT UNSIGNED NOT NULL,manifest_sha256 BINARY(32) NOT NULL,artifact_set_digest BINARY(32) NOT NULL,creative_review_event_id BIGINT UNSIGNED NOT NULL,rights_review_event_id BIGINT UNSIGNED NOT NULL,decision_state VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,canonical_receipt_sha256 BINARY(32) NULL,canonical_receipt_signature MEDIUMBLOB NULL,canonical_receipt_key_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NULL,occurred_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
    "UNIQUE KEY uq_office_module_release_event_identity (release_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest),KEY idx_office_module_release_event_candidate (candidate_id,candidate_revision_id,occurred_at),CONSTRAINT fk_office_module_release_event_revision FOREIGN KEY (candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest) REFERENCES office_module_candidate_revision(candidate_revision_id,candidate_id,manifest_sha256,claimed_artifact_set_digest),CONSTRAINT fk_office_module_release_event_creative FOREIGN KEY (creative_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest) REFERENCES office_module_creative_review_event(creative_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest),CONSTRAINT fk_office_module_release_event_rights FOREIGN KEY (rights_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest) REFERENCES office_module_rights_review_event(rights_review_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest),CONSTRAINT chk_office_module_release_event_staging CHECK (decision_state='PENDING_CONTRACT' AND canonical_receipt_sha256 IS NULL AND canonical_receipt_signature IS NULL AND canonical_receipt_key_id IS NULL)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_module_release ("
    "candidate_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,head_version BIGINT UNSIGNED NOT NULL,release_event_id BIGINT UNSIGNED NOT NULL,candidate_revision_id BIGINT UNSIGNED NOT NULL,manifest_sha256 BINARY(32) NOT NULL,artifact_set_digest BINARY(32) NOT NULL,status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "UNIQUE KEY uq_office_module_release_event (release_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest),CONSTRAINT fk_office_module_release_candidate FOREIGN KEY (candidate_id) REFERENCES office_module_candidate(candidate_id),CONSTRAINT fk_office_module_release_event FOREIGN KEY (release_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest) REFERENCES office_module_release_event(release_event_id,candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest),CONSTRAINT chk_office_module_release_head CHECK (head_version>=1 AND status='PENDING_CONTRACT')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
)
_MODULE_PUBLICATION_STAGING_EVENT_TABLES = ("office_module_creative_review_event", "office_module_rights_review_event", "office_module_release_event")
_MODULE_PUBLICATION_STAGING_COLUMNS = {
    "office_module_creative_review_event": (("creative_review_event_id", "bigint(20) unsigned", "NO"), ("candidate_revision_id", "bigint(20) unsigned", "NO"), ("candidate_id", "bigint(20) unsigned", "NO"), ("manifest_sha256", "binary(32)", "NO"), ("artifact_set_digest", "binary(32)", "NO"), ("human_user_id", "int(11)", "NO"), ("mfa_proof_event_id", "bigint(20) unsigned", "NO"), ("decision", "varchar(16)", "NO"), ("occurred_at", "datetime(6)", "NO")),
    "office_module_creative_review_head": (("candidate_id", "bigint(20) unsigned", "NO"), ("head_version", "bigint(20) unsigned", "NO"), ("creative_review_event_id", "bigint(20) unsigned", "NO"), ("candidate_revision_id", "bigint(20) unsigned", "NO"), ("manifest_sha256", "binary(32)", "NO"), ("artifact_set_digest", "binary(32)", "NO")),
    "office_module_rights_review_event": (("rights_review_event_id", "bigint(20) unsigned", "NO"), ("candidate_revision_id", "bigint(20) unsigned", "NO"), ("candidate_id", "bigint(20) unsigned", "NO"), ("manifest_sha256", "binary(32)", "NO"), ("artifact_set_digest", "binary(32)", "NO"), ("critic_invocation_ref", "varchar(128)", "NO"), ("verification_state", "varchar(24)", "NO"), ("decision", "varchar(24)", "NO"), ("occurred_at", "datetime(6)", "NO")),
    "office_module_rights_review_head": (("candidate_id", "bigint(20) unsigned", "NO"), ("head_version", "bigint(20) unsigned", "NO"), ("rights_review_event_id", "bigint(20) unsigned", "NO"), ("candidate_revision_id", "bigint(20) unsigned", "NO"), ("manifest_sha256", "binary(32)", "NO"), ("artifact_set_digest", "binary(32)", "NO")),
    "office_module_release_event": (("release_event_id", "bigint(20) unsigned", "NO"), ("candidate_revision_id", "bigint(20) unsigned", "NO"), ("candidate_id", "bigint(20) unsigned", "NO"), ("manifest_sha256", "binary(32)", "NO"), ("artifact_set_digest", "binary(32)", "NO"), ("creative_review_event_id", "bigint(20) unsigned", "NO"), ("rights_review_event_id", "bigint(20) unsigned", "NO"), ("decision_state", "varchar(24)", "NO"), ("canonical_receipt_sha256", "binary(32)", "YES"), ("canonical_receipt_signature", "mediumblob", "YES"), ("canonical_receipt_key_id", "varchar(128)", "YES"), ("occurred_at", "datetime(6)", "NO")),
    "office_module_release": (("candidate_id", "bigint(20) unsigned", "NO"), ("head_version", "bigint(20) unsigned", "NO"), ("release_event_id", "bigint(20) unsigned", "NO"), ("candidate_revision_id", "bigint(20) unsigned", "NO"), ("manifest_sha256", "binary(32)", "NO"), ("artifact_set_digest", "binary(32)", "NO"), ("status", "varchar(24)", "NO")),
}
_MODULE_PUBLICATION_STAGING_INDEXES = {
    "office_module_creative_review_event": {"PRIMARY": (True, ("creative_review_event_id",)), "uq_office_module_creative_review_identity": (True, ("creative_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest")), "idx_office_module_creative_review_candidate": (False, ("candidate_id", "candidate_revision_id", "occurred_at"))},
    "office_module_creative_review_head": {"PRIMARY": (True, ("candidate_id",)), "uq_office_module_creative_head_event": (True, ("creative_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"))},
    "office_module_rights_review_event": {"PRIMARY": (True, ("rights_review_event_id",)), "uq_office_module_rights_review_identity": (True, ("rights_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest")), "idx_office_module_rights_review_candidate": (False, ("candidate_id", "candidate_revision_id", "occurred_at"))},
    "office_module_rights_review_head": {"PRIMARY": (True, ("candidate_id",)), "uq_office_module_rights_head_event": (True, ("rights_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"))},
    "office_module_release_event": {"PRIMARY": (True, ("release_event_id",)), "uq_office_module_release_event_identity": (True, ("release_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest")), "idx_office_module_release_event_candidate": (False, ("candidate_id", "candidate_revision_id", "occurred_at"))},
    "office_module_release": {"PRIMARY": (True, ("candidate_id",)), "uq_office_module_release_event": (True, ("release_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"))},
}
_MODULE_PUBLICATION_STAGING_FKS = {
    "office_module_creative_review_event": {"fk_office_module_creative_review_revision": (("candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"), "office_module_candidate_revision", ("candidate_revision_id", "candidate_id", "manifest_sha256", "claimed_artifact_set_digest")), "fk_office_module_creative_review_human": (("human_user_id",), "jax_users", ("user_id",)), "fk_office_module_creative_review_mfa": (("mfa_proof_event_id",), "office_mfa_security_event", ("event_id",))},
    "office_module_creative_review_head": {"fk_office_module_creative_head_candidate": (("candidate_id",), "office_module_candidate", ("candidate_id",)), "fk_office_module_creative_head_event": (("creative_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"), "office_module_creative_review_event", ("creative_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"))},
    "office_module_rights_review_event": {"fk_office_module_rights_review_revision": (("candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"), "office_module_candidate_revision", ("candidate_revision_id", "candidate_id", "manifest_sha256", "claimed_artifact_set_digest"))},
    "office_module_rights_review_head": {"fk_office_module_rights_head_candidate": (("candidate_id",), "office_module_candidate", ("candidate_id",)), "fk_office_module_rights_head_event": (("rights_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"), "office_module_rights_review_event", ("rights_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"))},
    "office_module_release_event": {"fk_office_module_release_event_revision": (("candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"), "office_module_candidate_revision", ("candidate_revision_id", "candidate_id", "manifest_sha256", "claimed_artifact_set_digest")), "fk_office_module_release_event_creative": (("creative_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"), "office_module_creative_review_event", ("creative_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest")), "fk_office_module_release_event_rights": (("rights_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"), "office_module_rights_review_event", ("rights_review_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"))},
    "office_module_release": {"fk_office_module_release_candidate": (("candidate_id",), "office_module_candidate", ("candidate_id",)), "fk_office_module_release_event": (("release_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"), "office_module_release_event", ("release_event_id", "candidate_revision_id", "candidate_id", "manifest_sha256", "artifact_set_digest"))},
}
_MODULE_PUBLICATION_STAGING_CHECKS = {
    "chk_office_module_creative_review_decision": "decision='PENDING_CONTRACT'", "chk_office_module_creative_head_version": "head_version>=1", "chk_office_module_rights_review_staging": "critic_invocation_ref<>'' AND verification_state='PENDING_CONTRACT' AND decision='PENDING_CONTRACT'", "chk_office_module_rights_head_version": "head_version>=1", "chk_office_module_release_event_staging": "decision_state='PENDING_CONTRACT' AND canonical_receipt_sha256 IS NULL AND canonical_receipt_signature IS NULL AND canonical_receipt_key_id IS NULL", "chk_office_module_release_head": "head_version>=1 AND status='PENDING_CONTRACT'",
}
_MODULE_PUBLICATION_STAGING_CHECK_TABLES = {
    "chk_office_module_creative_review_decision": "office_module_creative_review_event",
    "chk_office_module_creative_head_version": "office_module_creative_review_head",
    "chk_office_module_rights_review_staging": "office_module_rights_review_event",
    "chk_office_module_rights_head_version": "office_module_rights_review_head",
    "chk_office_module_release_event_staging": "office_module_release_event",
    "chk_office_module_release_head": "office_module_release",
}


def _module_publication_expected_indexes(table: str):
    """MariaDB creates named FK support indexes where no usable prefix exists."""
    expected = dict(_MODULE_PUBLICATION_STAGING_INDEXES[table])
    for name, (local, _target, _remote) in _MODULE_PUBLICATION_STAGING_FKS[table].items():
        if not any(columns[:len(local)] == local for _unique, columns in expected.values()):
            expected[name] = (False, local)
    return expected


def _module_publication_expected_column_metadata(table: str):
    """COLUMN metadata is authority metadata: defaults and AUTO_INCREMENT matter."""
    result = []
    for name, column_type, nullable in _MODULE_PUBLICATION_STAGING_COLUMNS[table]:
        result.append((name, column_type, nullable,
                       "current_timestamp(6)" if name == "occurred_at" else ("NULL" if nullable == "YES" else None),
                       "auto_increment" if table in _MODULE_PUBLICATION_STAGING_EVENT_TABLES and name == _MODULE_PUBLICATION_STAGING_COLUMNS[table][0][0] else "",
                       "ascii" if column_type.startswith("varchar") else None,
                       "ascii_bin" if column_type.startswith("varchar") else None))
    return tuple(result)


async def apply_module_publication_staging_migrations(cursor) -> None:
    """Install durable pending-contract records, never a publication path."""
    if not await _index_exists(cursor, "office_module_candidate_revision", "uq_office_module_candidate_revision_release_identity"):
        await cursor.execute("ALTER TABLE office_module_candidate_revision ADD UNIQUE KEY uq_office_module_candidate_revision_release_identity (candidate_revision_id,candidate_id,manifest_sha256,claimed_artifact_set_digest)")
    for statement in OFFICE_MODULE_PUBLICATION_STAGING_DDL:
        await cursor.execute(statement)
    for table in _MODULE_PUBLICATION_STAGING_EVENT_TABLES:
        for operation in ("UPDATE", "DELETE"):
            await cursor.execute(f"CREATE TRIGGER IF NOT EXISTS `no_{operation.lower()}_{table}` BEFORE {operation} ON `{table}` FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'")
    await assert_module_publication_staging_metadata(cursor)


async def assert_module_publication_staging_metadata(cursor, *, allow_redacted_trigger_body: bool = False) -> None:
    """Attest authority separation and append-only event ledgers."""
    for table, expected_columns in _MODULE_PUBLICATION_STAGING_COLUMNS.items():
        if not await _table_exists(cursor, table):
            raise RuntimeError(f"Office module publication staging table {table} is absent")
        await cursor.execute("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
        if tuple(await cursor.fetchone() or ()) != ("InnoDB",):
            raise RuntimeError(f"Office module publication staging engine {table} is invalid")
        await cursor.execute("SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA,CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (table,))
        found_columns = tuple(tuple(row) for row in await cursor.fetchall())
        if found_columns != _module_publication_expected_column_metadata(table):
            raise RuntimeError(f"Office module publication staging columns for {table} are invalid")
        await cursor.execute("SELECT INDEX_NAME,NON_UNIQUE,COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY INDEX_NAME,SEQ_IN_INDEX", (table,))
        found_indexes = {}
        for name, non_unique, column in await cursor.fetchall():
            unique, columns = found_indexes.get(name, (not bool(non_unique), []))
            found_indexes[name] = (unique, [*columns, column])
        normalized_indexes = {name: (unique, tuple(columns)) for name, (unique, columns) in found_indexes.items()}
        if normalized_indexes != _module_publication_expected_indexes(table):
            raise RuntimeError(f"Office module publication staging indexes for {table} are invalid")
    if not await _index_exists(cursor, "office_module_candidate_revision", "uq_office_module_candidate_revision_release_identity"):
        raise RuntimeError("Office module publication revision identity index is absent")
    await cursor.execute("SELECT k.TABLE_NAME,k.CONSTRAINT_NAME,k.COLUMN_NAME,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,k.REFERENCED_TABLE_SCHEMA,r.UPDATE_RULE,r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.TABLE_NAME=k.TABLE_NAME WHERE k.CONSTRAINT_SCHEMA=DATABASE() AND k.TABLE_NAME LIKE 'office_module_%' AND k.REFERENCED_TABLE_NAME IS NOT NULL ORDER BY k.TABLE_NAME,k.CONSTRAINT_NAME,k.ORDINAL_POSITION")
    found_fks = {}
    for table, name, column, target, target_column, target_schema, update_rule, delete_rule in await cursor.fetchall():
        local, current_target, remote, current_schema, current_update, current_delete = found_fks.get((table, name), ([], target, [], target_schema, update_rule, delete_rule))
        found_fks[(table, name)] = ([*local, column], current_target, [*remote, target_column], current_schema, current_update, current_delete)
    expected_fks = {(table, name): value for table, values in _MODULE_PUBLICATION_STAGING_FKS.items() for name, value in values.items()}
    await cursor.execute("SELECT DATABASE()")
    schema = (await cursor.fetchone())[0]
    normalized_fks = {(table, name): (tuple(local), target, tuple(remote)) for (table, name), (local, target, remote, target_schema, update_rule, delete_rule) in found_fks.items() if table in _MODULE_PUBLICATION_STAGING_FKS}
    if (normalized_fks != expected_fks
            or any(target_schema != schema or update_rule != "RESTRICT" or delete_rule != "RESTRICT"
                   for (table, _name), (_local, _target, _remote, target_schema, update_rule, delete_rule) in found_fks.items()
                   if table in _MODULE_PUBLICATION_STAGING_FKS)):
        raise RuntimeError("Office module publication staging foreign keys are invalid")
    await cursor.execute("SELECT tc.TABLE_NAME,tc.CONSTRAINT_NAME,cc.CHECK_CLAUSE FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME LIKE 'office_module_%' AND tc.CONSTRAINT_TYPE='CHECK'")
    found_checks = {(table, name): clause for table, name, clause in await cursor.fetchall() if table in _MODULE_PUBLICATION_STAGING_COLUMNS}
    def normalized(clause):
        return re.sub(r"[\s`]", "", str(clause).lower()).strip("()")
    expected_checks = {(table, name): normalized(_MODULE_PUBLICATION_STAGING_CHECKS[name]) for name, table in _MODULE_PUBLICATION_STAGING_CHECK_TABLES.items()}
    if {key: normalized(clause) for key, clause in found_checks.items()} != expected_checks:
        raise RuntimeError("Office module publication staging checks are invalid")
    for table in _MODULE_PUBLICATION_STAGING_EVENT_TABLES:
        await cursor.execute("SELECT TRIGGER_NAME,ACTION_TIMING,EVENT_MANIPULATION,ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE=%s", (table,))
        rows = {row[0]: tuple(row[1:]) for row in await cursor.fetchall()}
        expected = {f"no_update_{table}": ("BEFORE", "UPDATE", f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'"), f"no_delete_{table}": ("BEFORE", "DELETE", f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'")}
        if allow_redacted_trigger_body:
            expected = {name: (body[0], body[1], None if rows.get(name, (None, None, None))[2] is None else body[2]) for name, body in expected.items()}
        if rows != expected:
            raise RuntimeError(f"Office module publication staging triggers for {table} are invalid")

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
    "CREATE TABLE IF NOT EXISTS office_bootstrap_state (ceremony_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY, target_user_id INT NOT NULL, identity_actor_user_id INT NOT NULL, identity_proof_event_id BIGINT UNSIGNED NULL, credential_audit_id BIGINT NULL, verification_method VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, basis_ref VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, completed_at DATETIME(6) NOT NULL, CONSTRAINT fk_office_bootstrap_target FOREIGN KEY (target_user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_bootstrap_actor FOREIGN KEY (identity_actor_user_id) REFERENCES jax_users(user_id), CONSTRAINT fk_office_bootstrap_proof FOREIGN KEY (identity_proof_event_id) REFERENCES office_mfa_security_event(event_id), CONSTRAINT fk_office_bootstrap_credential_audit FOREIGN KEY (credential_audit_id) REFERENCES user_admin_audit(id), CONSTRAINT chk_office_bootstrap_identity CHECK (target_user_id=identity_actor_user_id), CONSTRAINT chk_office_bootstrap_basis CHECK (basis_ref <> ''), CONSTRAINT chk_office_bootstrap_verification CHECK ((verification_method='MFA' AND identity_proof_event_id IS NOT NULL AND credential_audit_id IS NOT NULL) OR (verification_method='PASSWORD_REAUTH' AND identity_proof_event_id IS NULL AND credential_audit_id IS NULL))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_bootstrap_state BEFORE UPDATE ON office_bootstrap_state FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_bootstrap_state append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_bootstrap_state BEFORE DELETE ON office_bootstrap_state FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_bootstrap_state append-only'",
)

# 3C2 deliberately separates the mutable invitation lifecycle from the proof
# that made its issuance lawful.  The bearer is identified only by SHA-256;
# neither this schema nor its audit evidence stores a raw invitation link.
# An issuance is inserted before its invitation in the same transaction.  The
# invitation references it one-to-one, so acceptance can never manufacture a
# proof row after the fact.
OFFICE_INVITATION_DDL = (
    "CREATE TABLE IF NOT EXISTS office_invitation_issuance ("
    "issuance_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,"
    "token_hash BINARY(32) NOT NULL,email_normalized VARCHAR(320) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "tenant_id INT NOT NULL,project_id INT NOT NULL,recipient_group_id BIGINT UNSIGNED NULL,target_user_id INT NULL,"
    "target_account_class VARCHAR(24) NOT NULL,grant_role VARCHAR(24) NOT NULL,audience VARCHAR(24) NOT NULL,"
    "issued_by_user_id INT NOT NULL,issuer_profile_version BIGINT NOT NULL,issuer_grant_version BIGINT NOT NULL,"
    "issuer_authorization_kind VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'MFA_PROOF',"
    "issuer_proof_event_id BIGINT UNSIGNED NULL,issuer_mfa_version BIGINT NULL,issuer_mfa_at DATETIME(6) NULL,"
    "issuance_authorization_event_id BIGINT UNSIGNED NOT NULL,proof_digest BINARY(32) NOT NULL,"
    "correlation_id BINARY(16) NOT NULL,issued_at DATETIME(6) NOT NULL,expires_at DATETIME(6) NOT NULL,"
    "UNIQUE KEY uq_office_invitation_issuance_token (token_hash),UNIQUE KEY uq_office_invitation_issuance_binding (issuance_id,project_id,email_normalized,target_user_id),"
    "KEY idx_office_invitation_issuance_scope (project_id,tenant_id,expires_at,issuance_id),"
    "KEY idx_office_invitation_issuance_expiry (expires_at,issuance_id),"
    "CONSTRAINT fk_office_invitation_issuance_scope FOREIGN KEY (project_id,tenant_id) REFERENCES jax_project_scope(project_id,tenant_id),"
    "CONSTRAINT fk_office_invitation_issuance_group_project FOREIGN KEY (recipient_group_id,project_id) REFERENCES office_recipient_group(recipient_group_id,project_id),"
    "CONSTRAINT fk_office_invitation_issuance_issuer FOREIGN KEY (issued_by_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_invitation_issuance_target FOREIGN KEY (target_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_invitation_issuance_profile FOREIGN KEY (issued_by_user_id) REFERENCES office_account_profile(user_id),"
    "CONSTRAINT fk_office_invitation_issuance_basis FOREIGN KEY (project_id,issued_by_user_id) REFERENCES office_project_grant(project_id,user_id),"
    "CONSTRAINT fk_office_invitation_issuance_proof FOREIGN KEY (issuer_proof_event_id) REFERENCES office_mfa_security_event(event_id),"
    "CONSTRAINT fk_office_invitation_issuance_authorization FOREIGN KEY (issuance_authorization_event_id) REFERENCES office_authorization_event(event_id),"
    "CONSTRAINT chk_office_invitation_issuance_shape CHECK ("
    "email_normalized <> '' AND issuer_profile_version>=1 AND issuer_grant_version>=1 AND issued_at < expires_at AND "
    "((issuer_authorization_kind='MFA_PROOF' AND issuer_proof_event_id IS NOT NULL AND issuer_mfa_version >= 0 AND issuer_mfa_at IS NOT NULL) OR "
    "(issuer_authorization_kind='SESSION_AUTHORIZATION' AND issuer_proof_event_id IS NULL AND issuer_mfa_version IS NULL AND issuer_mfa_at IS NULL)) AND "
    "((target_account_class='OFFICE_INTERNAL' AND grant_role='COLLABORATOR' AND audience='INTERNAL' AND recipient_group_id IS NULL) OR "
    "(target_account_class='EXTERNAL_CLIENT' AND grant_role='COLLABORATOR' AND audience='CLIENT_PUBLISHED' AND recipient_group_id IS NOT NULL))"
    ")) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_invitation_issuance BEFORE UPDATE ON office_invitation_issuance FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_issuance append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_invitation_issuance BEFORE DELETE ON office_invitation_issuance FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_issuance append-only'",
    "CREATE TABLE IF NOT EXISTS office_invitation ("
    "invitation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,"
    "issuance_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,expires_at DATETIME(6) NOT NULL,"
    "status VARCHAR(16) NOT NULL DEFAULT 'ISSUED',version BIGINT NOT NULL DEFAULT 1,"
    "project_id INT NOT NULL,email_normalized VARCHAR(320) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,target_user_id INT NULL,"
    "pending_email VARCHAR(320) CHARACTER SET ascii COLLATE ascii_bin GENERATED ALWAYS AS (CASE WHEN status='ISSUED' THEN email_normalized ELSE NULL END) STORED,"
    "accepted_user_id INT NULL,accepted_at DATETIME(6) NULL,revoked_by_user_id INT NULL,revoked_at DATETIME(6) NULL,"
    "UNIQUE KEY uq_office_invitation_issuance (issuance_id),UNIQUE KEY uq_office_invitation_pending_subject (project_id,pending_email),"
    "KEY idx_office_invitation_expiry (status,issuance_id,invitation_id),KEY idx_office_invitation_status_expiry (status,expires_at,invitation_id),KEY idx_office_invitation_project_live (project_id,status,expires_at,invitation_id),"
    "CONSTRAINT fk_office_invitation_issuance FOREIGN KEY (issuance_id) REFERENCES office_invitation_issuance(issuance_id),"
    "CONSTRAINT fk_office_invitation_project FOREIGN KEY (project_id) REFERENCES projects(id),"
    "CONSTRAINT fk_office_invitation_target FOREIGN KEY (target_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_invitation_issuance_binding FOREIGN KEY (issuance_id,project_id,email_normalized,target_user_id) REFERENCES office_invitation_issuance(issuance_id,project_id,email_normalized,target_user_id),"
    "CONSTRAINT fk_office_invitation_accepted_user FOREIGN KEY (accepted_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_invitation_revoker FOREIGN KEY (revoked_by_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT chk_office_invitation_shape CHECK (version >= 1 AND ((status='ISSUED' AND accepted_user_id IS NULL AND accepted_at IS NULL AND revoked_by_user_id IS NULL AND revoked_at IS NULL) OR "
    "(status='ACCEPTED' AND accepted_user_id IS NOT NULL AND accepted_at IS NOT NULL AND revoked_by_user_id IS NULL AND revoked_at IS NULL) OR "
    "(status='REVOKED' AND accepted_user_id IS NULL AND accepted_at IS NULL AND revoked_by_user_id IS NOT NULL AND revoked_at IS NOT NULL) OR "
    "(status='EXPIRED' AND accepted_user_id IS NULL AND accepted_at IS NULL AND revoked_by_user_id IS NULL AND revoked_at IS NULL)))"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS office_invitation_event ("
    "event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,invitation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "event_kind VARCHAR(32) NOT NULL,causation_event_id BIGINT UNSIGNED NULL,actor_user_id INT NULL,bearer_token_hash BINARY(32) NULL,observed_outbox_state VARCHAR(16) NULL,authorization_event_id BIGINT UNSIGNED NULL,invocation_digest BINARY(32) NULL,"
    "correlation_id BINARY(16) NOT NULL,occurred_at DATETIME(6) NOT NULL,"
    "UNIQUE KEY uq_office_invitation_event_kind (invitation_id,event_kind),KEY idx_office_invitation_event_correlation (invitation_id,correlation_id,event_id),KEY idx_office_invitation_event_invitation (invitation_id,occurred_at,event_id),KEY idx_office_invitation_event_authorization (authorization_event_id),"
    "CONSTRAINT fk_office_invitation_event_invitation FOREIGN KEY (invitation_id) REFERENCES office_invitation(invitation_id),"
    "CONSTRAINT fk_office_invitation_event_actor FOREIGN KEY (actor_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_invitation_event_causation FOREIGN KEY (causation_event_id) REFERENCES office_invitation_event(event_id),"
    "CONSTRAINT fk_office_invitation_event_authorization FOREIGN KEY (authorization_event_id) REFERENCES office_authorization_event(event_id),"
    "CONSTRAINT chk_office_invitation_event_shape CHECK ((event_kind='ISSUED' AND causation_event_id IS NULL AND actor_user_id IS NOT NULL AND bearer_token_hash IS NULL AND observed_outbox_state IS NULL) OR "
    "(event_kind='INVITATION_BEARER_PRESENTED' AND causation_event_id IS NOT NULL AND actor_user_id IS NULL AND bearer_token_hash IS NOT NULL AND observed_outbox_state IS NULL) OR "
    "(event_kind='ACCEPTED' AND causation_event_id IS NOT NULL AND actor_user_id IS NOT NULL AND bearer_token_hash IS NULL AND observed_outbox_state IN ('QUEUED','CLAIMED','SMTP_ACCEPTED','UNKNOWN','CANCELLED')) OR "
    "(event_kind='REVOKED' AND causation_event_id IS NOT NULL AND actor_user_id IS NOT NULL AND bearer_token_hash IS NULL AND observed_outbox_state IS NULL) OR "
    "(event_kind='EXPIRED' AND causation_event_id IS NOT NULL AND actor_user_id IS NULL AND bearer_token_hash IS NULL AND observed_outbox_state IS NULL) OR "
    "(event_kind='DELIVERY_OBSERVED' AND causation_event_id IS NOT NULL AND actor_user_id IS NULL AND bearer_token_hash IS NULL AND observed_outbox_state IN ('SMTP_ACCEPTED','UNKNOWN','CANCELLED')))"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_invitation_event BEFORE UPDATE ON office_invitation_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_event append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_invitation_event BEFORE DELETE ON office_invitation_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_event append-only'",
    "CREATE TABLE IF NOT EXISTS office_invitation_outbox ("
    "outbox_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,invitation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "state VARCHAR(16) NOT NULL DEFAULT 'QUEUED',attempt_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,attempt_count INT NOT NULL DEFAULT 0,cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,"
    "payload_ciphertext MEDIUMBLOB NULL,message_id VARCHAR(320) CHARACTER SET ascii COLLATE ascii_bin NULL,result_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,"
    "claimed_at DATETIME(6) NULL,finished_at DATETIME(6) NULL,created_at DATETIME(6) NOT NULL,updated_at DATETIME(6) NOT NULL,"
    "UNIQUE KEY uq_office_invitation_outbox_invitation (invitation_id),KEY idx_office_invitation_outbox_claim (state,created_at,outbox_id),KEY idx_office_invitation_outbox_stale_claim (state,claimed_at,outbox_id),"
    "CONSTRAINT fk_office_invitation_outbox_invitation FOREIGN KEY (invitation_id) REFERENCES office_invitation(invitation_id),"
    "CONSTRAINT chk_office_invitation_outbox_shape CHECK ((state='QUEUED' AND attempt_id IS NULL AND attempt_count=0 AND cancel_requested=FALSE AND payload_ciphertext IS NOT NULL AND result_code IS NULL AND claimed_at IS NULL AND finished_at IS NULL) OR "
    "(state='CLAIMED' AND attempt_id IS NOT NULL AND attempt_id REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' AND attempt_count=1 AND ((cancel_requested=FALSE AND payload_ciphertext IS NOT NULL) OR (cancel_requested=TRUE AND payload_ciphertext IS NULL)) AND message_id IS NULL AND result_code IS NULL AND claimed_at IS NOT NULL AND finished_at IS NULL) OR "
    "(state='SMTP_ACCEPTED' AND attempt_id IS NOT NULL AND attempt_count=1 AND payload_ciphertext IS NULL AND message_id IS NOT NULL AND result_code IS NOT NULL AND finished_at IS NOT NULL) OR "
    "(state='UNKNOWN' AND attempt_id IS NOT NULL AND attempt_count=1 AND payload_ciphertext IS NULL AND message_id IS NULL AND result_code IS NOT NULL AND finished_at IS NOT NULL) OR "
    "(state='CANCELLED' AND cancel_requested=TRUE AND payload_ciphertext IS NULL AND message_id IS NULL AND finished_at IS NOT NULL AND result_code IS NOT NULL))"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_insert BEFORE INSERT ON office_invitation FOR EACH ROW BEGIN IF NEW.status<>'ISSUED' OR NEW.version<>1 OR NOT EXISTS (SELECT 1 FROM office_invitation_issuance s WHERE s.issuance_id=NEW.issuance_id AND s.project_id=NEW.project_id AND BINARY s.email_normalized=BINARY NEW.email_normalized AND s.target_user_id<=>NEW.target_user_id AND s.expires_at=NEW.expires_at) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation issuance binding denied'; END IF; END",
    "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_update BEFORE UPDATE ON office_invitation FOR EACH ROW BEGIN IF NEW.issuance_id<>OLD.issuance_id OR NEW.expires_at<>OLD.expires_at OR NEW.project_id<>OLD.project_id OR BINARY NEW.email_normalized<>BINARY OLD.email_normalized OR NOT (NEW.target_user_id<=>OLD.target_user_id) OR NEW.version<>OLD.version+1 OR OLD.status<>'ISSUED' OR NEW.status NOT IN ('ACCEPTED','REVOKED','EXPIRED') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation transition denied'; END IF; IF NEW.status='ACCEPTED' AND (OLD.target_user_id IS NOT NULL AND NEW.accepted_user_id<>OLD.target_user_id OR NOT EXISTS (SELECT 1 FROM office_invitation_event accepted JOIN office_invitation_event bearer ON bearer.event_id=accepted.causation_event_id JOIN office_invitation_event issued ON issued.event_id=bearer.causation_event_id JOIN office_invitation_issuance s ON s.issuance_id=OLD.issuance_id WHERE accepted.invitation_id=OLD.invitation_id AND accepted.event_kind='ACCEPTED' AND accepted.actor_user_id=NEW.accepted_user_id AND bearer.invitation_id=OLD.invitation_id AND bearer.event_kind='INVITATION_BEARER_PRESENTED' AND bearer.bearer_token_hash=s.token_hash AND issued.invitation_id=OLD.invitation_id AND issued.event_kind='ISSUED' AND accepted.correlation_id=bearer.correlation_id)) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation acceptance evidence missing'; END IF; IF NEW.status='REVOKED' AND NOT EXISTS (SELECT 1 FROM office_invitation_event e JOIN office_invitation_event issued ON issued.event_id=e.causation_event_id WHERE e.invitation_id=OLD.invitation_id AND e.event_kind='REVOKED' AND e.actor_user_id=NEW.revoked_by_user_id AND issued.invitation_id=OLD.invitation_id AND issued.event_kind='ISSUED') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation revocation evidence missing'; END IF; IF NEW.status='EXPIRED' AND NOT EXISTS (SELECT 1 FROM office_invitation_event e JOIN office_invitation_event issued ON issued.event_id=e.causation_event_id WHERE e.invitation_id=OLD.invitation_id AND e.event_kind='EXPIRED' AND issued.invitation_id=OLD.invitation_id AND issued.event_kind='ISSUED') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation expiry evidence missing'; END IF; IF NEW.status IN ('ACCEPTED','REVOKED','EXPIRED') THEN UPDATE office_invitation_outbox SET state='CANCELLED',cancel_requested=TRUE,payload_ciphertext=NULL,result_code='INVITATION_TERMINATED',finished_at=UTC_TIMESTAMP(6),updated_at=IF(UTC_TIMESTAMP(6)>updated_at,UTC_TIMESTAMP(6),DATE_ADD(updated_at,INTERVAL 1 MICROSECOND)) WHERE invitation_id=OLD.invitation_id AND state='QUEUED'; UPDATE office_invitation_outbox SET cancel_requested=TRUE,payload_ciphertext=NULL,updated_at=IF(UTC_TIMESTAMP(6)>updated_at,UTC_TIMESTAMP(6),DATE_ADD(updated_at,INTERVAL 1 MICROSECOND)) WHERE invitation_id=OLD.invitation_id AND state='CLAIMED'; END IF; END",
    "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_event_insert BEFORE INSERT ON office_invitation_event FOR EACH ROW BEGIN IF NEW.event_kind='ISSUED' AND NOT EXISTS (SELECT 1 FROM office_invitation i JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED' AND s.issued_by_user_id=NEW.actor_user_id AND NEW.correlation_id=s.correlation_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation issued evidence denied'; END IF; IF NEW.event_kind='INVITATION_BEARER_PRESENTED' AND NOT EXISTS (SELECT 1 FROM office_invitation i JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id JOIN office_invitation_event issued ON issued.event_id=NEW.causation_event_id WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED' AND s.token_hash=NEW.bearer_token_hash AND issued.invitation_id=i.invitation_id AND issued.event_kind='ISSUED') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation bearer evidence denied'; END IF; IF NEW.event_kind='ACCEPTED' AND NOT EXISTS (SELECT 1 FROM office_invitation i JOIN office_invitation_event bearer ON bearer.event_id=NEW.causation_event_id JOIN office_invitation_outbox o ON o.invitation_id=i.invitation_id WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED' AND (i.target_user_id IS NULL OR i.target_user_id=NEW.actor_user_id) AND bearer.invitation_id=i.invitation_id AND bearer.event_kind='INVITATION_BEARER_PRESENTED' AND NEW.correlation_id=bearer.correlation_id AND o.state=NEW.observed_outbox_state) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation acceptance event denied'; END IF; IF NEW.event_kind IN ('REVOKED','EXPIRED') AND NOT EXISTS (SELECT 1 FROM office_invitation i JOIN office_invitation_event issued ON issued.event_id=NEW.causation_event_id WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED' AND issued.invitation_id=i.invitation_id AND issued.event_kind='ISSUED') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation causal evidence denied'; END IF; IF NEW.event_kind='DELIVERY_OBSERVED' AND NOT EXISTS (SELECT 1 FROM office_invitation i JOIN office_invitation_event issued ON issued.event_id=NEW.causation_event_id JOIN office_invitation_outbox o ON o.invitation_id=i.invitation_id WHERE i.invitation_id=NEW.invitation_id AND issued.invitation_id=i.invitation_id AND issued.event_kind='ISSUED' AND o.state=NEW.observed_outbox_state) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation delivery evidence denied'; END IF; END",
    "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_outbox_insert BEFORE INSERT ON office_invitation_outbox FOR EACH ROW BEGIN IF NEW.state<>'QUEUED' OR NEW.attempt_id IS NOT NULL OR NEW.attempt_count<>0 OR NEW.cancel_requested<>FALSE OR NEW.payload_ciphertext IS NULL OR NEW.message_id IS NOT NULL OR NEW.result_code IS NOT NULL OR NEW.claimed_at IS NOT NULL OR NEW.finished_at IS NOT NULL OR NEW.created_at<>NEW.updated_at OR NOT EXISTS (SELECT 1 FROM office_invitation i WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation outbox initial state denied'; END IF; END",
    "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_outbox_update BEFORE UPDATE ON office_invitation_outbox FOR EACH ROW BEGIN IF NEW.outbox_id<>OLD.outbox_id OR NEW.invitation_id<>OLD.invitation_id OR NEW.created_at<>OLD.created_at OR NEW.updated_at<=OLD.updated_at THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation outbox identity denied'; END IF; IF OLD.state='QUEUED' AND NOT ((NEW.state='CANCELLED' AND NEW.attempt_id IS NULL AND NEW.attempt_count=0 AND NEW.cancel_requested=TRUE AND NEW.payload_ciphertext IS NULL AND NEW.message_id IS NULL AND NEW.result_code IS NOT NULL AND NEW.claimed_at IS NULL AND NEW.finished_at IS NOT NULL) OR (NEW.state='CLAIMED' AND NEW.attempt_id IS NOT NULL AND NEW.attempt_count=1 AND NEW.cancel_requested=FALSE AND NEW.payload_ciphertext<=>OLD.payload_ciphertext AND NEW.claimed_at IS NOT NULL AND NEW.finished_at IS NULL AND NEW.message_id IS NULL AND NEW.result_code IS NULL)) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation outbox claim denied'; END IF; IF OLD.state='CLAIMED' AND NOT ((NEW.state='CLAIMED' AND NEW.attempt_id=OLD.attempt_id AND NEW.attempt_count=OLD.attempt_count AND NEW.cancel_requested=TRUE AND NEW.payload_ciphertext IS NULL AND NEW.claimed_at=OLD.claimed_at AND NEW.finished_at IS NULL AND NEW.message_id IS NULL AND NEW.result_code IS NULL) OR (NEW.state IN ('SMTP_ACCEPTED','UNKNOWN','CANCELLED') AND OLD.cancel_requested=FALSE AND NEW.attempt_id=OLD.attempt_id AND NEW.attempt_count=OLD.attempt_count AND NEW.payload_ciphertext IS NULL AND NEW.claimed_at=OLD.claimed_at AND NEW.finished_at IS NOT NULL) OR (NEW.state IN ('SMTP_ACCEPTED','UNKNOWN','CANCELLED') AND OLD.cancel_requested=TRUE AND NEW.attempt_id=OLD.attempt_id AND NEW.attempt_count=OLD.attempt_count AND NEW.payload_ciphertext IS NULL AND NEW.claimed_at=OLD.claimed_at AND NEW.finished_at IS NOT NULL)) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation outbox terminal denied'; END IF; IF OLD.state IN ('SMTP_ACCEPTED','UNKNOWN','CANCELLED') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation outbox terminal immutable'; END IF; END",
    "CREATE TABLE IF NOT EXISTS office_invitation_mutation_receipt ("
    "idempotency_key CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,request_digest BINARY(32) NOT NULL,"
    "operation VARCHAR(16) NOT NULL,actor_user_id INT NOT NULL,project_id INT NOT NULL,"
    "invitation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,expected_version BIGINT NOT NULL,response_version BIGINT NOT NULL,"
    "result_event_id BIGINT UNSIGNED NOT NULL,authorization_event_id BIGINT UNSIGNED NOT NULL,response_contract VARCHAR(48) NOT NULL,"
    "expires_at DATETIME(6) NULL,created_at DATETIME(6) NOT NULL,"
    "UNIQUE KEY uq_office_invitation_mutation_receipt_invitation_operation (invitation_id,operation),"
    "KEY idx_office_invitation_mutation_receipt_actor (actor_user_id,created_at,idempotency_key),"
    "CONSTRAINT fk_office_invitation_mutation_receipt_actor FOREIGN KEY (actor_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_invitation_mutation_receipt_project FOREIGN KEY (project_id) REFERENCES projects(id),"
    "CONSTRAINT fk_office_invitation_mutation_receipt_invitation FOREIGN KEY (invitation_id) REFERENCES office_invitation(invitation_id),"
    "CONSTRAINT fk_office_invitation_mutation_receipt_event FOREIGN KEY (result_event_id) REFERENCES office_invitation_event(event_id),"
    "CONSTRAINT fk_office_invitation_mutation_receipt_authorization FOREIGN KEY (authorization_event_id) REFERENCES office_authorization_event(event_id),"
    "CONSTRAINT chk_office_invitation_mutation_receipt_shape CHECK (request_digest <> 0x00 AND response_contract='office-invitation-mutation-response/v1' AND "
    "((operation='ISSUE' AND expected_version=0 AND response_version=1 AND expires_at IS NOT NULL) OR "
    "(operation='REVOKE' AND expected_version>=1 AND response_version=expected_version+1 AND expires_at IS NULL)))"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_mutation_receipt_insert BEFORE INSERT ON office_invitation_mutation_receipt FOR EACH ROW BEGIN IF (NEW.operation='ISSUE' AND NOT EXISTS (SELECT 1 FROM office_invitation i JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id JOIN office_invitation_event e ON e.event_id=NEW.result_event_id JOIN office_authorization_event a ON a.event_id=e.authorization_event_id WHERE i.invitation_id=NEW.invitation_id AND i.project_id=NEW.project_id AND i.version=NEW.response_version AND s.issued_by_user_id=NEW.actor_user_id AND s.issuance_authorization_event_id=NEW.authorization_event_id AND e.authorization_event_id=NEW.authorization_event_id AND e.invocation_digest=NEW.request_digest AND e.invitation_id=i.invitation_id AND e.event_kind='ISSUED' AND e.actor_user_id=NEW.actor_user_id AND NEW.expires_at=s.expires_at AND a.event_kind='AUTHORIZATION' AND a.actor_user_id=NEW.actor_user_id AND a.operation='ADMINISTER_PROJECT_MEMBERS' AND a.requested_project_id=NEW.project_id AND a.project_id=NEW.project_id AND a.result='ALLOWED')) OR (NEW.operation='REVOKE' AND NOT EXISTS (SELECT 1 FROM office_invitation i JOIN office_invitation_event e ON e.event_id=NEW.result_event_id JOIN office_authorization_event a ON a.event_id=e.authorization_event_id WHERE i.invitation_id=NEW.invitation_id AND i.project_id=NEW.project_id AND i.version=NEW.response_version AND e.authorization_event_id=NEW.authorization_event_id AND e.invocation_digest=NEW.request_digest AND e.invitation_id=i.invitation_id AND e.event_kind='REVOKED' AND e.actor_user_id=NEW.actor_user_id AND a.event_kind='AUTHORIZATION' AND a.actor_user_id=NEW.actor_user_id AND a.operation='ADMINISTER_PROJECT_MEMBERS' AND a.requested_project_id=NEW.project_id AND a.project_id=NEW.project_id AND a.result='ALLOWED')) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_mutation_receipt companion denied'; END IF; END",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_invitation_mutation_receipt BEFORE UPDATE ON office_invitation_mutation_receipt FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_mutation_receipt append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_invitation_mutation_receipt BEFORE DELETE ON office_invitation_mutation_receipt FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_mutation_receipt append-only'",
)


def _replace_invitation_guard(statement_prefix: str, *edits: tuple[str, str]) -> None:
    """Apply a narrowly attested lifecycle amendment to one DDL guard."""
    global OFFICE_INVITATION_DDL
    statements = list(OFFICE_INVITATION_DDL)
    index = next(i for i, statement in enumerate(statements) if statement.startswith(statement_prefix))
    amended = statements[index]
    for old, new in edits:
        if amended.count(old) != 1:
            raise RuntimeError("Office invitation guard source is not the expected predecessor")
        amended = amended.replace(old, new)
    statements[index] = amended
    OFFICE_INVITATION_DDL = tuple(statements)


_replace_invitation_guard(
    "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_update",
    (
        "JOIN office_invitation_issuance s ON s.issuance_id=OLD.issuance_id WHERE accepted.invitation_id",
        "JOIN office_invitation_issuance s ON s.issuance_id=OLD.issuance_id JOIN office_invitation_outbox o ON o.invitation_id=OLD.invitation_id WHERE accepted.invitation_id",
    ),
    (
        "accepted.actor_user_id=NEW.accepted_user_id AND bearer.invitation_id",
        "accepted.actor_user_id=NEW.accepted_user_id AND accepted.observed_outbox_state=o.state AND UTC_TIMESTAMP(6)<s.expires_at AND bearer.invitation_id",
    ),
    (
        "JOIN office_invitation_event issued ON issued.event_id=e.causation_event_id WHERE e.invitation_id=OLD.invitation_id AND e.event_kind='EXPIRED' AND issued.invitation_id=OLD.invitation_id AND issued.event_kind='ISSUED'",
        "JOIN office_invitation_event issued ON issued.event_id=e.causation_event_id JOIN office_invitation_issuance s ON s.issuance_id=OLD.issuance_id WHERE e.invitation_id=OLD.invitation_id AND e.event_kind='EXPIRED' AND issued.invitation_id=OLD.invitation_id AND issued.event_kind='ISSUED' AND UTC_TIMESTAMP(6)>=s.expires_at",
    ),
)
_replace_invitation_guard(
    "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_event_insert",
    (
        "JOIN office_invitation_outbox o ON o.invitation_id=i.invitation_id WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED' AND (i.target_user_id",
        "JOIN office_invitation_outbox o ON o.invitation_id=i.invitation_id JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED' AND (i.target_user_id",
    ),
    (
        "NEW.correlation_id=bearer.correlation_id AND o.state=NEW.observed_outbox_state",
        "NEW.correlation_id=bearer.correlation_id AND o.state=NEW.observed_outbox_state AND UTC_TIMESTAMP(6)<s.expires_at",
    ),
    (
        "JOIN office_invitation_event issued ON issued.event_id=NEW.causation_event_id WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED' AND issued.invitation_id=i.invitation_id AND issued.event_kind='ISSUED'",
        "JOIN office_invitation_event issued ON issued.event_id=NEW.causation_event_id JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED' AND issued.invitation_id=i.invitation_id AND issued.event_kind='ISSUED' AND UTC_TIMESTAMP(6)>=s.expires_at",
    ),
)
_replace_invitation_guard(
    "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_outbox_update",
    (
        "IF OLD.state IN ('SMTP_ACCEPTED','UNKNOWN','CANCELLED') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation outbox terminal immutable'; END IF",
        "IF OLD.state='UNKNOWN' AND NOT (NEW.state='SMTP_ACCEPTED' AND NEW.attempt_id=OLD.attempt_id AND NEW.attempt_count=OLD.attempt_count AND NEW.cancel_requested=OLD.cancel_requested AND NEW.payload_ciphertext IS NULL AND NEW.message_id IS NOT NULL AND NEW.result_code='SMTP_ACCEPTED' AND NEW.claimed_at=OLD.claimed_at AND NEW.finished_at IS NOT NULL) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation outbox terminal immutable'; END IF; IF OLD.state IN ('SMTP_ACCEPTED','CANCELLED') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation outbox terminal immutable'; END IF",
    ),
)
_replace_invitation_guard(
    "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_outbox_update",
    (
        "(NEW.state IN ('SMTP_ACCEPTED','UNKNOWN','CANCELLED') AND OLD.cancel_requested=FALSE AND NEW.attempt_id=OLD.attempt_id AND NEW.attempt_count=OLD.attempt_count AND NEW.payload_ciphertext",
        "(NEW.state IN ('SMTP_ACCEPTED','UNKNOWN','CANCELLED') AND OLD.cancel_requested=FALSE AND NEW.attempt_id=OLD.attempt_id AND NEW.attempt_count=OLD.attempt_count AND NEW.cancel_requested=OLD.cancel_requested AND NEW.payload_ciphertext",
    ),
    (
        "(NEW.state IN ('SMTP_ACCEPTED','UNKNOWN','CANCELLED') AND OLD.cancel_requested=TRUE AND NEW.attempt_id=OLD.attempt_id AND NEW.attempt_count=OLD.attempt_count AND NEW.payload_ciphertext",
        "(NEW.state IN ('SMTP_ACCEPTED','UNKNOWN','CANCELLED') AND OLD.cancel_requested=TRUE AND NEW.attempt_id=OLD.attempt_id AND NEW.attempt_count=OLD.attempt_count AND NEW.cancel_requested=OLD.cancel_requested AND NEW.payload_ciphertext",
    ),
)
_replace_invitation_guard(
    "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_event_insert",
    (
        "IF NEW.event_kind IN ('REVOKED','EXPIRED') AND NOT EXISTS (SELECT 1 FROM office_invitation i JOIN office_invitation_event issued ON issued.event_id=NEW.causation_event_id JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED' AND issued.invitation_id=i.invitation_id AND issued.event_kind='ISSUED' AND UTC_TIMESTAMP(6)>=s.expires_at) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation causal evidence denied'; END IF",
        "IF NEW.event_kind='REVOKED' AND NOT EXISTS (SELECT 1 FROM office_invitation i JOIN office_invitation_event issued ON issued.event_id=NEW.causation_event_id WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED' AND issued.invitation_id=i.invitation_id AND issued.event_kind='ISSUED') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation causal evidence denied'; END IF; IF NEW.event_kind='EXPIRED' AND NOT EXISTS (SELECT 1 FROM office_invitation i JOIN office_invitation_event issued ON issued.event_id=NEW.causation_event_id JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id WHERE i.invitation_id=NEW.invitation_id AND i.status='ISSUED' AND issued.invitation_id=i.invitation_id AND issued.event_kind='ISSUED' AND UTC_TIMESTAMP(6)>=s.expires_at) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='office_invitation expiry evidence denied'; END IF",
    ),
)

# These records are the Office half of an already immutable JAX namespace
# mutation.  They deliberately have their own receipt key: the JAX request is
# re-authorized by the core before it can be read, and this FK makes a partial
# "Office says done" record structurally impossible.
OFFICE_MEMBERSHIP_RECEIPT_DDL = (
    "CREATE TABLE IF NOT EXISTS office_membership_mutation_receipt ("
    "namespace VARCHAR(32) NOT NULL,idempotency_key CHAR(36) NOT NULL,request_digest BINARY(32) NOT NULL,"
    "operation VARCHAR(32) NOT NULL,actor_user_id INT NOT NULL,target_tenant_id INT NOT NULL,project_id INT NOT NULL,target_user_id INT NOT NULL,"
    "expected_version BIGINT NOT NULL,grant_role VARCHAR(24) NULL,audience VARCHAR(24) NULL,execution_kind VARCHAR(32) NOT NULL,"
    "jax_membership_id CHAR(36) NOT NULL,jax_event_id CHAR(36) NOT NULL,office_grant_version BIGINT NOT NULL,office_grant_status VARCHAR(16) NOT NULL,"
    "external_relation_id BIGINT UNSIGNED NULL,external_relation_version BIGINT NULL,recipient_group_id BIGINT UNSIGNED NULL,authorization_event_id BIGINT UNSIGNED NOT NULL,"
    "created_at DATETIME(6) NOT NULL,"
    "PRIMARY KEY(namespace,idempotency_key),UNIQUE KEY uq_office_membership_receipt_jax_event(jax_event_id),"
    "KEY idx_office_membership_receipt_jax_request(namespace,idempotency_key),"
    "CONSTRAINT fk_office_membership_receipt_jax_request FOREIGN KEY(namespace,idempotency_key) REFERENCES jax_namespace_membership_mutation_request(namespace,idempotency_key),"
    "CONSTRAINT fk_office_membership_receipt_jax_membership FOREIGN KEY(jax_membership_id) REFERENCES jax_project_membership(membership_id),"
    "CONSTRAINT fk_office_membership_receipt_jax_event FOREIGN KEY(jax_event_id) REFERENCES jax_project_membership_event(event_id),"
    "CONSTRAINT fk_office_membership_receipt_actor FOREIGN KEY(actor_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_membership_receipt_target FOREIGN KEY(target_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_membership_receipt_scope FOREIGN KEY(project_id,target_tenant_id) REFERENCES jax_project_scope(project_id,tenant_id),"
    "CONSTRAINT fk_office_membership_receipt_grant FOREIGN KEY(project_id,target_user_id) REFERENCES office_project_grant(project_id,user_id),"
    "CONSTRAINT fk_office_membership_receipt_auth FOREIGN KEY(authorization_event_id) REFERENCES office_authorization_event(event_id),"
    "CONSTRAINT fk_office_membership_receipt_relation FOREIGN KEY(external_relation_id,project_id,target_user_id,recipient_group_id) REFERENCES office_external_relation(relation_id,project_id,user_id,recipient_group_id),"
    "CONSTRAINT chk_office_membership_receipt_operation CHECK(operation IN ('GRANT_OFFICE_MEMBER','REVOKE_OFFICE_MEMBER')),"
    "CONSTRAINT chk_office_membership_receipt_expected_version CHECK(expected_version >= 0),"
    "CONSTRAINT chk_office_membership_receipt_grant CHECK(grant_role IN ('PROJECT_ADMIN','COLLABORATOR') AND audience IN ('INTERNAL','CLIENT_PUBLISHED') AND ((operation='GRANT_OFFICE_MEMBER' AND office_grant_status='ACTIVE') OR (operation='REVOKE_OFFICE_MEMBER' AND office_grant_status='REVOKED'))),"
    "CONSTRAINT chk_office_membership_receipt_external CHECK((audience='INTERNAL' AND external_relation_id IS NULL AND external_relation_version IS NULL AND recipient_group_id IS NULL) OR (audience='CLIENT_PUBLISHED' AND grant_role='COLLABORATOR' AND external_relation_id IS NOT NULL AND external_relation_version IS NOT NULL AND recipient_group_id IS NOT NULL)),"
    "CONSTRAINT chk_office_membership_receipt_execution CHECK(execution_kind IN ('INTERACTIVE_USER','INVITATION_ACCEPTANCE'))"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE={membership_receipt_collation}",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_membership_mutation_receipt BEFORE UPDATE ON office_membership_mutation_receipt FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_membership_mutation_receipt append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_membership_mutation_receipt BEFORE DELETE ON office_membership_mutation_receipt FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_membership_mutation_receipt append-only'",
)

# A role change never calls the JAX membership mutator: the canonical
# membership remains present and only the Office authorization role changes.
# Its replay evidence is consequently a separate immutable ledger rather than
# an overloaded membership-mutation receipt.
OFFICE_PROJECT_ROLE_RECEIPT_DDL = (
    "CREATE TABLE IF NOT EXISTS office_project_role_mutation_receipt ("
    "receipt_id CHAR(36) NOT NULL,idempotency_key CHAR(36) NOT NULL,request_digest BINARY(32) NOT NULL,"
    "actor_user_id INT NOT NULL,target_tenant_id INT NOT NULL,project_id INT NOT NULL,target_user_id INT NOT NULL,"
    "old_grant_role VARCHAR(24) NOT NULL,new_grant_role VARCHAR(24) NOT NULL,expected_version BIGINT NOT NULL,result_version BIGINT NOT NULL,"
    "authorization_event_id BIGINT UNSIGNED NOT NULL,created_at DATETIME(6) NOT NULL,"
    "PRIMARY KEY(receipt_id),UNIQUE KEY uq_office_project_role_receipt_idempotency(idempotency_key),"
    "KEY idx_office_project_role_receipt_scope(project_id,target_user_id,created_at),"
    "CONSTRAINT fk_office_project_role_receipt_actor FOREIGN KEY(actor_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_project_role_receipt_target FOREIGN KEY(target_user_id) REFERENCES jax_users(user_id),"
    "CONSTRAINT fk_office_project_role_receipt_scope FOREIGN KEY(project_id,target_tenant_id) REFERENCES jax_project_scope(project_id,tenant_id),"
    "CONSTRAINT fk_office_project_role_receipt_grant FOREIGN KEY(project_id,target_user_id) REFERENCES office_project_grant(project_id,user_id),"
    "CONSTRAINT fk_office_project_role_receipt_auth FOREIGN KEY(authorization_event_id) REFERENCES office_authorization_event(event_id),"
    "CONSTRAINT chk_office_project_role_receipt_shape CHECK(old_grant_role IN ('PROJECT_ADMIN','COLLABORATOR') AND new_grant_role IN ('PROJECT_ADMIN','COLLABORATOR') AND old_grant_role<>new_grant_role AND expected_version>=1 AND result_version=expected_version+1)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_project_role_mutation_receipt BEFORE UPDATE ON office_project_role_mutation_receipt FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_project_role_mutation_receipt append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_project_role_mutation_receipt BEFORE DELETE ON office_project_role_mutation_receipt FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_project_role_mutation_receipt append-only'",
)

OFFICE_PROFILE_REVOCATION_DDL = (
    "CREATE TABLE IF NOT EXISTS office_profile_revocation_event (event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,actor_user_id INT NOT NULL,target_user_id INT NOT NULL,action VARCHAR(32) NOT NULL,version_before BIGINT NOT NULL,version_after BIGINT NOT NULL,reason_code VARCHAR(64) NOT NULL,authorization_event_id BIGINT UNSIGNED NOT NULL,occurred_at DATETIME(6) NOT NULL,KEY idx_office_profile_revocation_target(target_user_id,occurred_at,event_id),CONSTRAINT fk_office_profile_revocation_actor FOREIGN KEY(actor_user_id) REFERENCES jax_users(user_id),CONSTRAINT fk_office_profile_revocation_target FOREIGN KEY(target_user_id) REFERENCES office_account_profile(user_id),CONSTRAINT fk_office_profile_revocation_auth FOREIGN KEY(authorization_event_id) REFERENCES office_authorization_event(event_id),CONSTRAINT chk_office_profile_revocation_shape CHECK(action='REVOKE_PROFILE' AND version_before >= 1 AND version_after=version_before+1 AND reason_code<>'')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TRIGGER IF NOT EXISTS no_update_office_profile_revocation_event BEFORE UPDATE ON office_profile_revocation_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_profile_revocation_event append-only'",
    "CREATE TRIGGER IF NOT EXISTS no_delete_office_profile_revocation_event BEFORE DELETE ON office_profile_revocation_event FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_profile_revocation_event append-only'",
)

_MEMBERSHIP_RECEIPT_TRIGGERS = {
    "no_update_office_membership_mutation_receipt": ("office_membership_mutation_receipt", "BEFORE", "UPDATE", "ROW", "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_membership_mutation_receipt append-only'"),
    "no_delete_office_membership_mutation_receipt": ("office_membership_mutation_receipt", "BEFORE", "DELETE", "ROW", "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_membership_mutation_receipt append-only'"),
}
_PROJECT_ROLE_RECEIPT_TRIGGERS = {
    "no_update_office_project_role_mutation_receipt": ("office_project_role_mutation_receipt", "BEFORE", "UPDATE", "ROW", "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_project_role_mutation_receipt append-only'"),
    "no_delete_office_project_role_mutation_receipt": ("office_project_role_mutation_receipt", "BEFORE", "DELETE", "ROW", "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_project_role_mutation_receipt append-only'"),
}
_PROJECT_ROLE_RECEIPT_INDEXES = {
    "PRIMARY": (("receipt_id",), True),
    "uq_office_project_role_receipt_idempotency": (("idempotency_key",), True),
    "idx_office_project_role_receipt_scope": (("project_id", "target_user_id", "created_at"), False),
    "fk_office_project_role_receipt_actor": (("actor_user_id",), False),
    "fk_office_project_role_receipt_target": (("target_user_id",), False),
    "fk_office_project_role_receipt_scope": (("project_id", "target_tenant_id"), False),
    "fk_office_project_role_receipt_auth": (("authorization_event_id",), False),
}
_PROJECT_ROLE_RECEIPT_FOREIGN_KEYS = {
    "fk_office_project_role_receipt_actor": (("actor_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
    "fk_office_project_role_receipt_target": (("target_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
    "fk_office_project_role_receipt_scope": (("project_id", "target_tenant_id"), "jax_project_scope", ("project_id", "tenant_id"), "RESTRICT", "RESTRICT"),
    "fk_office_project_role_receipt_grant": (("project_id", "target_user_id"), "office_project_grant", ("project_id", "user_id"), "RESTRICT", "RESTRICT"),
    "fk_office_project_role_receipt_auth": (("authorization_event_id",), "office_authorization_event", ("event_id",), "RESTRICT", "RESTRICT"),
}
_PROFILE_REVOCATION_TRIGGERS = {
    "no_update_office_profile_revocation_event": ("office_profile_revocation_event", "BEFORE", "UPDATE", "ROW", "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_profile_revocation_event append-only'"),
    "no_delete_office_profile_revocation_event": ("office_profile_revocation_event", "BEFORE", "DELETE", "ROW", "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_profile_revocation_event append-only'"),
}

_PROJECT_ROLE_RECEIPT_COLUMNS = (
    ("receipt_id", "char", "char(36)", "NO", "utf8mb4", "utf8mb4_uca1400_ai_ci", "", None),
    ("idempotency_key", "char", "char(36)", "NO", "utf8mb4", "utf8mb4_uca1400_ai_ci", "", None),
    ("request_digest", "binary", "binary(32)", "NO", None, None, "", None),
    ("actor_user_id", "int", "int(11)", "NO", None, None, "", None),
    ("target_tenant_id", "int", "int(11)", "NO", None, None, "", None),
    ("project_id", "int", "int(11)", "NO", None, None, "", None),
    ("target_user_id", "int", "int(11)", "NO", None, None, "", None),
    ("old_grant_role", "varchar", "varchar(24)", "NO", "utf8mb4", "utf8mb4_uca1400_ai_ci", "", None),
    ("new_grant_role", "varchar", "varchar(24)", "NO", "utf8mb4", "utf8mb4_uca1400_ai_ci", "", None),
    ("expected_version", "bigint", "bigint(20)", "NO", None, None, "", None),
    ("result_version", "bigint", "bigint(20)", "NO", None, None, "", None),
    ("authorization_event_id", "bigint", "bigint(20) unsigned", "NO", None, None, "", None),
    ("created_at", "datetime", "datetime(6)", "NO", None, None, "", None),
)
_PROJECT_ROLE_RECEIPT_CHECK_DIGEST = "1b457db7058f455d38016135d41ca0b1aef9c9d9e49f22c176403a63e65a76c6"

# These ledgers are release authority, not diagnostics.  The migration
# principal therefore attests the complete physical contract before it can
# stamp readiness; checking only a homonymous trigger and one index would let
# a same-named, weaker table pass.
_MEMBERSHIP_RECEIPT_COLUMNS = (
    ("namespace", "varchar", "varchar(32)", "NO", "utf8mb4", "utf8mb4_unicode_ci", "", None),
    ("idempotency_key", "char", "char(36)", "NO", "utf8mb4", "utf8mb4_unicode_ci", "", None),
    ("request_digest", "binary", "binary(32)", "NO", None, None, "", None),
    ("operation", "varchar", "varchar(32)", "NO", "utf8mb4", "utf8mb4_unicode_ci", "", None),
    ("actor_user_id", "int", "int(11)", "NO", None, None, "", None),
    ("target_tenant_id", "int", "int(11)", "NO", None, None, "", None),
    ("project_id", "int", "int(11)", "NO", None, None, "", None),
    ("target_user_id", "int", "int(11)", "NO", None, None, "", None),
    ("expected_version", "bigint", "bigint(20)", "NO", None, None, "", None),
    ("grant_role", "varchar", "varchar(24)", "YES", "utf8mb4", "utf8mb4_unicode_ci", "", "NULL"),
    ("audience", "varchar", "varchar(24)", "YES", "utf8mb4", "utf8mb4_unicode_ci", "", "NULL"),
    ("execution_kind", "varchar", "varchar(32)", "NO", "utf8mb4", "utf8mb4_unicode_ci", "", None),
    ("jax_membership_id", "char", "char(36)", "NO", "utf8mb4", "utf8mb4_unicode_ci", "", None),
    ("jax_event_id", "char", "char(36)", "NO", "utf8mb4", "utf8mb4_unicode_ci", "", None),
    ("office_grant_version", "bigint", "bigint(20)", "NO", None, None, "", None),
    ("office_grant_status", "varchar", "varchar(16)", "NO", "utf8mb4", "utf8mb4_unicode_ci", "", None),
    ("external_relation_id", "bigint", "bigint(20) unsigned", "YES", None, None, "", "NULL"),
    ("external_relation_version", "bigint", "bigint(20)", "YES", None, None, "", "NULL"),
    ("recipient_group_id", "bigint", "bigint(20) unsigned", "YES", None, None, "", "NULL"),
    ("authorization_event_id", "bigint", "bigint(20) unsigned", "NO", None, None, "", None),
    ("created_at", "datetime", "datetime(6)", "NO", None, None, "", None),
)


async def _membership_receipt_collation(cur) -> str:
    """Use the actual JAX request collation for every receipt FK string.

    JAX owns the parent tables and a fresh schema inherits the database's
    collation.  An older installed parent can legitimately use a different
    collation, so spelling one Office default here would make the receipt DDL
    fail before it can attest its foreign keys.
    """
    await cur.execute(
        "SELECT CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='jax_namespace_membership_mutation_request' "
        "AND COLUMN_NAME='namespace'"
    )
    row = await cur.fetchone()
    if row is None or len(row) != 2 or row[0] != "utf8mb4" or not isinstance(row[1], str):
        raise RuntimeError("JAX membership request namespace collation is unavailable")
    collation = row[1]
    if re.fullmatch(r"[a-z0-9_]+", collation) is None:
        raise RuntimeError("JAX membership request namespace collation is invalid")
    for table, column in (
        ("jax_namespace_membership_mutation_request", "idempotency_key"),
        ("jax_project_membership", "membership_id"),
        ("jax_project_membership_event", "event_id"),
    ):
        await cur.execute(
            "SELECT CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
            (table, column),
        )
        if tuple(await cur.fetchone() or ()) != ("utf8mb4", collation):
            raise RuntimeError("JAX membership receipt string foreign-key collations are incompatible")
    return collation


async def _membership_receipt_columns(cur) -> tuple[tuple[object, ...], ...]:
    collation = await _membership_receipt_collation(cur)
    return tuple(
        tuple(collation if value == "utf8mb4_unicode_ci" else value for value in column)
        for column in _MEMBERSHIP_RECEIPT_COLUMNS
    )

_PROFILE_REVOCATION_COLUMNS = (
    ("event_id", "bigint", "bigint(20) unsigned", "NO", None, None, "auto_increment", None),
    ("actor_user_id", "int", "int(11)", "NO", None, None, "", None),
    ("target_user_id", "int", "int(11)", "NO", None, None, "", None),
    ("action", "varchar", "varchar(32)", "NO", "utf8mb4", "utf8mb4_uca1400_ai_ci", "", None),
    ("version_before", "bigint", "bigint(20)", "NO", None, None, "", None),
    ("version_after", "bigint", "bigint(20)", "NO", None, None, "", None),
    ("reason_code", "varchar", "varchar(64)", "NO", "utf8mb4", "utf8mb4_uca1400_ai_ci", "", None),
    ("authorization_event_id", "bigint", "bigint(20) unsigned", "NO", None, None, "", None),
    ("occurred_at", "datetime", "datetime(6)", "NO", None, None, "", None),
)

_MEMBERSHIP_RECEIPT_FOREIGN_KEYS = {
    "fk_office_membership_receipt_jax_request": (("namespace", "idempotency_key"), "jax_namespace_membership_mutation_request", ("namespace", "idempotency_key"), "RESTRICT", "RESTRICT"),
    "fk_office_membership_receipt_jax_membership": (("jax_membership_id",), "jax_project_membership", ("membership_id",), "RESTRICT", "RESTRICT"),
    "fk_office_membership_receipt_jax_event": (("jax_event_id",), "jax_project_membership_event", ("event_id",), "RESTRICT", "RESTRICT"),
    "fk_office_membership_receipt_actor": (("actor_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
    "fk_office_membership_receipt_target": (("target_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
    "fk_office_membership_receipt_scope": (("project_id", "target_tenant_id"), "jax_project_scope", ("project_id", "tenant_id"), "RESTRICT", "RESTRICT"),
    "fk_office_membership_receipt_grant": (("project_id", "target_user_id"), "office_project_grant", ("project_id", "user_id"), "RESTRICT", "RESTRICT"),
    "fk_office_membership_receipt_auth": (("authorization_event_id",), "office_authorization_event", ("event_id",), "RESTRICT", "RESTRICT"),
    "fk_office_membership_receipt_relation": (("external_relation_id", "project_id", "target_user_id", "recipient_group_id"), "office_external_relation", ("relation_id", "project_id", "user_id", "recipient_group_id"), "RESTRICT", "RESTRICT"),
}

_PROFILE_REVOCATION_FOREIGN_KEYS = {
    "fk_office_profile_revocation_actor": (("actor_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
    "fk_office_profile_revocation_target": (("target_user_id",), "office_account_profile", ("user_id",), "RESTRICT", "RESTRICT"),
    "fk_office_profile_revocation_auth": (("authorization_event_id",), "office_authorization_event", ("event_id",), "RESTRICT", "RESTRICT"),
}

_MEMBERSHIP_RECEIPT_INDEXES = {
    "PRIMARY": (("namespace", "idempotency_key"), True),
    "uq_office_membership_receipt_jax_event": (("jax_event_id",), True),
    "idx_office_membership_receipt_jax_request": (("namespace", "idempotency_key"), False),
}
_PROFILE_REVOCATION_INDEXES = {
    "PRIMARY": (("event_id",), True),
    "idx_office_profile_revocation_target": (("target_user_id", "occurred_at", "event_id"), False),
}
_MEMBERSHIP_RECEIPT_CHECK_DIGESTS = {
    "chk_office_membership_receipt_execution": "196669adfa506ecad362d17e6d3e9253b86588f0fd91d3487159d3f43ff503fc",
    "chk_office_membership_receipt_expected_version": "422f5efc35ffd50753f031031a0d082292983c677e76c19c5ab2577d992df14a",
    "chk_office_membership_receipt_external": "7e6d7f900144fafa7507c4e620ea43e6c794174228d418957f11f65028994915",
    "chk_office_membership_receipt_grant": "8fc194e5784772aef4087925fbe267d7e700e04fb4796502374bcf9b0c144785",
    "chk_office_membership_receipt_operation": "6e851cd6199ee07d619df09ac08f88ef3ce76e93ee414298fd5595bc5222f993",
}
_PROFILE_REVOCATION_CHECK_DIGESTS = {
    "chk_office_profile_revocation_shape": "f769f02954fa70ced3504de2e004fc64e0908b2286a053962efa99bc2859777a",
}

_INVITATION_ISSUANCE_TRIGGERS = {
    "no_update_office_invitation_issuance": (
        "office_invitation_issuance", "BEFORE", "UPDATE", "ROW",
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_issuance append-only'",
    ),
    "no_delete_office_invitation_issuance": (
        "office_invitation_issuance", "BEFORE", "DELETE", "ROW",
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_issuance append-only'",
    ),
}
_INVITATION_EVENT_TRIGGERS = {
    "no_update_office_invitation_event": (
        "office_invitation_event", "BEFORE", "UPDATE", "ROW",
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_event append-only'",
    ),
    "no_delete_office_invitation_event": (
        "office_invitation_event", "BEFORE", "DELETE", "ROW",
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_event append-only'",
    ),
    "guard_office_invitation_event_insert": (
        "office_invitation_event", "BEFORE", "INSERT", "ROW",
        next(statement for statement in OFFICE_INVITATION_DDL if statement.startswith(
            "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_event_insert"
        )).split(" FOR EACH ROW ", 1)[1],
    ),
}
_INVITATION_STATE_TRIGGERS = {
    "guard_office_invitation_insert": (
        "office_invitation", "BEFORE", "INSERT", "ROW",
        next(statement for statement in OFFICE_INVITATION_DDL if statement.startswith(
            "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_insert"
        )).split(" FOR EACH ROW ", 1)[1],
    ),
    "guard_office_invitation_update": (
        "office_invitation", "BEFORE", "UPDATE", "ROW",
        next(statement for statement in OFFICE_INVITATION_DDL if statement.startswith(
            "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_update"
        )).split(" FOR EACH ROW ", 1)[1],
    ),
}
_INVITATION_OUTBOX_TRIGGERS = {
    "guard_office_invitation_outbox_insert": (
        "office_invitation_outbox", "BEFORE", "INSERT", "ROW",
        next(statement for statement in OFFICE_INVITATION_DDL if statement.startswith(
            "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_outbox_insert"
        )).split(" FOR EACH ROW ", 1)[1],
    ),
    "guard_office_invitation_outbox_update": (
        "office_invitation_outbox", "BEFORE", "UPDATE", "ROW",
        next(statement for statement in OFFICE_INVITATION_DDL if statement.startswith(
            "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_outbox_update"
        )).split(" FOR EACH ROW ", 1)[1],
    ),
}

_B44_INVITATION_GUARD_DIGESTS = {
    "guard_office_invitation_insert": "ac75e051a9191b3d4dc8b91f245678490c8abde52d3e11a092871be37345beea",
    "guard_office_invitation_update": "392b6ff3b6ec0f08e6cc3b8df48854f2fb063d9e7e071d40f97d3af331bc96cf",
    "guard_office_invitation_event_insert": "8ac242476382ad3de77789f03a84325e5eb7c42768f62f78525cd677445f87d2",
    "guard_office_invitation_outbox_update": "107925d73d6bd9f0854e9e5798b15e41cd8b5592f9075a55e16546ca76b6a214",
}
_3978115_INVITATION_GUARD_DIGESTS = {
    "guard_office_invitation_insert": "ac75e051a9191b3d4dc8b91f245678490c8abde52d3e11a092871be37345beea",
    "guard_office_invitation_update": "d9623e625f96f6bd6c6662b837414de4b3af6cb4a480ba3308126c022a26f3dc",
    "guard_office_invitation_event_insert": "14bb22aab2983ace0caf7ba2e3731f36f11d9e7868b523e22936499b316117b1",
    "guard_office_invitation_outbox_insert": "71f9948829a2480dba24787de96df65210c59243c871e97933178efdc328caee",
    "guard_office_invitation_outbox_update": "e3055bbada2bb011d4ff9e78cbe6057d7e47640eada0660307657c5a614eaf9c",
}
_JAX_MEMORY_TEST_INVITATION_GUARD_DIGESTS = {
    # Measured from the private disposable template on 2026-10-22.  This is
    # an installed predecessor, not a heuristic: every body is matched by
    # SHA-256 before it can be replaced and existing evidence is untouched.
    "guard_office_invitation_insert": "ac75e051a9191b3d4dc8b91f245678490c8abde52d3e11a092871be37345beea",
    "guard_office_invitation_update": "a8055bbf119acf7ca79766e897bf637d8e96fc349be0e13891bf85c3fc57cdce",
    "guard_office_invitation_event_insert": "32590cdd9a6ad3a6231309b0171ebe996c288abab878a10c39958f4b8e0fd213",
    "guard_office_invitation_outbox_insert": "71f9948829a2480dba24787de96df65210c59243c871e97933178efdc328caee",
    "guard_office_invitation_outbox_update": "98bc45fc6de21b242b66d35bd7474cab49e02ff786f8c3b8a48fd996d5767752",
}

_INVITATION_FOREIGN_KEYS = {
    "office_invitation_issuance": {
        "fk_office_invitation_issuance_scope": (("project_id", "tenant_id"), "jax_project_scope", ("project_id", "tenant_id"), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_issuance_group_project": (("recipient_group_id", "project_id"), "office_recipient_group", ("recipient_group_id", "project_id"), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_issuance_issuer": (("issued_by_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_issuance_target": (("target_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_issuance_profile": (("issued_by_user_id",), "office_account_profile", ("user_id",), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_issuance_basis": (("project_id", "issued_by_user_id"), "office_project_grant", ("project_id", "user_id"), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_issuance_proof": (("issuer_proof_event_id",), "office_mfa_security_event", ("event_id",), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_issuance_authorization": (("issuance_authorization_event_id",), "office_authorization_event", ("event_id",), "RESTRICT", "RESTRICT"),
    },
    "office_invitation": {
        "fk_office_invitation_issuance": (("issuance_id",), "office_invitation_issuance", ("issuance_id",), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_accepted_user": (("accepted_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_target": (("target_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_project": (("project_id",), "projects", ("id",), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_issuance_binding": (("issuance_id", "project_id", "email_normalized", "target_user_id"), "office_invitation_issuance", ("issuance_id", "project_id", "email_normalized", "target_user_id"), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_revoker": (("revoked_by_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
    },
    "office_invitation_event": {
        "fk_office_invitation_event_invitation": (("invitation_id",), "office_invitation", ("invitation_id",), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_event_actor": (("actor_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_event_causation": (("causation_event_id",), "office_invitation_event", ("event_id",), "RESTRICT", "RESTRICT"),
        "fk_office_invitation_event_authorization": (("authorization_event_id",), "office_authorization_event", ("event_id",), "RESTRICT", "RESTRICT"),
    },
    "office_invitation_outbox": {
        "fk_office_invitation_outbox_invitation": (("invitation_id",), "office_invitation", ("invitation_id",), "RESTRICT", "RESTRICT"),
    },
}

_INVITATION_INDEXES = {
    "office_invitation_issuance": {
        "PRIMARY": (("issuance_id",), True),
        "uq_office_invitation_issuance_token": (("token_hash",), True),
        "uq_office_invitation_issuance_binding": (("issuance_id", "project_id", "email_normalized", "target_user_id"), True),
        "idx_office_invitation_issuance_scope": (("project_id", "tenant_id", "expires_at", "issuance_id"), False),
        "idx_office_invitation_issuance_expiry": (("expires_at", "issuance_id"), False),
        "fk_office_invitation_issuance_group_project": (("recipient_group_id", "project_id"), False),
        "fk_office_invitation_issuance_target": (("target_user_id",), False),
        "fk_office_invitation_issuance_profile": (("issued_by_user_id",), False),
        "fk_office_invitation_issuance_basis": (("project_id", "issued_by_user_id"), False),
        "fk_office_invitation_issuance_proof": (("issuer_proof_event_id",), False),
        "fk_office_invitation_issuance_authorization": (("issuance_authorization_event_id",), False),
    },
    "office_invitation": {
        "PRIMARY": (("invitation_id",), True),
        "uq_office_invitation_issuance": (("issuance_id",), True),
        "uq_office_invitation_pending_subject": (("project_id", "pending_email"), True),
        "idx_office_invitation_expiry": (("status", "issuance_id", "invitation_id"), False),
        "idx_office_invitation_status_expiry": (("status", "expires_at", "invitation_id"), False),
        "idx_office_invitation_project_live": (("project_id", "status", "expires_at", "invitation_id"), False),
        "fk_office_invitation_target": (("target_user_id",), False),
        "fk_office_invitation_issuance_binding": (("issuance_id", "project_id", "email_normalized", "target_user_id"), False),
        "fk_office_invitation_accepted_user": (("accepted_user_id",), False),
        "fk_office_invitation_revoker": (("revoked_by_user_id",), False),
    },
    "office_invitation_event": {
        "PRIMARY": (("event_id",), True),
        "uq_office_invitation_event_kind": (("invitation_id", "event_kind"), True),
        "idx_office_invitation_event_correlation": (("invitation_id", "correlation_id", "event_id"), False),
        "idx_office_invitation_event_invitation": (("invitation_id", "occurred_at", "event_id"), False),
        "fk_office_invitation_event_actor": (("actor_user_id",), False),
        "fk_office_invitation_event_causation": (("causation_event_id",), False),
        "idx_office_invitation_event_authorization": (("authorization_event_id",), False),
    },
    "office_invitation_outbox": {
        "PRIMARY": (("outbox_id",), True),
        "uq_office_invitation_outbox_invitation": (("invitation_id",), True),
        "idx_office_invitation_outbox_claim": (("state", "created_at", "outbox_id"), False),
        "idx_office_invitation_outbox_stale_claim": (("state", "claimed_at", "outbox_id"), False),
    },
}
_INVITATION_CHECK_DIGESTS = {
    "chk_office_invitation_issuance_shape": "ce60d2af72bd71a12c905bdc1700eb602b4574e81de8bf523018e2a1e7bb9b71",
    "chk_office_invitation_shape": "c07efa419383b9f00f9bc5b0a8884aac877de5f6f5a5babd8e16cd347522abb6",
    "chk_office_invitation_event_shape": "64103b1ffd1a6e0adcdface79c7782f4b37b38a3428c4a40d0a004f7a478ed78",
    "chk_office_invitation_outbox_shape": "4eb25681d2ea2ab65fe509ea0ed988726a367edad5c214a71136c711192b40e3",
}
_INVITATION_ISSUANCE_V1_CHECK_DIGEST = "efc34a8e7ed42eff7b2d2e48656dd486ceaac49500a9d1516905c6e876ef278a"
_INVITATION_COLUMNS = {
    "office_invitation_issuance": (("issuance_id","char","char(36)","NO","ascii","ascii_bin","",None),("token_hash","binary","binary(32)","NO",None,None,"",None),("email_normalized","varchar","varchar(320)","NO","ascii","ascii_bin","",None),("tenant_id","int","int(11)","NO",None,None,"",None),("project_id","int","int(11)","NO",None,None,"",None),("recipient_group_id","bigint","bigint(20) unsigned","YES",None,None,"","NULL"),("target_user_id","int","int(11)","YES",None,None,"","NULL"),("target_account_class","varchar","varchar(24)","NO","utf8mb4","utf8mb4_uca1400_ai_ci","",None),("grant_role","varchar","varchar(24)","NO","utf8mb4","utf8mb4_uca1400_ai_ci","",None),("audience","varchar","varchar(24)","NO","utf8mb4","utf8mb4_uca1400_ai_ci","",None),("issued_by_user_id","int","int(11)","NO",None,None,"",None),("issuer_profile_version","bigint","bigint(20)","NO",None,None,"",None),("issuer_grant_version","bigint","bigint(20)","NO",None,None,"",None),("issuer_authorization_kind","varchar","varchar(32)","NO","ascii","ascii_bin","","'MFA_PROOF'"),("issuer_proof_event_id","bigint","bigint(20) unsigned","YES",None,None,"","NULL"),("issuer_mfa_version","bigint","bigint(20)","YES",None,None,"","NULL"),("issuer_mfa_at","datetime","datetime(6)","YES",None,None,"","NULL"),("issuance_authorization_event_id","bigint","bigint(20) unsigned","NO",None,None,"",None),("proof_digest","binary","binary(32)","NO",None,None,"",None),("correlation_id","binary","binary(16)","NO",None,None,"",None),("issued_at","datetime","datetime(6)","NO",None,None,"",None),("expires_at","datetime","datetime(6)","NO",None,None,"",None)),
    "office_invitation": (("invitation_id","char","char(36)","NO","ascii","ascii_bin","",None),("issuance_id","char","char(36)","NO","ascii","ascii_bin","",None),("expires_at","datetime","datetime(6)","NO",None,None,"",None),("status","varchar","varchar(16)","NO","utf8mb4","utf8mb4_uca1400_ai_ci","","'ISSUED'"),("version","bigint","bigint(20)","NO",None,None,"","1"),("project_id","int","int(11)","NO",None,None,"",None),("email_normalized","varchar","varchar(320)","NO","ascii","ascii_bin","",None),("target_user_id","int","int(11)","YES",None,None,"","NULL"),("pending_email","varchar","varchar(320)","YES","ascii","ascii_bin","STORED GENERATED","NULL"),("accepted_user_id","int","int(11)","YES",None,None,"","NULL"),("accepted_at","datetime","datetime(6)","YES",None,None,"","NULL"),("revoked_by_user_id","int","int(11)","YES",None,None,"","NULL"),("revoked_at","datetime","datetime(6)","YES",None,None,"","NULL")),
    "office_invitation_event": (("event_id","bigint","bigint(20) unsigned","NO",None,None,"auto_increment",None),("invitation_id","char","char(36)","NO","ascii","ascii_bin","",None),("event_kind","varchar","varchar(32)","NO","utf8mb4","utf8mb4_uca1400_ai_ci","",None),("causation_event_id","bigint","bigint(20) unsigned","YES",None,None,"","NULL"),("actor_user_id","int","int(11)","YES",None,None,"","NULL"),("bearer_token_hash","binary","binary(32)","YES",None,None,"","NULL"),("observed_outbox_state","varchar","varchar(16)","YES","utf8mb4","utf8mb4_uca1400_ai_ci","","NULL"),("authorization_event_id","bigint","bigint(20) unsigned","YES",None,None,"","NULL"),("invocation_digest","binary","binary(32)","YES",None,None,"","NULL"),("correlation_id","binary","binary(16)","NO",None,None,"",None),("occurred_at","datetime","datetime(6)","NO",None,None,"",None)),
    "office_invitation_outbox": (("outbox_id","char","char(36)","NO","ascii","ascii_bin","",None),("invitation_id","char","char(36)","NO","ascii","ascii_bin","",None),("state","varchar","varchar(16)","NO","utf8mb4","utf8mb4_uca1400_ai_ci","","'QUEUED'"),("attempt_id","char","char(36)","YES","ascii","ascii_bin","","NULL"),("attempt_count","int","int(11)","NO",None,None,"","0"),("cancel_requested","tinyint","tinyint(1)","NO",None,None,"","0"),("payload_ciphertext","mediumblob","mediumblob","YES",None,None,"","NULL"),("message_id","varchar","varchar(320)","YES","ascii","ascii_bin","","NULL"),("result_code","varchar","varchar(64)","YES","ascii","ascii_bin","","NULL"),("claimed_at","datetime","datetime(6)","YES",None,None,"","NULL"),("finished_at","datetime","datetime(6)","YES",None,None,"","NULL"),("created_at","datetime","datetime(6)","NO",None,None,"",None),("updated_at","datetime","datetime(6)","NO",None,None,"",None)),
}

# The request receipt is a companion to the invitation lifecycle, but it is
# deliberately not folded into issuance evidence: retries are request
# transport semantics and must not alter the proof that authorized issuance.
_INVITATION_MUTATION_RECEIPT_COLUMNS = (
    ("idempotency_key","char","char(36)","NO","ascii","ascii_bin","",None),
    ("request_digest","binary","binary(32)","NO",None,None,"",None),
    ("operation","varchar","varchar(16)","NO","utf8mb4","utf8mb4_uca1400_ai_ci","",None),
    ("actor_user_id","int","int(11)","NO",None,None,"",None),("project_id","int","int(11)","NO",None,None,"",None),
    ("invitation_id","char","char(36)","NO","ascii","ascii_bin","",None),
    ("expected_version","bigint","bigint(20)","NO",None,None,"",None),("response_version","bigint","bigint(20)","NO",None,None,"",None),
    ("result_event_id","bigint","bigint(20) unsigned","NO",None,None,"",None),("authorization_event_id","bigint","bigint(20) unsigned","NO",None,None,"",None),
    ("response_contract","varchar","varchar(48)","NO","utf8mb4","utf8mb4_uca1400_ai_ci","",None),
    ("expires_at","datetime","datetime(6)","YES",None,None,"","NULL"),("created_at","datetime","datetime(6)","NO",None,None,"",None),
)
_INVITATION_MUTATION_RECEIPT_INDEXES = {
    "PRIMARY": (("idempotency_key",), True),
    "uq_office_invitation_mutation_receipt_invitation_operation": (("invitation_id", "operation"), True),
    "idx_office_invitation_mutation_receipt_actor": (("actor_user_id", "created_at", "idempotency_key"), False),
    "fk_office_invitation_mutation_receipt_project": (("project_id",), False),
    "fk_office_invitation_mutation_receipt_event": (("result_event_id",), False),
    "fk_office_invitation_mutation_receipt_authorization": (("authorization_event_id",), False),
}
_INVITATION_MUTATION_RECEIPT_FOREIGN_KEYS = {
    "fk_office_invitation_mutation_receipt_actor": (("actor_user_id",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"),
    "fk_office_invitation_mutation_receipt_project": (("project_id",), "projects", ("id",), "RESTRICT", "RESTRICT"),
    "fk_office_invitation_mutation_receipt_invitation": (("invitation_id",), "office_invitation", ("invitation_id",), "RESTRICT", "RESTRICT"),
    "fk_office_invitation_mutation_receipt_event": (("result_event_id",), "office_invitation_event", ("event_id",), "RESTRICT", "RESTRICT"),
    "fk_office_invitation_mutation_receipt_authorization": (("authorization_event_id",), "office_authorization_event", ("event_id",), "RESTRICT", "RESTRICT"),
}
_INVITATION_MUTATION_RECEIPT_TRIGGERS = {
    "no_update_office_invitation_mutation_receipt": (
        "office_invitation_mutation_receipt", "BEFORE", "UPDATE", "ROW",
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_mutation_receipt append-only'"),
    "no_delete_office_invitation_mutation_receipt": (
        "office_invitation_mutation_receipt", "BEFORE", "DELETE", "ROW",
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_invitation_mutation_receipt append-only'"),
    "guard_office_invitation_mutation_receipt_insert": (
        "office_invitation_mutation_receipt", "BEFORE", "INSERT", "ROW",
        next(statement for statement in OFFICE_INVITATION_DDL if statement.startswith(
            "CREATE TRIGGER IF NOT EXISTS guard_office_invitation_mutation_receipt_insert"
        )).split(" FOR EACH ROW ", 1)[1]),
}
_INVITATION_MUTATION_RECEIPT_CHECK_DIGEST = "36c08ea87356a58a85f8bdebed430f3b2aad6e93c1d3c6a292c9ed786aabf22c"
_INVITATION_MUTATION_RECEIPT_GUARD_PREDECESSOR_DIGESTS = frozenset((
    # 2e86051: receipt lacked the revoke authorization attestation.
    "8c4ec368ac456a2aef4ef0eea6263f130fac8065894adb36bc6d641fa6416e51",
    # 30650e6 through 675d364: authorization was checked but the trigger was
    # not replaced after that amendment, so it lacks event/digest/expiry ties.
    "6e12d391133eaea141279c4e9446ab38c7678e831eeac1369d915ddbc2e8a745",
))


async def _assert_columns(cur, *, table: str, expected: tuple[tuple[object, ...], ...]) -> None:
    await cur.execute(
        "SELECT COLUMN_NAME,DATA_TYPE,COLUMN_TYPE,IS_NULLABLE,CHARACTER_SET_NAME,COLLATION_NAME,EXTRA,COLUMN_DEFAULT "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
        (table,),
    )
    if tuple(await cur.fetchall()) != expected:
        raise RuntimeError(f"Office {table} column metadata is invalid")


async def _assert_foreign_keys(cur, *, table: str, expected: dict[str, tuple[object, ...]]) -> None:
    await cur.execute("SELECT DATABASE()")
    row = await cur.fetchone()
    schema = None if row is None else str(row[0])
    if not schema:
        raise RuntimeError(f"Office {table} schema is unavailable")
    for name, shape in expected.items():
        await cur.execute(
            "SELECT k.COLUMN_NAME,k.REFERENCED_TABLE_SCHEMA,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,"
            "r.UPDATE_RULE,r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k "
            "JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA "
            "AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.TABLE_NAME=k.TABLE_NAME "
            "WHERE k.CONSTRAINT_SCHEMA=DATABASE() AND k.TABLE_NAME=%s AND k.CONSTRAINT_NAME=%s "
            "ORDER BY k.ORDINAL_POSITION", (table, name),
        )
        rows = await cur.fetchall()
        actual = (tuple(item[0] for item in rows), rows[0][2], tuple(item[3] for item in rows), rows[0][4], rows[0][5]) if rows and rows[0][1] == schema else None
        if actual != shape:
            raise RuntimeError(f"Office {table} foreign key {name} is invalid")


async def _assert_indexes(cur, *, table: str, expected: dict[str, tuple[tuple[str, ...], bool]]) -> None:
    for name, (columns, unique) in expected.items():
        if not await _index_has_exact_shape(cur, table, name, columns, unique=unique):
            raise RuntimeError(f"Office {table} index {name} is invalid")


_AGENT_RUNTIME_COLUMNS = {
    "office_agent_runtime_config": ("config_id", "config_version", "max_output_tokens", "max_input_bytes", "max_instructions_bytes", "max_response_bytes", "window_seconds", "actor_runs_per_window", "project_runs_per_window", "actor_concurrency", "project_concurrency", "timeout_seconds", "stale_seconds", "updated_at"),
    "office_engine": ("engine_key", "display_name_key", "model_ref", "credential_id", "transport", "allowed_data_classifications", "binding_fingerprint", "enabled", "activation_epoch", "version", "created_at", "updated_at"),
    "office_agent": ("agent_id", "project_id", "agent_key", "display_name", "instructions", "data_classification", "status", "version", "created_by", "created_at", "updated_at"),
    "office_agent_binding": ("binding_id", "agent_id", "engine_key", "binding_version", "active", "active_agent_id", "bound_by", "bound_at"),
    "office_agent_run": ("run_id", "agent_id", "project_id", "requested_by", "authorization_event_id", "agent_version", "binding_id", "binding_version", "idempotency_key", "input_sha256", "instructions_sha256", "dispatch_snapshot_sha256", "config_version", "engine_key", "engine_version", "engine_binding_fingerprint", "model_ref", "model_id", "credential_id", "credential_fingerprint", "provider_id", "auth_type", "auth_transport", "status", "output_text", "accepted_at", "deadline_at", "fence", "completed_at"),
}
# Exact MariaDB 12.3 information_schema.COLUMNS rows for the private-agent
# catalogue.  Names and ordinal position alone do not prevent a homonymous
# table from silently widening a field, changing a default, or acquiring a
# different text collation.  These values are the normalized forms emitted by
# the canonical DDL above (JSON is reported by MariaDB as longtext).
_AGENT_RUNTIME_COLUMN_METADATA = {
    "office_engine": (
        ("engine_key", "varchar(64)", "NO", None, "", "ascii", "ascii_bin"),
        ("display_name_key", "varchar(128)", "NO", None, "", "utf8mb4", "utf8mb4_bin"),
        ("model_ref", "int(11)", "NO", None, "", None, None),
        ("credential_id", "int(11)", "NO", None, "", None, None),
        ("transport", "enum('http_openai_compat')", "NO", None, "", "utf8mb4", "utf8mb4_uca1400_ai_ci"),
        ("allowed_data_classifications", "longtext", "NO", None, "", "utf8mb4", "utf8mb4_bin"),
        ("binding_fingerprint", "char(64)", "NO", None, "", "ascii", "ascii_bin"),
        ("enabled", "tinyint(1)", "NO", "0", "", None, None),
        ("activation_epoch", "bigint(20) unsigned", "NO", "0", "", None, None),
        ("version", "bigint(20)", "NO", "1", "", None, None),
        ("created_at", "datetime(6)", "NO", "current_timestamp(6)", "", None, None),
        ("updated_at", "datetime(6)", "NO", "current_timestamp(6)", "on update current_timestamp(6)", None, None),
    ),
    "office_agent_runtime_config": (
        ("config_id", "tinyint(3) unsigned", "NO", None, "", None, None),
        ("config_version", "smallint(5) unsigned", "NO", "1", "", None, None),
        ("max_output_tokens", "smallint(5) unsigned", "NO", "2048", "", None, None),
        ("max_input_bytes", "int(10) unsigned", "NO", "32768", "", None, None),
        ("max_instructions_bytes", "int(10) unsigned", "NO", "32768", "", None, None),
        ("max_response_bytes", "int(10) unsigned", "NO", "1048576", "", None, None),
        ("window_seconds", "int(10) unsigned", "NO", "3600", "", None, None),
        ("actor_runs_per_window", "smallint(5) unsigned", "NO", "30", "", None, None),
        ("project_runs_per_window", "smallint(5) unsigned", "NO", "300", "", None, None),
        ("actor_concurrency", "tinyint(3) unsigned", "NO", "2", "", None, None),
        ("project_concurrency", "tinyint(3) unsigned", "NO", "8", "", None, None),
        ("timeout_seconds", "tinyint(3) unsigned", "NO", "45", "", None, None),
        ("stale_seconds", "tinyint(3) unsigned", "NO", "60", "", None, None),
        ("updated_at", "datetime(6)", "NO", "current_timestamp(6)", "on update current_timestamp(6)", None, None),
    ),
    "office_agent": (
        ("agent_id", "bigint(20) unsigned", "NO", None, "auto_increment", None, None),
        ("project_id", "int(11)", "NO", None, "", None, None),
        ("agent_key", "varchar(64)", "NO", None, "", "ascii", "ascii_bin"),
        ("display_name", "varchar(128)", "NO", None, "", "utf8mb4", "utf8mb4_uca1400_ai_ci"),
        ("instructions", "text", "NO", None, "", "utf8mb4", "utf8mb4_uca1400_ai_ci"),
        ("data_classification", "varchar(64)", "NO", None, "", "ascii", "ascii_bin"),
        ("status", "enum('ACTIVE','DISABLED')", "NO", "'ACTIVE'", "", "utf8mb4", "utf8mb4_uca1400_ai_ci"),
        ("version", "bigint(20) unsigned", "NO", "1", "", None, None),
        ("created_by", "int(11)", "NO", None, "", None, None),
        ("created_at", "datetime(6)", "NO", "current_timestamp(6)", "", None, None),
        ("updated_at", "datetime(6)", "NO", "current_timestamp(6)", "on update current_timestamp(6)", None, None),
    ),
    "office_agent_binding": (
        ("binding_id", "bigint(20) unsigned", "NO", None, "auto_increment", None, None),
        ("agent_id", "bigint(20) unsigned", "NO", None, "", None, None),
        ("engine_key", "varchar(64)", "NO", None, "", "ascii", "ascii_bin"),
        ("binding_version", "bigint(20) unsigned", "NO", None, "", None, None),
        ("active", "tinyint(1)", "NO", "1", "", None, None),
        ("active_agent_id", "bigint(20) unsigned", "YES", "NULL", "STORED GENERATED", None, None),
        ("bound_by", "int(11)", "NO", None, "", None, None),
        ("bound_at", "datetime(6)", "NO", "current_timestamp(6)", "", None, None),
    ),
    "office_agent_run": (
        ("run_id", "char(36)", "NO", None, "", "ascii", "ascii_bin"),
        ("agent_id", "bigint(20) unsigned", "NO", None, "", None, None),
        ("project_id", "int(11)", "NO", None, "", None, None),
        ("requested_by", "int(11)", "NO", None, "", None, None),
        ("authorization_event_id", "bigint(20) unsigned", "NO", None, "", None, None),
        ("agent_version", "bigint(20) unsigned", "NO", None, "", None, None),
        ("binding_id", "bigint(20) unsigned", "NO", None, "", None, None),
        ("binding_version", "bigint(20) unsigned", "NO", None, "", None, None),
        ("idempotency_key", "varchar(128)", "NO", None, "", "ascii", "ascii_bin"),
        ("input_sha256", "binary(32)", "NO", None, "", None, None),
        ("instructions_sha256", "binary(32)", "NO", None, "", None, None),
        ("dispatch_snapshot_sha256", "binary(32)", "NO", None, "", None, None),
        ("config_version", "smallint(5) unsigned", "NO", None, "", None, None),
        ("engine_key", "varchar(64)", "NO", None, "", "ascii", "ascii_bin"),
        ("engine_version", "bigint(20)", "NO", None, "", None, None),
        ("engine_binding_fingerprint", "char(64)", "NO", None, "", "ascii", "ascii_bin"),
        ("model_ref", "int(11)", "NO", None, "", None, None),
        ("model_id", "varchar(255)", "NO", None, "", "utf8mb4", "utf8mb4_uca1400_ai_ci"),
        ("credential_id", "int(11)", "NO", None, "", None, None),
        ("credential_fingerprint", "char(64)", "NO", None, "", "ascii", "ascii_bin"),
        ("provider_id", "varchar(128)", "NO", None, "", "ascii", "ascii_bin"),
        ("auth_type", "varchar(32)", "NO", None, "", "ascii", "ascii_bin"),
        ("auth_transport", "varchar(32)", "NO", None, "", "ascii", "ascii_bin"),
        ("status", "enum('DISPATCHING','SUCCEEDED','UNKNOWN')", "NO", None, "", "utf8mb4", "utf8mb4_uca1400_ai_ci"),
        ("output_text", "mediumtext", "YES", "NULL", "", "utf8mb4", "utf8mb4_uca1400_ai_ci"),
        ("accepted_at", "datetime(6)", "NO", "current_timestamp(6)", "", None, None),
        ("deadline_at", "datetime(6)", "NO", None, "", None, None),
        ("fence", "bigint(20) unsigned", "NO", None, "", None, None),
        ("completed_at", "datetime(6)", "YES", "NULL", "", None, None),
    ),
}
_AGENT_RUNTIME_INDEXES = {
    "office_agent_runtime_config": {"PRIMARY": (("config_id",), True)},
    "office_engine": {"PRIMARY": (("engine_key",), True), "idx_office_engine_enabled_key": (("enabled", "engine_key"), False)},
    "office_agent": {"PRIMARY": (("agent_id",), True), "uq_office_agent_project_key": (("project_id", "agent_key"), True), "idx_office_agent_project": (("project_id", "status", "agent_id"), False), "idx_office_agent_project_list": (("project_id", "agent_id"), False)},
    "office_agent_binding": {"PRIMARY": (("binding_id",), True), "uq_office_agent_binding_version": (("agent_id", "binding_version"), True), "uq_office_agent_binding_active_agent": (("active_agent_id",), True), "idx_office_agent_binding_active": (("agent_id", "active", "binding_version"), False)},
    "office_agent_run": {"PRIMARY": (("run_id",), True), "uq_office_agent_run_idempotency": (("requested_by", "agent_id", "idempotency_key"), True), "idx_office_agent_run_actor_window": (("requested_by", "accepted_at", "status", "deadline_at"), False), "idx_office_agent_run_project_window": (("project_id", "accepted_at", "status", "deadline_at"), False), "idx_office_agent_run_actor_concurrency": (("requested_by", "status", "deadline_at"), False), "idx_office_agent_run_project_concurrency": (("project_id", "status", "deadline_at"), False)},
}
_AGENT_RUNTIME_FKS = {
    "office_engine": {"fk_office_engine_model": (("model_ref",), "model", ("id",), "RESTRICT", "RESTRICT"), "fk_office_engine_credential": (("credential_id",), "credential", ("id",), "RESTRICT", "RESTRICT")},
    "office_agent": {"fk_office_agent_project": (("project_id",), "projects", ("id",), "RESTRICT", "RESTRICT"), "fk_office_agent_created_by": (("created_by",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT")},
    "office_agent_binding": {"fk_office_agent_binding_agent": (("agent_id",), "office_agent", ("agent_id",), "RESTRICT", "RESTRICT"), "fk_office_agent_binding_engine": (("engine_key",), "office_engine", ("engine_key",), "RESTRICT", "RESTRICT"), "fk_office_agent_binding_by": (("bound_by",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT")},
    "office_agent_run": {"fk_office_agent_run_agent": (("agent_id",), "office_agent", ("agent_id",), "RESTRICT", "RESTRICT"), "fk_office_agent_run_project": (("project_id",), "projects", ("id",), "RESTRICT", "RESTRICT"), "fk_office_agent_run_requested_by": (("requested_by",), "jax_users", ("user_id",), "RESTRICT", "RESTRICT"), "fk_office_agent_run_authorization": (("authorization_event_id",), "office_authorization_event", ("event_id",), "RESTRICT", "RESTRICT"), "fk_office_agent_run_binding": (("binding_id",), "office_agent_binding", ("binding_id",), "RESTRICT", "RESTRICT"), "fk_office_agent_run_engine": (("engine_key",), "office_engine", ("engine_key",), "RESTRICT", "RESTRICT"), "fk_office_agent_run_model": (("model_ref",), "model", ("id",), "RESTRICT", "RESTRICT"), "fk_office_agent_run_credential": (("credential_id",), "credential", ("id",), "RESTRICT", "RESTRICT")},
}


_AGENT_RUNTIME_CHECK_CLAUSES = {
    # MariaDB 12.3 materializes JSON as longtext plus this automatic check.
    # The clauses below are the exact information_schema form of the reviewed
    # DDL, not a substring or a semantic approximation.
    "allowed_data_classifications": "json_valid(`allowed_data_classifications`)",
    "chk_office_engine_classifications": "json_type(`allowed_data_classifications`) = 'ARRAY' and json_length(`allowed_data_classifications`) > 0",
    "chk_office_engine_display_name_key": "`display_name_key` <> ''",
    "chk_office_engine_epoch": "`activation_epoch` >= 0",
    "chk_office_engine_fingerprint": "`binding_fingerprint` regexp '^[0-9a-f]{64}$'",
    "chk_office_engine_key": "`engine_key` <> ''",
    "chk_office_engine_version": "`version` >= 1",
    "chk_office_agent_runtime_config_bounds": "`max_output_tokens` between 1 and 2048 and `max_input_bytes` between 1 and 32768 and `max_instructions_bytes` between 1 and 32768 and `max_response_bytes` between 1 and 1048576 and `window_seconds` between 60 and 3600 and `actor_runs_per_window` between 1 and 30 and `project_runs_per_window` between 1 and 300 and `actor_concurrency` between 1 and 2 and `project_concurrency` between 1 and 8 and `timeout_seconds` between 1 and 45 and `stale_seconds` between 6 and 60 and `stale_seconds` >= `timeout_seconds` + 5",
    "chk_office_agent_runtime_config_singleton": "`config_id` = 1",
    "chk_office_agent_runtime_config_version": "`config_version` >= 1",
    "chk_office_agent_classification": "`data_classification` regexp '^[A-Z][A-Z0-9_]{0,63}$'",
    "chk_office_agent_key": "`agent_key` <> ''",
    "chk_office_agent_name": "`display_name` <> ''",
    "chk_office_agent_version": "`version` >= 1",
    "chk_office_agent_binding_version": "`binding_version` >= 1",
    "chk_office_agent_run_key": "`idempotency_key` <> ''",
    "chk_office_agent_run_snapshot": "`agent_version` >= 1 and `binding_version` >= 1 and `config_version` >= 1 and `engine_version` >= 1 and `engine_binding_fingerprint` regexp '^[0-9a-f]{64}$' and `credential_fingerprint` regexp '^[0-9a-f]{64}$' and `provider_id` <> '' and `auth_type` <> '' and `auth_transport` <> '' and `fence` >= 1 and `deadline_at` >= `accepted_at`",
    "chk_office_agent_run_output": "`status` = 'SUCCEEDED' and `output_text` is not null and `completed_at` is not null or `status` = 'DISPATCHING' and `output_text` is null and `completed_at` is null or `status` = 'UNKNOWN' and `output_text` is null and `completed_at` is not null",
}


def _agent_runtime_check_clauses() -> dict[str, str]:
    return _AGENT_RUNTIME_CHECK_CLAUSES


def _agent_runtime_trigger_bodies() -> dict[str, str]:
    result = {}
    for statement in (*OFFICE_AGENT_RUNTIME_DDL, *OFFICE_AGENT_DDL):
        match = re.match(r"CREATE TRIGGER IF NOT EXISTS ([a-z0-9_]+) .*? FOR EACH ROW (.*)", statement)
        if match is not None:
            result[match.group(1)] = match.group(2)
    return result


def _agent_runtime_expected_triggers() -> dict[str, tuple[str, str, str, str, str]]:
    """Return the full trigger identity, including the protected table."""
    result = {}
    for statement in (*OFFICE_AGENT_RUNTIME_DDL, *OFFICE_AGENT_DDL):
        match = re.match(
            r"CREATE TRIGGER IF NOT EXISTS ([a-z0-9_]+) (BEFORE|AFTER) (INSERT|UPDATE|DELETE) "
            r"ON ([a-z0-9_]+) FOR EACH ROW (.*)",
            statement,
        )
        if match is not None:
            name, timing, event, table, body = match.groups()
            result[name] = (table, timing, event, "ROW", body)
    return result


def _normalize_trigger_body(value: object) -> str:
    return re.sub(r"[\s`]", "", str(value).lower())


def _agent_runtime_expected_indexes(table: str) -> dict[str, tuple[tuple[str, ...], bool]]:
    expected = dict(_AGENT_RUNTIME_INDEXES[table])
    for name, (local, _target, _remote, _update, _delete) in _AGENT_RUNTIME_FKS.get(table, {}).items():
        if not any(columns[:len(local)] == local for columns, _unique in expected.values()):
            expected[name] = (local, False)
    return expected


async def assert_office_agent_runtime_metadata(cur) -> None:
    """Fail closed when a homonymous private-agent table lost its contract."""
    for table, columns in _AGENT_RUNTIME_COLUMNS.items():
        if not await _table_exists(cur, table):
            raise RuntimeError(f"Office agent runtime table {table} is absent")
        await cur.execute(
            "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA,CHARACTER_SET_NAME,COLLATION_NAME "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
            (table,),
        )
        found_columns = tuple(tuple(row) for row in await cur.fetchall())
        if found_columns != _AGENT_RUNTIME_COLUMN_METADATA[table]:
            raise RuntimeError(f"Office agent runtime columns for {table} are invalid")
        expected_indexes = _agent_runtime_expected_indexes(table)
        await _assert_indexes(cur, table=table, expected=expected_indexes)
        await cur.execute("SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
        if {row[0] for row in await cur.fetchall()} != set(expected_indexes):
            raise RuntimeError(f"Office agent runtime indexes for {table} are invalid")
        expected_fks = _AGENT_RUNTIME_FKS.get(table, {})
        await _assert_foreign_keys(cur, table=table, expected=expected_fks)
        await cur.execute("SELECT DISTINCT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL", (table,))
        if {row[0] for row in await cur.fetchall()} != set(expected_fks):
            raise RuntimeError(f"Office agent runtime foreign keys for {table} are invalid")
    await cur.execute(
        "SELECT GENERATION_EXPRESSION FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='office_agent_binding' AND COLUMN_NAME='active_agent_id'"
    )
    if await cur.fetchone() != ("if(`active`,`agent_id`,NULL)",):
        raise RuntimeError("Office agent active binding generation is invalid")
    expected_checks = _agent_runtime_check_clauses()
    await cur.execute("SELECT tc.CONSTRAINT_NAME,cc.CHECK_CLAUSE FROM information_schema.TABLE_CONSTRAINTS tc JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME IN ('office_engine','office_agent','office_agent_binding','office_agent_run','office_agent_runtime_config') AND tc.CONSTRAINT_TYPE='CHECK'")
    found_checks = {str(name): str(clause) for name, clause in await cur.fetchall()}
    if set(found_checks) != set(expected_checks) or any(_normalize_mariadb_check(found_checks[name]) != _normalize_mariadb_check(clause) for name, clause in expected_checks.items()):
        raise RuntimeError("Office agent runtime checks are invalid")
    expected_triggers = _agent_runtime_expected_triggers()
    placeholders = ",".join("%s" for _table in _AGENT_RUNTIME_COLUMNS)
    await cur.execute(
        "SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT "
        "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() "
        f"AND EVENT_OBJECT_TABLE IN ({placeholders})",
        tuple(_AGENT_RUNTIME_COLUMNS),
    )
    found_triggers = {str(name): tuple(row) for name, *row in await cur.fetchall()}
    if set(found_triggers) != set(expected_triggers) or any(
        tuple(found_triggers[name][:4]) != expected_triggers[name][:4]
        or _normalize_trigger_body(found_triggers[name][4]) != _normalize_trigger_body(expected_triggers[name][4])
        for name in expected_triggers
    ):
        raise RuntimeError("Office agent runtime triggers are invalid")


async def _assert_append_only_table_metadata(cur, *, table: str, triggers: dict[str, tuple[str, ...]],
                                             allow_redacted_trigger_body: bool = False,
                                             primary_key: tuple[str, ...] | None = None) -> None:
    """Reject homonymous/no-op append-only guards before readiness is stamped."""
    await cur.execute(
        "SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,"
        "ACTION_ORIENTATION,ACTION_STATEMENT FROM information_schema.TRIGGERS "
        "WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE=%s", (table,))
    found = {row[0]: tuple(row[1:]) for row in await cur.fetchall()}
    if allow_redacted_trigger_body:
        expected = {
            name: (*metadata[:-1], None if found.get(name, (None,))[-1] is None else metadata[-1])
            for name, metadata in triggers.items()
        }
    else:
        expected = triggers
    if found != expected:
        raise RuntimeError(f"Office {table} triggers are missing or invalid")
    await cur.execute(
        "SELECT COLUMN_NAME,NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME='PRIMARY' ORDER BY SEQ_IN_INDEX", (table,))
    expected_primary = (
        tuple((column, 0) for column in primary_key)
        if primary_key is not None
        else (("namespace", 0), ("idempotency_key", 0))
        if table == "office_membership_mutation_receipt" else (("event_id", 0),)
    )
    if tuple(await cur.fetchall()) != expected_primary:
        raise RuntimeError(f"Office {table} primary key is invalid")


async def assert_invitation_runtime_metadata(cur) -> None:
    """Attest the dormant 3C2 physical contract before readiness is stamped."""
    for table, indexes in _INVITATION_INDEXES.items():
        if not await _table_exists(cur, table):
            raise RuntimeError(f"Office {table} is missing")
        await _assert_columns(cur, table=table, expected=_INVITATION_COLUMNS[table])
        await _assert_indexes(cur, table=table, expected=indexes)
        await _assert_foreign_keys(cur, table=table, expected=_INVITATION_FOREIGN_KEYS[table])
        await cur.execute(
            "SELECT DISTINCT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL",
            (table,),
        )
        if {row[0] for row in await cur.fetchall()} != set(_INVITATION_FOREIGN_KEYS[table]):
            raise RuntimeError(f"Office {table} foreign key set is invalid")
        await cur.execute(
            "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
            (table,),
        )
        if {row[0] for row in await cur.fetchall()} != set(indexes):
            raise RuntimeError(f"Office {table} index set is invalid")
    await _assert_append_only_table_metadata(
        cur, table="office_invitation_issuance", triggers=_INVITATION_ISSUANCE_TRIGGERS,
        primary_key=("issuance_id",))
    await _assert_append_only_table_metadata(
        cur, table="office_invitation_event", triggers=_INVITATION_EVENT_TRIGGERS)
    for table, expected in (("office_invitation", _INVITATION_STATE_TRIGGERS),
                            ("office_invitation_outbox", _INVITATION_OUTBOX_TRIGGERS)):
        await cur.execute(
            "SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT "
            "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE=%s", (table,))
        if {row[0]: tuple(row[1:]) for row in await cur.fetchall()} != expected:
            raise RuntimeError(f"Office {table} triggers are missing or invalid")
    for name, expected_digest in _INVITATION_CHECK_DIGESTS.items():
        if _check_digest(await _check_clause(cur, name)) != expected_digest:
            raise RuntimeError(f"Office invitation check {name} is invalid")
    await cur.execute(
        "SELECT CONSTRAINT_NAME FROM information_schema.CHECK_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME IN "
        "('office_invitation_issuance','office_invitation','office_invitation_event','office_invitation_outbox')",
    )
    if {row[0] for row in await cur.fetchall()} != set(_INVITATION_CHECK_DIGESTS):
        raise RuntimeError("Office invitation check set is invalid")
    await cur.execute(
        "SELECT GENERATION_EXPRESSION FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='office_invitation' AND COLUMN_NAME='pending_email'"
    )
    if await cur.fetchone() != ("case when `status` = 'ISSUED' then `email_normalized` else NULL end",):
        raise RuntimeError("Office invitation pending-email generation is invalid")
    await _assert_invitation_mutation_receipt_runtime_metadata(cur)


async def _assert_invitation_mutation_receipt_runtime_metadata(cur) -> None:
    """Attest the global replay key and its immutable response binding."""
    table = "office_invitation_mutation_receipt"
    if not await _table_exists(cur, table):
        raise RuntimeError("Office invitation mutation receipt is missing")
    await _assert_columns(cur, table=table, expected=_INVITATION_MUTATION_RECEIPT_COLUMNS)
    await _assert_indexes(cur, table=table, expected=_INVITATION_MUTATION_RECEIPT_INDEXES)
    await _assert_foreign_keys(cur, table=table, expected=_INVITATION_MUTATION_RECEIPT_FOREIGN_KEYS)
    await cur.execute(
        "SELECT DISTINCT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL", (table,))
    if {row[0] for row in await cur.fetchall()} != set(_INVITATION_MUTATION_RECEIPT_FOREIGN_KEYS):
        raise RuntimeError("Office invitation mutation receipt foreign key set is invalid")
    await cur.execute("SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
    if {row[0] for row in await cur.fetchall()} != set(_INVITATION_MUTATION_RECEIPT_INDEXES):
        raise RuntimeError("Office invitation mutation receipt index set is invalid")
    await _assert_append_only_table_metadata(
        cur, table=table, triggers=_INVITATION_MUTATION_RECEIPT_TRIGGERS,
        primary_key=("idempotency_key",))
    await cur.execute(
        "SELECT CONSTRAINT_NAME FROM information_schema.CHECK_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))
    if {row[0] for row in await cur.fetchall()} != {"chk_office_invitation_mutation_receipt_shape"}:
        raise RuntimeError("Office invitation mutation receipt check set is invalid")
    if _check_digest(await _check_clause(cur, "chk_office_invitation_mutation_receipt_shape")) != _INVITATION_MUTATION_RECEIPT_CHECK_DIGEST:
        raise RuntimeError("Office invitation mutation receipt check is invalid")


async def _prepare_invitation_schema(cur) -> None:
    """Replace only an empty, never-ready rejected 3C2 shape.

    The previous development branch used ``office_invitation`` for a
    different, incomplete record.  It never reached a reviewed runtime, but
    private databases can retain its empty DDL.  Rows are evidence: any row
    in that incompatible shape is a hard stop, never a migration heuristic.
    """
    if not await _table_exists(cur, "office_invitation"):
        return
    if (await _column_exists(cur, "office_invitation", "issuance_id")
            and await _column_exists(cur, "office_invitation", "project_id")
            and await _column_exists(cur, "office_invitation", "target_user_id")
            and await _column_exists(cur, "office_invitation_issuance", "proof_digest")
            and await _column_exists(cur, "office_invitation_event", "causation_event_id")):
        return
    tables = (
        "office_invitation_event", "office_invitation_outbox",
        "office_invitation", "office_invitation_issuance",
    )
    for table in tables:
        if await _table_exists(cur, table):
            await cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
            if await cur.fetchone() is not None:
                raise RuntimeError("Office incompatible invitation history requires reviewed migration")
    for trigger in (
        "no_update_office_invitation_event", "no_delete_office_invitation_event",
        "no_update_office_invitation_issuance", "no_delete_office_invitation_issuance",
        "guard_office_invitation_insert", "guard_office_invitation_update", "guard_office_invitation_event_insert",
        "guard_office_invitation_outbox_insert", "guard_office_invitation_outbox_update",
    ):
        await cur.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in tables:
        await cur.execute(f"DROP TABLE IF EXISTS {table}")


async def _upgrade_invitation_session_authorization(cur) -> None:
    """Add the v2 normal-session authority without rewriting v1 MFA evidence."""
    if not await _table_exists(cur, "office_invitation_issuance"):
        return
    if not await _column_exists(cur, "office_invitation_issuance", "issuer_authorization_kind"):
        await cur.execute(
            "ALTER TABLE office_invitation_issuance ADD COLUMN "
            "issuer_authorization_kind VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin "
            "NOT NULL DEFAULT 'MFA_PROOF' AFTER issuer_grant_version"
        )
    # Historic issuance rows retain their non-null v1 evidence.  Only new
    # SESSION_AUTHORIZATION rows carry NULL MFA fields, so no old evidence is
    # fabricated or rewritten during this additive upgrade.
    await cur.execute(
        "ALTER TABLE office_invitation_issuance "
        "MODIFY issuer_proof_event_id BIGINT UNSIGNED NULL, "
        "MODIFY issuer_mfa_version BIGINT NULL, "
        "MODIFY issuer_mfa_at DATETIME(6) NULL"
    )
    clause = await _check_clause(cur, "chk_office_invitation_issuance_shape")
    digest = _check_digest(clause)
    expected = _INVITATION_CHECK_DIGESTS["chk_office_invitation_issuance_shape"]
    if digest == expected:
        return
    if digest != _INVITATION_ISSUANCE_V1_CHECK_DIGEST:
        raise RuntimeError("Office invitation issuance CHECK history is incompatible; historic evidence is not rewritten")
    await cur.execute(
        "SELECT 1 FROM office_invitation_issuance "
        "WHERE issuer_authorization_kind<>'MFA_PROOF' OR issuer_proof_event_id IS NULL "
        "OR issuer_mfa_version IS NULL OR issuer_mfa_at IS NULL LIMIT 1"
    )
    if await cur.fetchone() is not None:
        raise RuntimeError("Office invitation v1 issuance evidence is incomplete; historic evidence is not rewritten")
    statement = next(item for item in OFFICE_INVITATION_DDL if item.startswith(
        "CREATE TABLE IF NOT EXISTS office_invitation_issuance"
    ))
    check_sql = statement.split(
        "CONSTRAINT chk_office_invitation_issuance_shape ", 1
    )[1].rsplit(") ENGINE=", 1)[0]
    await cur.execute("ALTER TABLE office_invitation_issuance DROP CONSTRAINT chk_office_invitation_issuance_shape")
    await cur.execute(
        "ALTER TABLE office_invitation_issuance ADD CONSTRAINT "
        "chk_office_invitation_issuance_shape " + check_sql
    )


async def _preflight_invitation_receipt_causal_history(cur) -> None:
    """Do not replace an old receipt guard over evidence it cannot attest.

    The 2e/306 guard accepted receipt rows before their result event carried
    an authority and invocation digest.  Those receipts are append-only
    evidence.  Adding nullable columns and replacing the guard would make the
    physical contract look current while leaving historic rows unverifiable.
    """
    if not await _table_exists(cur, "office_invitation_mutation_receipt"):
        return
    await cur.execute("SELECT 1 FROM office_invitation_mutation_receipt LIMIT 1")
    if await cur.fetchone() is None:
        return
    name = "guard_office_invitation_mutation_receipt_insert"
    await cur.execute(
        "SELECT EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT "
        "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME=%s",
        (name,),
    )
    guard = await cur.fetchone()
    expected = _INVITATION_MUTATION_RECEIPT_TRIGGERS[name]
    if guard is None or tuple(guard[:4]) != expected[:4]:
        raise RuntimeError("Office invitation mutation receipt guard history is incompatible; historic evidence is not rewritten")
    guard_digest = hashlib.sha256(str(guard[4]).encode("utf-8")).hexdigest()
    if guard_digest in _INVITATION_MUTATION_RECEIPT_GUARD_PREDECESSOR_DIGESTS:
        # These guards did not establish the complete receipt companion
        # invariant.  Existing rows are immutable, so they must be reviewed
        # outside this automatic migration before the guard can be replaced.
        raise RuntimeError("Office invitation receipt history uses a predecessor guard; historic evidence is not rewritten")
    if guard_digest != hashlib.sha256(str(expected[-1]).encode("utf-8")).hexdigest():
        raise RuntimeError("Office invitation mutation receipt guard history is incompatible; historic evidence is not rewritten")
    if (not await _column_exists(cur, "office_invitation_event", "authorization_event_id")
            or not await _column_exists(cur, "office_invitation_event", "invocation_digest")):
        raise RuntimeError(
            "Office invitation receipt history predates causal event binding; historic evidence is not rewritten"
        )
    await cur.execute(
        "SELECT 1 FROM office_invitation_mutation_receipt r "
        "LEFT JOIN office_invitation_event e ON e.event_id=r.result_event_id "
        "WHERE e.event_id IS NULL OR e.authorization_event_id IS NULL OR e.invocation_digest IS NULL "
        "OR e.authorization_event_id<>r.authorization_event_id OR e.invocation_digest<>r.request_digest LIMIT 1"
    )
    if await cur.fetchone() is not None:
        raise RuntimeError(
            "Office invitation receipt history lacks causal event binding; historic evidence is not rewritten"
        )


async def _upgrade_invitation_event_causality(cur) -> None:
    """Add nullable causal fields without rewriting immutable history.

    Existing events predate the receipt-to-event binding and remain readable.
    The new receipt trigger rejects them as companions for a new receipt.
    """
    if not await _table_exists(cur, "office_invitation_event"):
        return
    if not await _column_exists(cur, "office_invitation_event", "authorization_event_id"):
        await cur.execute(
            "ALTER TABLE office_invitation_event "
            "ADD COLUMN authorization_event_id BIGINT UNSIGNED NULL AFTER observed_outbox_state"
        )
    if not await _column_exists(cur, "office_invitation_event", "invocation_digest"):
        await cur.execute(
            "ALTER TABLE office_invitation_event "
            "ADD COLUMN invocation_digest BINARY(32) NULL AFTER authorization_event_id"
        )
    await cur.execute(
        "SELECT COLUMN_NAME,DATA_TYPE,COLUMN_TYPE,IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_invitation_event' "
        "AND COLUMN_NAME IN ('authorization_event_id','invocation_digest') ORDER BY ORDINAL_POSITION"
    )
    if tuple(await cur.fetchall()) != (
        ("authorization_event_id", "bigint", "bigint(20) unsigned", "YES"),
        ("invocation_digest", "binary", "binary(32)", "YES"),
    ):
        raise RuntimeError("Office invitation event causal columns are incompatible; historic evidence is not rewritten")
    if not await _index_exists(cur, "office_invitation_event", "idx_office_invitation_event_authorization"):
        await cur.execute(
            "ALTER TABLE office_invitation_event ADD KEY idx_office_invitation_event_authorization (authorization_event_id)"
        )
    if not await _constraint_exists(cur, "fk_office_invitation_event_authorization"):
        await cur.execute(
            "ALTER TABLE office_invitation_event ADD CONSTRAINT "
            "fk_office_invitation_event_authorization FOREIGN KEY (authorization_event_id) "
            "REFERENCES office_authorization_event(event_id)"
        )


async def _upgrade_invitation_expiry_materialization(cur) -> None:
    """Materialize immutable issuance expiry for a bounded terminal-state sweep."""
    if not await _table_exists(cur, "office_invitation"):
        return
    if not await _column_exists(cur, "office_invitation", "expires_at"):
        await cur.execute("ALTER TABLE office_invitation ADD COLUMN expires_at DATETIME(6) NULL AFTER issuance_id")
    await cur.execute(
        "UPDATE office_invitation i JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id "
        "SET i.expires_at=s.expires_at WHERE i.expires_at IS NULL"
    )
    await cur.execute(
        "SELECT 1 FROM office_invitation i JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id "
        "WHERE i.expires_at IS NULL OR i.expires_at<>s.expires_at LIMIT 1"
    )
    if await cur.fetchone() is not None:
        raise RuntimeError("Office invitation expiry materialization is incompatible; historic evidence is not rewritten")
    await cur.execute("ALTER TABLE office_invitation MODIFY COLUMN expires_at DATETIME(6) NOT NULL")
    # MariaDB may retain the project-live key name while shortening the key
    # after a predecessor drops ``expires_at``.  Resume only that exact,
    # measured partial state.  A homonymous key with another shape could be
    # an operator's constraint or an unknown history, so it fails closed.
    status_name = "idx_office_invitation_status_expiry"
    status_columns = ("status", "expires_at", "invitation_id")
    if not await _index_exists(cur, "office_invitation", status_name):
        await cur.execute(
            "ALTER TABLE office_invitation ADD KEY "
            "idx_office_invitation_status_expiry (status,expires_at,invitation_id)"
        )
    elif not await _index_has_exact_shape(cur, "office_invitation", status_name, status_columns):
        raise RuntimeError("Office invitation status-expiry index is incompatible; historic evidence is not rewritten")

    project_name = "idx_office_invitation_project_live"
    project_columns = ("project_id", "status", "expires_at", "invitation_id")
    predecessor_columns = ("project_id", "status", "invitation_id")
    if not await _index_exists(cur, "office_invitation", project_name):
        await cur.execute(
            "ALTER TABLE office_invitation ADD KEY "
            "idx_office_invitation_project_live (project_id,status,expires_at,invitation_id)"
        )
    elif await _index_has_exact_shape(cur, "office_invitation", project_name, project_columns):
        return
    elif await _index_has_exact_shape(cur, "office_invitation", project_name, predecessor_columns):
        await cur.execute("ALTER TABLE office_invitation DROP INDEX idx_office_invitation_project_live")
        await cur.execute(
            "ALTER TABLE office_invitation ADD KEY "
            "idx_office_invitation_project_live (project_id,status,expires_at,invitation_id)"
        )
    else:
        raise RuntimeError("Office invitation project-live index is incompatible; historic evidence is not rewritten")


async def _upgrade_invitation_outbox_stale_index(cur) -> None:
    """Add the bounded stale-claim scan index before the worker can use it."""
    if (await _table_exists(cur, "office_invitation_outbox")
            and not await _index_exists(cur, "office_invitation_outbox", "idx_office_invitation_outbox_stale_claim")):
        await cur.execute(
            "ALTER TABLE office_invitation_outbox ADD KEY "
            "idx_office_invitation_outbox_stale_claim (state,claimed_at,outbox_id)"
        )


async def _upgrade_office_agent_list_indexes(cur) -> None:
    """Add the two exact covering order indexes before selector reads run."""
    indexes = (
        ("office_engine", "idx_office_engine_enabled_key", ("enabled", "engine_key")),
        ("office_agent", "idx_office_agent_project_list", ("project_id", "agent_id")),
    )
    for table, name, columns in indexes:
        if not await _index_exists(cur, table, name):
            await cur.execute(
                f"ALTER TABLE {table} ADD KEY {name} (" + ",".join(columns) + ")"
            )
        elif not await _index_has_exact_shape(cur, table, name, columns):
            raise RuntimeError(f"Office {table} selector index is incompatible; historic evidence is not rewritten")


async def _upgrade_invitation_guards(cur) -> None:
    """Replace only the attested b44 guard set; unknown history fails closed."""
    names = {
        name for name in (
            set(_INVITATION_STATE_TRIGGERS) | set(_INVITATION_EVENT_TRIGGERS) | set(_INVITATION_OUTBOX_TRIGGERS)
        ) if name.startswith("guard_")
    }
    await cur.execute(
        "SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT "
        "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() "
        "AND TRIGGER_NAME IN (" + ",".join("%s" for _ in names) + ")",
        tuple(names),
    )
    found = {row[0]: tuple(row[1:]) for row in await cur.fetchall()}
    if not found:
        return
    all_expected = {**_INVITATION_STATE_TRIGGERS, **_INVITATION_EVENT_TRIGGERS, **_INVITATION_OUTBOX_TRIGGERS}
    expected = {name: all_expected[name] for name in names}
    if found == expected:
        return
    predecessors = (
        _B44_INVITATION_GUARD_DIGESTS,
        _3978115_INVITATION_GUARD_DIGESTS,
        _JAX_MEMORY_TEST_INVITATION_GUARD_DIGESTS,
    )
    actual_digests: dict[str, str] = {}
    for name, metadata in found.items():
        table, timing, event, orientation, action = metadata
        expected_table = "office_invitation_outbox" if "outbox" in name else (
            "office_invitation_event" if "event" in name else "office_invitation"
        )
        if (table, timing, event, orientation) != (expected_table, "BEFORE", "UPDATE" if name.endswith("update") else "INSERT", "ROW"):
            raise RuntimeError("Office invitation guard history is incompatible; historic evidence is not rewritten")
        actual_digests[name] = hashlib.sha256(str(action).encode("utf-8")).hexdigest()
    current_digests = {name: hashlib.sha256(str(expected[name][-1]).encode("utf-8")).hexdigest() for name in found}
    if actual_digests != current_digests and not any(
        all(actual == current_digests[name] or actual == predecessor.get(name)
            for name, actual in actual_digests.items())
        for predecessor in predecessors
    ):
        # A known predecessor may be partially replaced by the current set,
        # but bodies from different predecessor revisions are not attested.
        raise RuntimeError("Office invitation guard history is incompatible; historic evidence is not rewritten")
    for name in found:
        await cur.execute(f"DROP TRIGGER {name}")


async def _upgrade_invitation_mutation_receipt_guard(cur) -> None:
    """Replace only measured predecessor receipt guards, preserving rows."""
    name = "guard_office_invitation_mutation_receipt_insert"
    await cur.execute(
        "SELECT EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT "
        "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME=%s",
        (name,),
    )
    row = await cur.fetchone()
    if row is None:
        return
    expected = _INVITATION_MUTATION_RECEIPT_TRIGGERS[name]
    if tuple(row) == expected:
        return
    if (tuple(row[:4]) != expected[:4]
            or hashlib.sha256(str(row[4]).encode("utf-8")).hexdigest()
            not in _INVITATION_MUTATION_RECEIPT_GUARD_PREDECESSOR_DIGESTS):
        raise RuntimeError("Office invitation mutation receipt guard history is incompatible; historic evidence is not rewritten")
    await cur.execute(f"DROP TRIGGER {name}")


async def assert_membership_receipt_runtime_metadata(cur, *, allow_redacted_trigger_body: bool = False,
                                                     require_table: bool = False) -> None:
    if not await _table_exists(cur, "office_membership_mutation_receipt"):
        if require_table:
            raise RuntimeError("Office membership receipt table is missing")
        return
    await _assert_columns(
        cur,
        table="office_membership_mutation_receipt",
        expected=await _membership_receipt_columns(cur),
    )
    await _assert_indexes(cur, table="office_membership_mutation_receipt", expected=_MEMBERSHIP_RECEIPT_INDEXES)
    await _assert_foreign_keys(cur, table="office_membership_mutation_receipt", expected=_MEMBERSHIP_RECEIPT_FOREIGN_KEYS)
    await _assert_append_only_table_metadata(
        cur, table="office_membership_mutation_receipt", triggers=_MEMBERSHIP_RECEIPT_TRIGGERS,
        allow_redacted_trigger_body=allow_redacted_trigger_body)
    for name, digest in _MEMBERSHIP_RECEIPT_CHECK_DIGESTS.items():
        if _check_digest(await _check_clause(cur, name)) != digest:
            raise RuntimeError(f"Office membership receipt CHECK {name} is invalid")


async def assert_profile_revocation_runtime_metadata(cur, *, allow_redacted_trigger_body: bool = False) -> None:
    await _assert_columns(cur, table="office_profile_revocation_event", expected=_PROFILE_REVOCATION_COLUMNS)
    await _assert_indexes(cur, table="office_profile_revocation_event", expected=_PROFILE_REVOCATION_INDEXES)
    await _assert_foreign_keys(cur, table="office_profile_revocation_event", expected=_PROFILE_REVOCATION_FOREIGN_KEYS)
    await _assert_append_only_table_metadata(
        cur, table="office_profile_revocation_event", triggers=_PROFILE_REVOCATION_TRIGGERS,
        allow_redacted_trigger_body=allow_redacted_trigger_body)
    for name, digest in _PROFILE_REVOCATION_CHECK_DIGESTS.items():
        if _check_digest(await _check_clause(cur, name)) != digest:
            raise RuntimeError(f"Office profile revocation CHECK {name} is invalid")


async def assert_project_role_receipt_runtime_metadata(cur, *, allow_redacted_trigger_body: bool = False) -> None:
    """Require the immutable role replay ledger before the runtime is ready."""
    table = "office_project_role_mutation_receipt"
    if not await _table_exists(cur, table):
        raise RuntimeError("Office project role receipt table is missing")
    await _assert_columns(cur, table=table, expected=_PROJECT_ROLE_RECEIPT_COLUMNS)
    await _assert_indexes(cur, table=table, expected=_PROJECT_ROLE_RECEIPT_INDEXES)
    await cur.execute(
        "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,),
    )
    if {row[0] for row in await cur.fetchall()} != set(_PROJECT_ROLE_RECEIPT_INDEXES):
        raise RuntimeError("Office project role receipt index set is invalid")
    await _assert_foreign_keys(cur, table=table, expected=_PROJECT_ROLE_RECEIPT_FOREIGN_KEYS)
    await cur.execute(
        "SELECT DISTINCT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL",
        (table,),
    )
    if {row[0] for row in await cur.fetchall()} != set(_PROJECT_ROLE_RECEIPT_FOREIGN_KEYS):
        raise RuntimeError("Office project role receipt foreign key set is invalid")
    await cur.execute(
        "SELECT CONSTRAINT_NAME FROM information_schema.CHECK_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,),
    )
    if {row[0] for row in await cur.fetchall()} != {"chk_office_project_role_receipt_shape"}:
        raise RuntimeError("Office project role receipt check set is invalid")
    if (_check_digest(await _check_clause(cur, "chk_office_project_role_receipt_shape"))
            != _PROJECT_ROLE_RECEIPT_CHECK_DIGEST):
        raise RuntimeError("Office project role receipt check is invalid")
    await _assert_append_only_table_metadata(
        cur, table=table, triggers=_PROJECT_ROLE_RECEIPT_TRIGGERS,
        allow_redacted_trigger_body=allow_redacted_trigger_body, primary_key=("receipt_id",))

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

_AUDIT_TRIGGER_METADATA = {
    "no_update_office_authorization_event": (
        "office_authorization_event", "BEFORE", "UPDATE", "ROW",
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_authorization_event append-only'",
    ),
    "no_delete_office_authorization_event": (
        "office_authorization_event", "BEFORE", "DELETE", "ROW",
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'office_authorization_event append-only'",
    ),
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
    # MariaDB stores this CHECK with its canonical lower-case boolean
    # operators and without redundant grouping parentheses.  This is the
    # exact metadata representation to attest, not a permissive comparison.
    "chk_office_bootstrap_verification": "`verification_method` = 'MFA' and `identity_proof_event_id` is not null and `credential_audit_id` is not null or `verification_method` = 'PASSWORD_REAUTH' and `identity_proof_event_id` is null and `credential_audit_id` is null",
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


async def _index_has_exact_shape(cur, table: str, index: str, columns: tuple[str, ...], *, unique: bool = False) -> bool:
    """Attest the complete MariaDB index shape, not merely its name/columns."""
    await cur.execute(
        "SELECT SEQ_IN_INDEX,NON_UNIQUE,SUB_PART,COLLATION,INDEX_TYPE,IGNORED,COLUMN_NAME "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s "
        "AND INDEX_NAME=%s ORDER BY SEQ_IN_INDEX",
        (table, index),
    )
    rows = await cur.fetchall()
    return (
        len(rows) == len(columns)
        and tuple(row[0] for row in rows) == tuple(range(1, len(columns) + 1))
        and all(
            int(row[1]) == int(not unique)
            and row[2] is None
            and row[3] == "A"
            and row[4] == "BTREE"
            and row[5] == "NO"
            for row in rows
        )
        and tuple(row[6] for row in rows) == columns
    )


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
        if not await _index_has_exact_shape(cur, table, index, columns):
            raise RuntimeError("Office project list index is incompatible")


async def _ensure_published_grant_role_check(cur) -> None:
    """Reject legacy drift where a client publication can be a project admin."""
    name = "chk_office_project_grant_published_role"
    clause = await _check_clause(cur, name)
    # MariaDB persists identifiers with backticks, so use its exact canonical
    # rendering rather than comparing a source spelling that will never round
    # trip through information_schema.
    canonical = _normalize_mariadb_check("`audience` = 'INTERNAL' or `grant_role` = 'COLLABORATOR'")
    if clause is None:
        await cur.execute(
            "ALTER TABLE office_project_grant ADD CONSTRAINT " + name + " "
            "CHECK (audience='INTERNAL' OR grant_role='COLLABORATOR')"
        )
        clause = await _check_clause(cur, name)
    if _normalize_mariadb_check(str(clause)) != canonical:
        raise RuntimeError("Office published grant role constraint is incompatible")


async def _ensure_profile_binding_evidence(cur) -> None:
    """Add nullable provenance only; legacy profile evidence is never rewritten."""
    if not await _column_exists(cur, "office_account_profile", "bound_by_user_id"):
        await cur.execute("ALTER TABLE office_account_profile ADD COLUMN bound_by_user_id INT NULL AFTER version")
    if not await _column_exists(cur, "office_account_profile", "credential_audit_id"):
        await cur.execute("ALTER TABLE office_account_profile ADD COLUMN credential_audit_id BIGINT NULL AFTER bound_by_user_id")
    if not await _constraint_exists(cur, "fk_office_account_profile_bound_by"):
        await cur.execute("ALTER TABLE office_account_profile ADD CONSTRAINT fk_office_account_profile_bound_by FOREIGN KEY (bound_by_user_id) REFERENCES jax_users(user_id)")
    if not await _constraint_exists(cur, "fk_office_account_profile_credential_audit"):
        await cur.execute("ALTER TABLE office_account_profile ADD CONSTRAINT fk_office_account_profile_credential_audit FOREIGN KEY (credential_audit_id) REFERENCES user_admin_audit(id)")
    name = "chk_office_account_profile_binding"
    if await _check_clause(cur, name) is None:
        await cur.execute("ALTER TABLE office_account_profile ADD CONSTRAINT " + name + " CHECK ((bound_by_user_id IS NULL AND credential_audit_id IS NULL) OR (bound_by_user_id IS NOT NULL AND credential_audit_id IS NOT NULL))")
    if await _check_clause(cur, name) is None:
        raise RuntimeError("Office profile binding constraint is missing")


_DELEGATION_UNIQUE_INDEX = ("tenant_id", "user_id", "capability")
_DELEGATION_SCOPE_INDEX = ("tenant_id", "user_id", "capability", "status")
_DELEGATION_USER_INDEX = ("user_id",)


async def _delegation_row_count(cur) -> int:
    await cur.execute("SELECT COUNT(*) FROM office_admin_delegation")
    return int((await cur.fetchone())[0])


async def _ensure_tenant_bound_delegation(cur) -> None:
    """Upgrade only an empty legacy delegation table to the tenant contract.

    Global rows have no authoritative tenant attribution.  We therefore refuse
    before any delegation DDL when they exist.  An interrupted upgrade is
    resumable only while the table remains empty; this avoids treating a row
    written into a partially upgraded shape as historical evidence.
    """
    has_tenant = await _column_exists(cur, "office_admin_delegation", "tenant_id")
    if not has_tenant and await _delegation_row_count(cur):
        raise RuntimeError("Office legacy delegations require reviewed tenant assignment")

    # The old unique key begins with user_id and often backs the user FK.  Give
    # that FK an explicit supporting index before replacing the unique key.
    if not await _index_exists(cur, "office_admin_delegation", "idx_office_admin_delegation_user"):
        await cur.execute("ALTER TABLE office_admin_delegation ADD KEY idx_office_admin_delegation_user (user_id)")

    if not has_tenant:
        await cur.execute("ALTER TABLE office_admin_delegation ADD COLUMN tenant_id INT NOT NULL AFTER delegation_id")

    unique_ok = await _index_has_exact_shape(
        cur, "office_admin_delegation", "uq_office_admin_delegation", _DELEGATION_UNIQUE_INDEX, unique=True)
    scope_ok = await _index_has_exact_shape(
        cur, "office_admin_delegation", "idx_office_admin_delegation_scope", _DELEGATION_SCOPE_INDEX)
    user_ok = await _index_has_exact_shape(
        cur, "office_admin_delegation", "idx_office_admin_delegation_user", _DELEGATION_USER_INDEX)
    if not (unique_ok and scope_ok and user_ok):
        if await _delegation_row_count(cur):
            raise RuntimeError("Office admin delegation indexes are incompatible")
        if await _index_exists(cur, "office_admin_delegation", "uq_office_admin_delegation"):
            await cur.execute("ALTER TABLE office_admin_delegation DROP INDEX uq_office_admin_delegation")
        if await _index_exists(cur, "office_admin_delegation", "idx_office_admin_delegation_scope"):
            await cur.execute("ALTER TABLE office_admin_delegation DROP INDEX idx_office_admin_delegation_scope")
        if not await _index_exists(cur, "office_admin_delegation", "uq_office_admin_delegation"):
            await cur.execute("ALTER TABLE office_admin_delegation ADD UNIQUE KEY uq_office_admin_delegation (tenant_id,user_id,capability)")
        if not await _index_exists(cur, "office_admin_delegation", "idx_office_admin_delegation_scope"):
            await cur.execute("ALTER TABLE office_admin_delegation ADD KEY idx_office_admin_delegation_scope (tenant_id,user_id,capability,status)")

    if not await _constraint_exists(cur, "fk_office_admin_delegation_tenant"):
        await cur.execute("ALTER TABLE office_admin_delegation ADD CONSTRAINT fk_office_admin_delegation_tenant FOREIGN KEY (tenant_id) REFERENCES jax_tenants(tenant_id)")
    if not await _index_has_exact_shape(cur, "office_admin_delegation", "uq_office_admin_delegation", _DELEGATION_UNIQUE_INDEX, unique=True):
        raise RuntimeError("Office admin delegation unique index is incompatible")
    if not await _index_has_exact_shape(cur, "office_admin_delegation", "idx_office_admin_delegation_scope", _DELEGATION_SCOPE_INDEX):
        raise RuntimeError("Office admin delegation scope index is incompatible")
    if not await _index_has_exact_shape(cur, "office_admin_delegation", "idx_office_admin_delegation_user", _DELEGATION_USER_INDEX):
        raise RuntimeError("Office admin delegation user index is incompatible")
    await _assert_foreign_keys(
        cur, table="office_admin_delegation", expected={
            "fk_office_admin_delegation_tenant": (("tenant_id",), "jax_tenants", ("tenant_id",), "RESTRICT", "RESTRICT"),
        })


async def _assert_professional_grant_version(cur) -> None:
    """The CAS column is authority state; a same-named nullable field is unsafe."""
    await cur.execute(
        "SELECT DATA_TYPE,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='office_professional_grant' AND COLUMN_NAME='version'"
    )
    row = await cur.fetchone()
    if row != ("bigint", "bigint(20)", "NO", "1"):
        raise RuntimeError("Office professional grant version is incompatible")


async def _constraint_exists(cur, name: str) -> bool:
    await cur.execute("SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME=%s", (name,))
    return await cur.fetchone() is not None


async def _ensure_terminal_ledger_foreign_keys(cur) -> None:
    """Backfill only absent terminal-ledger links; attestation rejects drift."""
    additions = (
        (
            "office_membership_mutation_receipt",
            "fk_office_membership_receipt_grant",
            "ALTER TABLE office_membership_mutation_receipt ADD CONSTRAINT "
            "fk_office_membership_receipt_grant FOREIGN KEY (project_id,target_user_id) "
            "REFERENCES office_project_grant(project_id,user_id)",
        ),
        (
            "office_profile_revocation_event",
            "fk_office_profile_revocation_target",
            "ALTER TABLE office_profile_revocation_event ADD CONSTRAINT "
            "fk_office_profile_revocation_target FOREIGN KEY (target_user_id) "
            "REFERENCES office_account_profile(user_id)",
        ),
    )
    for table, name, statement in additions:
        if await _table_exists(cur, table) and not await _constraint_exists(cur, name):
            await cur.execute(statement)
    # This was the original profile-event link.  It can be upgraded without
    # changing historic rows because profiles are durable, soft-revoked state.
    # A differently shaped same-name constraint is never repaired silently.
    if await _table_exists(cur, "office_profile_revocation_event"):
        await cur.execute(
            "SELECT k.COLUMN_NAME,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE k WHERE k.CONSTRAINT_SCHEMA=DATABASE() "
            "AND k.TABLE_NAME='office_profile_revocation_event' "
            "AND k.CONSTRAINT_NAME='fk_office_profile_revocation_target' "
            "ORDER BY k.ORDINAL_POSITION"
        )
        legacy_shape = (("target_user_id", "jax_users", "user_id"),)
        if tuple(await cur.fetchall()) == legacy_shape:
            await cur.execute(
                "ALTER TABLE office_profile_revocation_event "
                "DROP CONSTRAINT fk_office_profile_revocation_target"
            )
            await cur.execute(
                "ALTER TABLE office_profile_revocation_event ADD CONSTRAINT "
                "fk_office_profile_revocation_target FOREIGN KEY (target_user_id) "
                "REFERENCES office_account_profile(user_id)"
            )


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
        "AND EVENT_OBJECT_TABLE='office_mfa_security_event'"
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


async def assert_audit_runtime_metadata(cur) -> None:
    """Attest the audit ledger trigger bodies before stamping readiness.

    Runtime principals cannot read trigger bodies on the target MariaDB
    version, so the runtime guard can only check trigger identity.  The
    migrator has DDL metadata access and therefore owns this full-body check.
    """
    await cur.execute(
        "SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,"
        "ACTION_ORIENTATION,ACTION_STATEMENT FROM information_schema.TRIGGERS "
        "WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE='office_authorization_event'"
    )
    found = {row[0]: tuple(row[1:]) for row in await cur.fetchall()}
    if found != _AUDIT_TRIGGER_METADATA:
        raise RuntimeError("Office authorization audit triggers are missing or invalid")


async def assert_bootstrap_runtime_metadata(cur, *, allow_redacted_trigger_body: bool = False) -> None:
    """Attest bootstrap's signed/unsigned foreign keys and append-only guard."""
    await cur.execute(
        "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_bootstrap_state' ORDER BY ORDINAL_POSITION"
    )
    if tuple(await cur.fetchall()) != (
        ("ceremony_key", "varchar(64)", "NO"), ("target_user_id", "int(11)", "NO"),
        ("identity_actor_user_id", "int(11)", "NO"),
        ("identity_proof_event_id", "bigint(20) unsigned", "YES"),
        ("credential_audit_id", "bigint(20)", "YES"),
        ("verification_method", "varchar(24)", "NO"), ("basis_ref", "varchar(128)", "NO"),
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
    check_placeholders = ",".join("%s" for _ in _BOOTSTRAP_CHECKS)
    await cur.execute(
        "SELECT CONSTRAINT_NAME,CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME IN (" + check_placeholders + ")",
        tuple(_BOOTSTRAP_CHECKS),
    )
    checks = {str(name): _normalize_mariadb_check(str(clause)) for name, clause in await cur.fetchall()}
    if checks != {name: _normalize_mariadb_check(clause) for name, clause in _BOOTSTRAP_CHECKS.items()}:
        raise RuntimeError("Office bootstrap state checks are invalid")
    await cur.execute(
        "SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_ORIENTATION,ACTION_STATEMENT "
        "FROM information_schema.TRIGGERS "
        "WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE='office_bootstrap_state'"
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


async def _upgrade_bootstrap_schema(cur) -> None:
    """Evolve the immutable bootstrap receipt without fabricating MFA proof.

    Predecessor rows are real MFA ceremonies, so an added non-null default is
    semantically exact for them.  No historic row is rewritten and the
    append-only guards remain present throughout the migration.
    """
    if not await _column_exists(cur, "office_bootstrap_state", "verification_method"):
        await cur.execute(
            "ALTER TABLE office_bootstrap_state ADD COLUMN verification_method "
            "VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'MFA' AFTER credential_audit_id"
        )
    await cur.execute(
        "ALTER TABLE office_bootstrap_state MODIFY identity_proof_event_id BIGINT UNSIGNED NULL, "
        "MODIFY credential_audit_id BIGINT NULL"
    )
    if not await _constraint_exists(cur, "chk_office_bootstrap_verification"):
        await cur.execute(
            "ALTER TABLE office_bootstrap_state ADD CONSTRAINT chk_office_bootstrap_verification CHECK "
            "((verification_method='MFA' AND identity_proof_event_id IS NOT NULL AND credential_audit_id IS NOT NULL) "
            "OR (verification_method='PASSWORD_REAUTH' AND identity_proof_event_id IS NULL AND credential_audit_id IS NULL))"
        )


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


_OFFICE_AGENT_RUN_OUTPUT_V1 = (
    "`status` = 'SUCCEEDED' and `output_text` is not null and `completed_at` is not null "
    "or `status` in ('DISPATCHING','UNKNOWN') and `output_text` is null"
)
_OFFICE_AGENT_RUN_OUTPUT_V2 = _AGENT_RUNTIME_CHECK_CLAUSES["chk_office_agent_run_output"]


async def _ensure_agent_runtime_invariants(cursor) -> None:
    """Upgrade only recognized pre-release shapes without rewriting run history."""
    if not await _column_exists(cursor, "office_agent_binding", "active_agent_id"):
        await cursor.execute(
            "SELECT agent_id FROM office_agent_binding WHERE active=TRUE "
            "GROUP BY agent_id HAVING COUNT(*)>1 LIMIT 1"
        )
        if await cursor.fetchone() is not None:
            raise RuntimeError("Office agent bindings have multiple active rows; historic bindings are not rewritten")
        await cursor.execute(
            "ALTER TABLE office_agent_binding ADD COLUMN active_agent_id BIGINT UNSIGNED "
            "GENERATED ALWAYS AS (IF(active,agent_id,NULL)) STORED AFTER active, "
            "ADD UNIQUE KEY uq_office_agent_binding_active_agent (active_agent_id)"
        )
    elif not await _index_exists(cursor, "office_agent_binding", "uq_office_agent_binding_active_agent"):
        raise RuntimeError("Office agent binding active identity index is absent")

    existing = _normalize_mariadb_check(await _check_clause(cursor, "chk_office_agent_run_output"))
    if existing == _normalize_mariadb_check(_OFFICE_AGENT_RUN_OUTPUT_V2):
        return
    if existing != _normalize_mariadb_check(_OFFICE_AGENT_RUN_OUTPUT_V1):
        raise RuntimeError("Office agent run output constraint is incompatible; historic runs are not rewritten")
    await cursor.execute(
        "SELECT 1 FROM office_agent_run WHERE NOT ("
        "(status='SUCCEEDED' AND output_text IS NOT NULL AND completed_at IS NOT NULL) OR "
        "(status='DISPATCHING' AND output_text IS NULL AND completed_at IS NULL) OR "
        "(status='UNKNOWN' AND output_text IS NULL AND completed_at IS NOT NULL)"
        ") LIMIT 1"
    )
    if await cursor.fetchone() is not None:
        raise RuntimeError("Office agent run output rows are incompatible; historic runs are not rewritten")
    await cursor.execute(
        "ALTER TABLE office_agent_run DROP CONSTRAINT chk_office_agent_run_output, "
        "ADD CONSTRAINT chk_office_agent_run_output CHECK "
        "((status='SUCCEEDED' AND output_text IS NOT NULL AND completed_at IS NOT NULL) OR "
        "(status='DISPATCHING' AND output_text IS NULL AND completed_at IS NULL) OR "
        "(status='UNKNOWN' AND output_text IS NULL AND completed_at IS NOT NULL))"
    )


async def apply_office_migrations(cursor) -> None:
    """Apply only Office-owned additive DDL after JAX's namespace migration."""
    # Parse the shared manifest before any DDL.  A malformed/ambiguous ACL
    # must not leave a new schema revision that a web process could trust.
    runtime_table_privilege_digest()
    if not await _column_exists(cursor, "jax_project_scope", "authorization_namespace"):
        raise RuntimeError("JAX authorization_namespace migration is required before Virtual Office")
    if not await _column_exists(cursor, "jax_users", "account_class"):
        await cursor.execute("ALTER TABLE jax_users ADD COLUMN account_class VARCHAR(24) NOT NULL DEFAULT 'INTERNAL', ADD CONSTRAINT chk_jax_users_account_class CHECK (account_class IN ('INTERNAL','OFFICE_INTERNAL','EXTERNAL_CLIENT'))")
    if not await _column_exists(cursor, "jax_users", "mfa_version"):
        await cursor.execute("ALTER TABLE jax_users ADD COLUMN mfa_version BIGINT NOT NULL DEFAULT 0, ADD CONSTRAINT chk_jax_users_mfa_version CHECK (mfa_version >= 0)")
    for statement in OFFICE_DDL:
        await cursor.execute(statement)
    # v10 has no Office engine or agent tables.  This additive v11 cut is
    # applied before any route can resolve a binding or create a run.
    for statement in OFFICE_ENGINE_DDL:
        await cursor.execute(statement)
    for statement in OFFICE_AGENT_RUNTIME_DDL:
        await cursor.execute(statement)
    for statement in OFFICE_AGENT_DDL:
        await cursor.execute(statement)
    await _upgrade_office_agent_list_indexes(cursor)
    await _ensure_agent_runtime_invariants(cursor)
    await _ensure_profile_binding_evidence(cursor)
    await _ensure_published_grant_role_check(cursor)
    await _ensure_office_list_indexes(cursor)
    await assert_audit_runtime_metadata(cursor)
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
        if not await _index_has_exact_shape(cursor, "office_mfa_security_event", proof_index, proof_columns):
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
    await _upgrade_bootstrap_schema(cursor)
    await assert_bootstrap_runtime_metadata(cursor)
    await _prepare_invitation_schema(cursor)
    await _upgrade_invitation_session_authorization(cursor)
    await _preflight_invitation_receipt_causal_history(cursor)
    await _upgrade_invitation_event_causality(cursor)
    # Attest and remove only known lifecycle guards before the one-time
    # backfill; predecessor update guards reject every non-transition UPDATE.
    await _upgrade_invitation_guards(cursor)
    await _upgrade_invitation_expiry_materialization(cursor)
    await _upgrade_invitation_outbox_stale_index(cursor)
    await _upgrade_invitation_mutation_receipt_guard(cursor)
    for statement in OFFICE_INVITATION_DDL:
        await cursor.execute(statement)
    await assert_invitation_runtime_metadata(cursor)
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
    relation_receipt_index = "uq_office_external_relation_receipt"
    relation_receipt_columns = ("relation_id", "project_id", "user_id", "recipient_group_id")
    if not await _index_exists(cursor, "office_external_relation", relation_receipt_index):
        await cursor.execute(
            "ALTER TABLE office_external_relation ADD UNIQUE KEY " + relation_receipt_index
            + " (relation_id,project_id,user_id,recipient_group_id)"
        )
    if not await _index_has_exact_shape(
        cursor, "office_external_relation", relation_receipt_index, relation_receipt_columns, unique=True,
    ):
        raise RuntimeError("Office external relation receipt index is incompatible")
    await _ensure_tenant_bound_delegation(cursor)
    # Professional capability is mutable/revocable.  Existing pre-3C rows
    # receive the deterministic first version; later API mutations use a CAS
    # instead of treating a stale form as authority.
    if not await _column_exists(cursor, "office_professional_grant", "version"):
        await cursor.execute("ALTER TABLE office_professional_grant ADD COLUMN version BIGINT NOT NULL DEFAULT 1 AFTER granted_by")
    await _assert_professional_grant_version(cursor)
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
    # Receipt is deliberately last: a committed receipt means the canonical
    # JAX event, Office companion and authorization event all committed.
    # Focused legacy/MFA fixtures intentionally exercise only their tiny
    # predecessor schema.  A real platform migration has already installed
    # this JAX core table; without it no member route can run or be ready.
    if await _table_exists(cursor, "jax_namespace_membership_mutation_request"):
        membership_receipt_collation = await _membership_receipt_collation(cursor)
        for statement in OFFICE_MEMBERSHIP_RECEIPT_DDL:
            await cursor.execute(statement.format(membership_receipt_collation=membership_receipt_collation))
    for statement in OFFICE_PROJECT_ROLE_RECEIPT_DDL:
        await cursor.execute(statement)
    for statement in OFFICE_PROFILE_REVOCATION_DDL:
        await cursor.execute(statement)
    await _ensure_terminal_ledger_foreign_keys(cursor)
    if await _table_exists(cursor, "office_membership_mutation_receipt"):
        await assert_membership_receipt_runtime_metadata(cursor)
    await assert_project_role_receipt_runtime_metadata(cursor)
    await assert_profile_revocation_runtime_metadata(cursor)
    # Candidate storage is intentionally inert: it is only untrusted bytes and
    # claims, never publication, approval, or a release decision.  It must be
    # attested here, before db.migrations stamps the Office runtime revision.
    from virtual_office.module_release_ledger import apply_module_candidate_storage_migrations
    await apply_module_candidate_storage_migrations(cursor)
    await apply_module_publication_staging_migrations(cursor)
    await assert_office_agent_runtime_metadata(cursor)
