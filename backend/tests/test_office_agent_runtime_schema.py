"""Durable execution limits and snapshots for the private-agent runtime."""
from __future__ import annotations


def test_runtime_config_is_a_seeded_singleton_with_bounded_values() -> None:
    from virtual_office import migrations

    ddl = "\n".join(migrations.OFFICE_AGENT_RUNTIME_DDL)
    assert "office_agent_runtime_config" in ddl
    assert "config_id TINYINT UNSIGNED NOT NULL PRIMARY KEY" in ddl
    assert "max_output_tokens SMALLINT UNSIGNED NOT NULL DEFAULT 2048" in ddl
    assert "max_input_bytes INT UNSIGNED NOT NULL DEFAULT 32768" in ddl
    assert "max_instructions_bytes INT UNSIGNED NOT NULL DEFAULT 32768" in ddl
    assert "max_response_bytes INT UNSIGNED NOT NULL DEFAULT 1048576" in ddl
    assert "window_seconds INT UNSIGNED NOT NULL DEFAULT 3600" in ddl
    assert "actor_runs_per_window SMALLINT UNSIGNED NOT NULL DEFAULT 30" in ddl
    assert "project_runs_per_window SMALLINT UNSIGNED NOT NULL DEFAULT 300" in ddl
    assert "actor_concurrency TINYINT UNSIGNED NOT NULL DEFAULT 2" in ddl
    assert "project_concurrency TINYINT UNSIGNED NOT NULL DEFAULT 8" in ddl
    assert "timeout_seconds TINYINT UNSIGNED NOT NULL DEFAULT 45" in ddl
    assert "stale_seconds TINYINT UNSIGNED NOT NULL DEFAULT 60" in ddl
    assert "INSERT INTO office_agent_runtime_config" in ddl


def test_runtime_config_is_migrator_owned() -> None:
    from db.runtime_privileges import _FORBIDDEN_TABLES

    assert "office_agent_runtime_config" in _FORBIDDEN_TABLES


def test_run_schema_pins_every_dispatch_snapshot_and_actor_idempotency() -> None:
    from virtual_office import migrations

    ddl = "\n".join(migrations.OFFICE_AGENT_DDL)
    for field in (
        "authorization_event_id", "agent_version", "binding_id", "binding_version",
        "instructions_sha256", "dispatch_snapshot_sha256", "config_version", "model_ref",
        "engine_version", "engine_binding_fingerprint", "credential_id", "credential_fingerprint",
        "provider_id", "auth_type", "auth_transport", "accepted_at", "deadline_at", "fence",
    ):
        assert field in ddl
    assert "UNIQUE KEY uq_office_agent_run_idempotency (requested_by,agent_id,idempotency_key)" in ddl
    assert "idx_office_agent_run_actor_window (requested_by,accepted_at,status,deadline_at)" in ddl
    assert "idx_office_agent_run_project_window (project_id,accepted_at,status,deadline_at)" in ddl
    assert "no_mutate_office_agent_run_snapshot" in ddl


def test_engine_binding_v2_includes_the_exact_credential_identity() -> None:
    from virtual_office.engine_resolver import engine_binding_fingerprint

    common = dict(
        engine_key="glm", model_ref=7, transport="http_openai_compat",
        allowed_data_classifications=frozenset({"INTERNAL_CONFIDENTIAL"}),
        provider_fingerprint="a" * 64, model_fingerprint="b" * 64,
        credential_fingerprint="c" * 64, engine_enabled=1, activation_epoch=1, version=1,
    )
    assert engine_binding_fingerprint(**common, credential_id=11) != engine_binding_fingerprint(**common, credential_id=12)
