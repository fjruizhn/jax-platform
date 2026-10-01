"""Reversible MariaDB schema for the F2-D Web Chat output outbox.

These tables hold only effective F2-C output bytes and lifecycle metadata.
They never hold raw provider candidates or governance receipt bodies.
"""

OUTBOX_SCHEMA_VERSION = "f2-d.outbox.2"

UP_SQL = (
    """CREATE TABLE IF NOT EXISTS governed_output_outbox (
      outbox_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      attempt_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      previous_attempt_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
      attempt_sequence INT UNSIGNED NOT NULL,
      tenant_id INT NOT NULL,
      project_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL,
      subject_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
      audience VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
      scope_digest CHAR(71) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      request_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      trace_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      response_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      transport_kind VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      idempotency_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      lifecycle_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      record_schema_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      envelope_schema_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      transport_projection_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      renderer_api_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      domain_spec_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      contract_state VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      effective_output_digest VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      effective_projection_digest CHAR(71) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      original_envelope_digest VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      transport_payload_digest CHAR(71) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      governance_reference_ids JSON NOT NULL,
      claim_ids JSON NOT NULL,
      contains_current_claim BOOLEAN NOT NULL,
      current_not_after DATETIME(6) NULL,
      state VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      response_payload MEDIUMBLOB NOT NULL,
      prepared_at DATETIME(6) NOT NULL,
      committing_at DATETIME(6) NULL,
      committed_at DATETIME(6) NULL,
      acknowledged_at DATETIME(6) NULL,
      failure_class VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
      row_version INT UNSIGNED NOT NULL DEFAULT 1,
      PRIMARY KEY (outbox_id),
      UNIQUE KEY uq_go_output_attempt (tenant_id, scope_digest, request_id, attempt_id),
      UNIQUE KEY uq_go_output_idempotency (tenant_id, idempotency_key),
      UNIQUE KEY uq_go_output_attempt_sequence (tenant_id, scope_digest, request_id, attempt_sequence),
      KEY idx_go_output_scope_state (tenant_id, scope_digest, state, prepared_at),
      KEY idx_go_output_response (tenant_id, scope_digest, response_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "ALTER TABLE governed_output_outbox ADD COLUMN IF NOT EXISTS current_not_after DATETIME(6) NULL",
    """CREATE TABLE IF NOT EXISTS governed_output_lifecycle_events (
      event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      outbox_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      tenant_id INT NOT NULL,
      scope_digest CHAR(71) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      sequence_no INT UNSIGNED NOT NULL,
      event_type VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      from_state VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL,
      to_state VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      occurred_at DATETIME(6) NOT NULL,
      details_json JSON NULL,
      PRIMARY KEY (event_id),
      UNIQUE KEY uq_go_output_event_seq (outbox_id, sequence_no),
      KEY idx_go_output_event_scope (tenant_id, scope_digest, outbox_id),
      CONSTRAINT fk_go_output_event_outbox
        FOREIGN KEY (outbox_id) REFERENCES governed_output_outbox(outbox_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)

DOWN_SQL = (
    "DROP TABLE IF EXISTS governed_output_lifecycle_events",
    "DROP TABLE IF EXISTS governed_output_outbox",
)

# Upgrade rollback preserves all prior F2-D rows and restores the v1 shape.
# DOWN_SQL is the separate opt-in teardown for a fresh, disposable schema.
LEGACY_DOWNGRADE_SQL = (
    "ALTER TABLE governed_output_outbox DROP COLUMN IF EXISTS current_not_after",
)


async def apply(cur) -> None:
    for statement in UP_SQL:
        await cur.execute(statement)


async def rollback(cur) -> None:
    """Explicit destructive rollback for controlled, non-production use."""
    for statement in DOWN_SQL:
        await cur.execute(statement)


async def downgrade_legacy(cur) -> None:
    """Reverse only the v1→v2 expiry-column upgrade, preserving outbox data."""
    for statement in LEGACY_DOWNGRADE_SQL:
        await cur.execute(statement)
