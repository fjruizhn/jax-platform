"""Migración de las tablas motor/capability/capability_motor (R4 — motor
desacoplado de faceta). Corre contra jax_memory_test (ver conftest.py)."""


async def _table_columns(cur, table_name):
    await cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table_name,),
    )
    rows = await cur.fetchall()
    return {r[0] for r in rows}


async def _get_columns(table_name):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            return await _table_columns(cur, table_name)


def test_motor_table_has_expected_columns(client):
    cols = client.portal.call(_get_columns, "motor")
    expected = {
        "key", "model_ref", "transport", "max_tokens",
        "default_timeout_seconds", "supports_reasoning",
        "reasoning_default_visibility", "sandbox_only", "status",
    }
    assert expected.issubset(cols)


def test_capability_table_has_expected_columns(client):
    cols = client.portal.call(_get_columns, "capability")
    expected = {
        "key", "risk_level", "sandbox_only", "requires_human_gate",
        "max_execution_minutes", "max_recursion_depth", "output_schema",
        "fallback_motor", "fallback_mode", "allowed_callers", "forbidden_paths",
    }
    assert expected.issubset(cols)


def test_capability_motor_table_has_expected_columns(client):
    cols = client.portal.call(_get_columns, "capability_motor")
    expected = {"capability_key", "motor_key", "priority"}
    assert expected.issubset(cols)


async def _fetch_all(query, params=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, params)
            return await cur.fetchall()


def test_seed_kimi_y_ada_como_motor(client):
    rows = client.portal.call(
        _fetch_all,
        "SELECT `key`, transport, max_tokens, sandbox_only FROM motor WHERE `key` IN ('kimi','ada')",
    )
    by_key = {r[0]: r for r in rows}
    assert set(by_key) == {"kimi", "ada"}
    assert by_key["kimi"][1] == "http_openai_compat"
    assert by_key["kimi"][2] == 8000
    assert by_key["ada"][1] == "http_openai_compat"


def test_seed_capability_motor_no_referencia_motores_inexistentes(client):
    """thot no es motor todavia (Task 8 lo crea) -- las filas de
    validate_consistency/critique que en config.toml apuntaban a "thot"
    deben quedar excluidas del seed, no romper la FK."""
    rows = client.portal.call(
        _fetch_all,
        "SELECT capability_key, motor_key FROM capability_motor WHERE motor_key = 'thot'",
    )
    assert len(rows) == 0


def test_seed_code_swarm_apunta_a_kimi_con_fallback_ada(client):
    rows = client.portal.call(
        _fetch_all,
        "SELECT motor_key, priority FROM capability_motor WHERE capability_key = 'code_swarm' ORDER BY priority",
    )
    assert [r[0] for r in rows] == ["kimi"]
    cap = client.portal.call(
        _fetch_all,
        "SELECT fallback_motor, fallback_mode, requires_human_gate FROM capability WHERE `key`='code_swarm'",
    )
    assert cap[0] == ("ada", "manual_only", 1)


def test_seed_generate_tiene_dos_motores_en_orden_kimi_luego_ada(client):
    """[capabilities.generate].allowed_motors = ["kimi", "ada"] en config.toml
    -- el orden es el criterio de _resolve_motor() (el primero habilitado
    gana), portado a priority 0/1."""
    rows = client.portal.call(
        _fetch_all,
        "SELECT motor_key, priority FROM capability_motor WHERE capability_key = 'generate' ORDER BY priority",
    )
    assert [r[0] for r in rows] == ["kimi", "ada"]


async def _count_capability_motor():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM capability_motor")
            row = await cur.fetchone()
            return row[0]


def test_capability_motor_seed_count_is_16(client):
    """Verify the _CAPABILITY_MOTOR_SEED produces exactly 16 rows:
    8 capabilities with 1 motor + 4 capabilities with 2 motors = 16 total.
    Capabilities with 1 motor: code_swarm, refactor, architecture_review,
    bug_hunt, pipeline_analysis, implementation, validate_consistency, critique.
    Capabilities with 2 motors: generate, reason, design, reconcile."""
    count = client.portal.call(_count_capability_motor)
    assert count == 16, f"Expected 16 capability_motor rows, got {count}"
