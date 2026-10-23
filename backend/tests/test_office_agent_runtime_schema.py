"""Durable execution limits and snapshots for the private-agent runtime."""
from __future__ import annotations

from pathlib import Path

import pytest
import aiomysql
import uuid


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
    assert "INSERT IGNORE INTO office_agent_runtime_config" in ddl
    assert "max_output_tokens BETWEEN 1 AND 2048" in ddl
    assert "window_seconds BETWEEN 60 AND 3600" in ddl
    assert "actor_concurrency BETWEEN 1 AND 2" in ddl
    assert "project_concurrency BETWEEN 1 AND 8" in ddl
    assert "stale_seconds>=timeout_seconds+5" in ddl


def test_runtime_config_is_migrator_owned() -> None:
    from db.runtime_privileges import _FORBIDDEN_TABLES

    assert "office_agent_runtime_config" in _FORBIDDEN_TABLES


def test_runtime_config_accepts_tighter_valid_database_limits(client) -> None:
    from tests.identidades import sql

    columns = (
        "max_output_tokens,max_input_bytes,max_instructions_bytes,max_response_bytes,"
        "window_seconds,actor_runs_per_window,project_runs_per_window,actor_concurrency,"
        "project_concurrency,timeout_seconds,stale_seconds"
    )

    async def exercise() -> None:
        before = (await sql(f"SELECT config_version,{columns} FROM office_agent_runtime_config WHERE config_id=1", (), True))[0]
        tighter = (1024, 16384, 16384, 524288, 1800, 15, 150, 1, 4, 30, 45)
        try:
            await sql(
                "UPDATE office_agent_runtime_config SET config_version=config_version+1,max_output_tokens=%s,max_input_bytes=%s,"
                "max_instructions_bytes=%s,max_response_bytes=%s,window_seconds=%s,actor_runs_per_window=%s,"
                "project_runs_per_window=%s,actor_concurrency=%s,project_concurrency=%s,timeout_seconds=%s,"
                "stale_seconds=%s WHERE config_id=1",
                tighter,
            )
            assert (await sql(f"SELECT {columns} FROM office_agent_runtime_config WHERE config_id=1", (), True))[0] == tighter
        finally:
            await sql(
                "UPDATE office_agent_runtime_config SET config_version=config_version+1,max_output_tokens=%s,max_input_bytes=%s,"
                "max_instructions_bytes=%s,max_response_bytes=%s,window_seconds=%s,actor_runs_per_window=%s,"
                "project_runs_per_window=%s,actor_concurrency=%s,project_concurrency=%s,timeout_seconds=%s,"
                "stale_seconds=%s WHERE config_id=1",
                before[1:],
            )

    client.portal.call(exercise)


def test_runtime_config_rejects_a_stale_window_shorter_than_timeout(client) -> None:
    from tests.identidades import sql

    async def exercise() -> None:
        with pytest.raises(aiomysql.OperationalError):
            await sql(
                "UPDATE office_agent_runtime_config SET config_version=config_version+1,timeout_seconds=45,stale_seconds=44 WHERE config_id=1"
            )

    client.portal.call(exercise)


def test_runtime_config_rejects_a_limit_change_without_a_version_advance(client) -> None:
    from tests.identidades import sql

    async def exercise() -> None:
        with pytest.raises(aiomysql.OperationalError):
            await sql("UPDATE office_agent_runtime_config SET max_output_tokens=1024 WHERE config_id=1")

    client.portal.call(exercise)


def test_runtime_schema_attestation_rejects_missing_quota_check(client) -> None:
    from tests.identidades import sql
    from virtual_office import migrations

    async def exercise() -> None:
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("ALTER TABLE office_agent_runtime_config DROP CONSTRAINT chk_office_agent_runtime_config_bounds")
                try:
                    with pytest.raises(RuntimeError, match="runtime check"):
                        await migrations.assert_office_agent_runtime_metadata(cur)
                finally:
                    await cur.execute(
                "ALTER TABLE office_agent_runtime_config ADD CONSTRAINT chk_office_agent_runtime_config_bounds "
                "CHECK (max_output_tokens BETWEEN 1 AND 2048 AND max_input_bytes BETWEEN 1 AND 32768 "
                "AND max_instructions_bytes BETWEEN 1 AND 32768 AND max_response_bytes BETWEEN 1 AND 1048576 "
                "AND window_seconds BETWEEN 60 AND 3600 AND actor_runs_per_window BETWEEN 1 AND 30 "
                "AND project_runs_per_window BETWEEN 1 AND 300 AND actor_concurrency BETWEEN 1 AND 2 "
                "AND project_concurrency BETWEEN 1 AND 8 AND timeout_seconds BETWEEN 1 AND 45 "
                "AND stale_seconds BETWEEN 6 AND 60 AND stale_seconds>=timeout_seconds+5)"
                    )

    client.portal.call(exercise)


def test_runtime_schema_attestation_rejects_noop_config_trigger(client) -> None:
    from tests.identidades import sql
    from virtual_office import migrations

    async def exercise() -> None:
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DROP TRIGGER guard_office_agent_runtime_config_update")
                await cur.execute(
            "CREATE TRIGGER guard_office_agent_runtime_config_update BEFORE UPDATE ON office_agent_runtime_config "
            "FOR EACH ROW SET NEW.config_version=NEW.config_version"
                )
                try:
                    with pytest.raises(RuntimeError, match="runtime triggers"):
                        await migrations.assert_office_agent_runtime_metadata(cur)
                finally:
                    await cur.execute("DROP TRIGGER guard_office_agent_runtime_config_update")
                    await cur.execute(migrations.OFFICE_AGENT_RUNTIME_DDL[-1])

    client.portal.call(exercise)


def test_runtime_schema_attestation_rejects_homonymous_column_default_drift(client) -> None:
    from tests.identidades import sql
    from virtual_office import migrations

    async def exercise() -> None:
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "ALTER TABLE office_agent_runtime_config "
                    "MODIFY max_output_tokens SMALLINT UNSIGNED NOT NULL DEFAULT 1024"
                )
                try:
                    with pytest.raises(RuntimeError, match="runtime columns"):
                        await migrations.assert_office_agent_runtime_metadata(cur)
                finally:
                    await cur.execute(
                        "ALTER TABLE office_agent_runtime_config "
                        "MODIFY max_output_tokens SMALLINT UNSIGNED NOT NULL DEFAULT 2048"
                    )

    client.portal.call(exercise)


def test_runtime_schema_attestation_rejects_an_extra_homonymous_trigger(client) -> None:
    from virtual_office import migrations

    async def exercise() -> None:
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "CREATE TRIGGER unexpected_office_agent_insert BEFORE INSERT ON office_agent "
                    "FOR EACH ROW SET NEW.display_name=NEW.display_name"
                )
                try:
                    with pytest.raises(RuntimeError, match="runtime triggers"):
                        await migrations.assert_office_agent_runtime_metadata(cur)
                finally:
                    await cur.execute("DROP TRIGGER unexpected_office_agent_insert")

    client.portal.call(exercise)


def test_runtime_quota_queries_use_their_four_real_indexes(client) -> None:
    """Use skewed durable rows; an empty table makes MariaDB's choice arbitrary."""
    from tests.identidades import crear_usuario, sql

    async def exercise() -> None:
        actor, _ = await crear_usuario()
        project_id = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("quota-index-" + uuid.uuid4().hex,),
        )
        await sql(
            "INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project_id,),
        )
        model_ref, credential_id = (await sql(
            "SELECT m.id,c.id FROM model m JOIN credential c ON c.provider_id=m.provider_id AND c.state='active' "
            "WHERE m.provider_id='zhipu' AND m.model_id='glm-5.3' LIMIT 1", (), True,
        ))[0]
        engine_key = "quota-engine-" + uuid.uuid4().hex
        await sql(
            "INSERT INTO office_engine (engine_key,display_name_key,model_ref,credential_id,transport,allowed_data_classifications,binding_fingerprint,enabled,activation_epoch,version) "
            "VALUES (%s,'Quota',%s,%s,'http_openai_compat',JSON_ARRAY('INTERNAL_CONFIDENTIAL'),%s,FALSE,0,1)",
            (engine_key, model_ref, credential_id, "0" * 64),
        )
        agent_id = await sql(
            "INSERT INTO office_agent (project_id,agent_key,display_name,instructions,data_classification,created_by) "
            "VALUES (%s,'quota','Quota','x','INTERNAL_CONFIDENTIAL',%s)", (project_id, actor),
        )
        binding_id = await sql(
            "INSERT INTO office_agent_binding (agent_id,engine_key,binding_version,bound_by) VALUES (%s,%s,1,%s)",
            (agent_id, engine_key, actor),
        )
        event_id = await sql(
            "INSERT INTO office_authorization_event (actor_user_id,operation,requested_project_id,project_id,tenant_id,result,reason_code,occurred_at) "
            "VALUES (%s,'VIEW_PROJECT',%s,%s,1,'ALLOWED','test',UTC_TIMESTAMP(6))", (actor, project_id, project_id),
        )
        insert_run = (
            "INSERT INTO office_agent_run (run_id,agent_id,project_id,requested_by,authorization_event_id,agent_version,binding_id,binding_version,"
            "idempotency_key,input_sha256,instructions_sha256,dispatch_snapshot_sha256,config_version,engine_key,engine_version,engine_binding_fingerprint,"
            "model_ref,model_id,credential_id,credential_fingerprint,provider_id,auth_type,auth_transport,status,accepted_at,deadline_at,fence,completed_at) "
            "VALUES (UUID(),%s,%s,%s,%s,1,%s,1,%s,%s,%s,%s,1,%s,1,%s,%s,'glm-5.3',%s,%s,'zhipu','api_key','bearer',%s,"
            "UTC_TIMESTAMP(6)-INTERVAL %s SECOND,UTC_TIMESTAMP(6)+INTERVAL %s SECOND,1,%s)"
        )
        common = (agent_id, project_id, actor, event_id, binding_id)
        for number in range(80):
            dispatching = number < 2
            await sql(
                insert_run,
                (*common, f"quota-old-{number}", b"a" * 32, b"b" * 32, b"c" * 32, engine_key, "0" * 64,
                 model_ref, credential_id, "0" * 64, "DISPATCHING" if dispatching else "UNKNOWN",
                 7200, 30 if dispatching else 0, None),
            )
        for number in range(8):
            await sql(
                insert_run,
                (*common, f"quota-live-{number}", b"d" * 32, b"e" * 32, b"f" * 32, engine_key, "0" * 64,
                 model_ref, credential_id, "0" * 64, "DISPATCHING" if number < 2 else "UNKNOWN",
                 60, 30 if number < 2 else 0, None),
            )
        queries = (
            ("idx_office_agent_run_actor_window", "SELECT COUNT(*) FROM office_agent_run WHERE requested_by=%s AND accepted_at>=UTC_TIMESTAMP(6)-INTERVAL 3600 SECOND", (actor,)),
            ("idx_office_agent_run_project_window", "SELECT COUNT(*) FROM office_agent_run WHERE project_id=%s AND accepted_at>=UTC_TIMESTAMP(6)-INTERVAL 3600 SECOND", (project_id,)),
            ("idx_office_agent_run_actor_concurrency", "SELECT COUNT(*) FROM office_agent_run WHERE requested_by=%s AND status='DISPATCHING' AND deadline_at>=UTC_TIMESTAMP(6)", (actor,)),
            ("idx_office_agent_run_project_concurrency", "SELECT COUNT(*) FROM office_agent_run WHERE project_id=%s AND status='DISPATCHING' AND deadline_at>=UTC_TIMESTAMP(6)", (project_id,)),
        )
        for expected, statement, params in queries:
            plan = (await sql("EXPLAIN " + statement, params, True))[0]
            assert plan[5] == expected

    client.portal.call(exercise)


def test_agent_selector_queries_use_ordered_indexes_on_real_database(client) -> None:
    """Bias the optimizer with skewed rows, then assert its unhinted real plans."""
    from tests.identidades import crear_usuario, sql

    async def exercise() -> None:
        user_id, _ = await crear_usuario()
        project_id = await sql(
            "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
            ("selector-index-" + uuid.uuid4().hex,),
        )
        await sql(
            "INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
            "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project_id,),
        )
        for number in range(48):
            await sql(
                "INSERT INTO office_agent (project_id,agent_key,display_name,instructions,data_classification,created_by) "
                "VALUES (%s,%s,'Selector','x','INTERNAL_CONFIDENTIAL',%s)",
                (project_id, f"selector-{number:02d}", user_id),
            )

        model_ref, credential_id = (await sql(
            "SELECT m.id,c.id FROM model m JOIN credential c ON c.provider_id=m.provider_id AND c.state='active' "
            "WHERE m.provider_id='zhipu' AND m.model_id='glm-5.3' LIMIT 1", (), True,
        ))[0]
        for number in range(48):
            await sql(
                "INSERT INTO office_engine (engine_key,display_name_key,model_ref,credential_id,transport,allowed_data_classifications,binding_fingerprint,enabled,activation_epoch,version) "
                "VALUES (%s,'Selector',%s,%s,'http_openai_compat',JSON_ARRAY('INTERNAL_CONFIDENTIAL'),%s,FALSE,0,1)",
                ("selector-disabled-" + uuid.uuid4().hex, model_ref, credential_id, "0" * 64),
            )
        engine_key = "selector-enabled-" + uuid.uuid4().hex
        await sql(
            "INSERT INTO office_engine (engine_key,display_name_key,model_ref,credential_id,transport,allowed_data_classifications,binding_fingerprint,enabled,activation_epoch,version) "
            "VALUES (%s,'Selector',%s,%s,'http_openai_compat',JSON_ARRAY('INTERNAL_CONFIDENTIAL'),%s,TRUE,1,1)",
            (engine_key, model_ref, credential_id, "0" * 64),
        )

        agent_plan = (await sql(
            "EXPLAIN SELECT agent_id FROM office_agent WHERE project_id=%s ORDER BY agent_id", (project_id,), True,
        ))[0]
        engine_plan_rows = await sql(
            "EXPLAIN SELECT e.engine_key,e.display_name_key,m.model_id,e.allowed_data_classifications "
            "FROM office_engine e JOIN model m ON m.id=e.model_ref WHERE e.enabled=TRUE ORDER BY e.engine_key", (), True,
        )
        engine_plan = next(row for row in engine_plan_rows if row[2] == "e")
        assert agent_plan[5] == "idx_office_agent_project_list"
        assert engine_plan[5] == "idx_office_engine_enabled_key"
        assert "filesort" not in str(agent_plan[9]).lower()
        assert "filesort" not in str(engine_plan[9]).lower()

    client.portal.call(exercise)


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


def test_agent_selector_indexes_are_part_of_the_attested_schema() -> None:
    from virtual_office import migrations

    assert migrations._AGENT_RUNTIME_INDEXES["office_agent"]["idx_office_agent_project_list"] == (("project_id", "agent_id"), False)
    assert migrations._AGENT_RUNTIME_INDEXES["office_engine"]["idx_office_engine_enabled_key"] == (("enabled", "engine_key"), False)


def test_agent_binding_allows_only_one_active_binding_per_agent(client) -> None:
    from tests.identidades import sql
    from virtual_office import migrations

    assert "active_agent_id BIGINT UNSIGNED GENERATED ALWAYS AS (IF(active,agent_id,NULL)) STORED" in "\n".join(migrations.OFFICE_AGENT_DDL)
    assert "uq_office_agent_binding_active_agent" in migrations._AGENT_RUNTIME_INDEXES["office_agent_binding"]

    async def exercise() -> None:
        await sql(
            "CREATE TEMPORARY TABLE office_agent_binding_uniqueness_probe ("
            "agent_id BIGINT UNSIGNED NOT NULL,active BOOLEAN NOT NULL,"
            "active_agent_id BIGINT UNSIGNED GENERATED ALWAYS AS (IF(active,agent_id,NULL)) STORED,"
            "UNIQUE KEY uq_active_agent (active_agent_id))"
        )
        await sql("INSERT INTO office_agent_binding_uniqueness_probe (agent_id,active) VALUES (7,FALSE),(7,FALSE),(7,TRUE)")
        with pytest.raises((aiomysql.IntegrityError, aiomysql.OperationalError)):
            await sql("INSERT INTO office_agent_binding_uniqueness_probe (agent_id,active) VALUES (7,TRUE)")

    client.portal.call(exercise)


def test_agent_run_output_states_have_exact_terminal_shape(client) -> None:
    from tests.identidades import sql
    from virtual_office import migrations

    expected = migrations._AGENT_RUNTIME_CHECK_CLAUSES["chk_office_agent_run_output"]
    assert "'DISPATCHING' and `output_text` is null and `completed_at` is null" in expected
    assert "'UNKNOWN' and `output_text` is null and `completed_at` is not null" in expected

    async def exercise() -> None:
        await sql(
            "CREATE TEMPORARY TABLE office_agent_run_output_probe ("
            "status ENUM('DISPATCHING','SUCCEEDED','UNKNOWN') NOT NULL,output_text MEDIUMTEXT NULL,completed_at DATETIME(6) NULL,"
            "CONSTRAINT chk_output CHECK ((status='SUCCEEDED' AND output_text IS NOT NULL AND completed_at IS NOT NULL) OR "
            "(status='DISPATCHING' AND output_text IS NULL AND completed_at IS NULL) OR "
            "(status='UNKNOWN' AND output_text IS NULL AND completed_at IS NOT NULL)))"
        )
        await sql(
            "INSERT INTO office_agent_run_output_probe (status,output_text,completed_at) VALUES "
            "('SUCCEEDED','ok',UTC_TIMESTAMP(6)),('DISPATCHING',NULL,NULL),('UNKNOWN',NULL,UTC_TIMESTAMP(6))"
        )
        with pytest.raises(aiomysql.OperationalError):
            await sql("INSERT INTO office_agent_run_output_probe (status,output_text,completed_at) VALUES ('DISPATCHING',NULL,UTC_TIMESTAMP(6))")
        with pytest.raises(aiomysql.OperationalError):
            await sql("INSERT INTO office_agent_run_output_probe (status,output_text,completed_at) VALUES ('UNKNOWN',NULL,NULL)")

    client.portal.call(exercise)


def test_engine_binding_v2_includes_the_exact_credential_identity() -> None:
    from virtual_office.engine_resolver import engine_binding_fingerprint

    common = dict(
        engine_key="glm", model_ref=7, transport="http_openai_compat",
        allowed_data_classifications=frozenset({"INTERNAL_CONFIDENTIAL"}),
        provider_fingerprint="a" * 64, model_fingerprint="b" * 64,
        credential_fingerprint="c" * 64, engine_enabled=1, activation_epoch=1, version=1,
    )
    assert engine_binding_fingerprint(**common, credential_id=11) != engine_binding_fingerprint(**common, credential_id=12)


def test_activation_locks_provider_then_credential_then_engine() -> None:
    source = (Path(__file__).parents[1] / "virtual_office" / "engine_activation_cli.py").read_text(encoding="utf-8")

    provider_lock = source.index("auth_type='api_key' FOR UPDATE")
    model_lock = source.index("model_id=%s AND status='available' FOR UPDATE")
    credential_lock = source.index("WHERE provider_id=%s AND state='active' FOR UPDATE")
    engine_lock = source.index("FROM office_engine WHERE engine_key=%s FOR UPDATE")
    assert provider_lock < model_lock < credential_lock < engine_lock


def test_resolver_exposes_a_pinned_credential_loader_not_a_provider_lookup() -> None:
    source = (Path(__file__).parents[1] / "virtual_office" / "engine_resolver.py").read_text(encoding="utf-8")

    for field in ("credential_id: int", "credential_fingerprint: str", "engine_binding_fingerprint: str",
                  "provider_auth_type: str", "provider_api_key_transport: str"):
        assert field in source
    assert "async def load_pinned_office_credential_in_cursor" in source
    assert "WHERE id=%s AND provider_id=%s AND state='active'" in source
