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


def test_thot_existe_como_motor_y_completa_las_capabilities_pendientes(client):
    """R4 Task 8 -- criterio de aceptacion decisivo: thot se da de alta
    solo por dato (INSERT en _seed_thot_motor), completando las 2 filas de
    capability_motor que Task 1 dejo excluidas a proposito porque el motor
    todavia no existia."""
    rows = client.portal.call(
        _fetch_all, "SELECT transport FROM motor WHERE `key`='thot'",
    )
    assert len(rows) == 1, rows
    assert rows[0][0] == "http_openai_compat"

    for cap in ("validate_consistency", "critique"):
        rows = client.portal.call(
            _fetch_all,
            "SELECT motor_key FROM capability_motor WHERE capability_key=%s ORDER BY priority",
            (cap,),
        )
        assert [r[0] for r in rows] == ["thot", "ada"], (cap, rows)


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
    gana), portado a priority 0/1. Task 4: jax_local agregado con priority 2
    (tercer intento, despues de kimi/ada)."""
    rows = client.portal.call(
        _fetch_all,
        "SELECT motor_key, priority FROM capability_motor WHERE capability_key = 'generate' ORDER BY priority",
    )
    assert [r[0] for r in rows] == ["kimi", "ada", "jax_local"]


async def _count_capability_motor():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM capability_motor")
            row = await cur.fetchone()
            return row[0]


def test_capability_motor_seed_count_is_22(client):
    """Verify the _CAPABILITY_MOTOR_SEED + Task 4 (jax_local) + Task 8
    (thot) produces exactly 22 rows:
    6 capabilities with 1 motor + 2 capabilities with 2 motors (Task 8
    agregó thot a validate_consistency/critique, antes 1 motor cada una)
    + 4 capabilities with 3 motors = 6 + 4 + 12 = 22 total.
    Capabilities with 1 motor: code_swarm, refactor, architecture_review,
    bug_hunt, pipeline_analysis, implementation.
    Capabilities with 2 motors: validate_consistency, critique (thot, ada).
    Capabilities with 3 motors (Task 4 agregó jax_local con priority 2):
    generate, reason, design, reconcile."""
    count = client.portal.call(_count_capability_motor)
    assert count == 22, f"Expected 22 capability_motor rows, got {count}"


def test_seed_jax_local_como_motor_ollama(client):
    rows = client.portal.call(
        _fetch_all,
        "SELECT transport, max_tokens FROM motor WHERE `key`='jax_local'",
    )
    assert len(rows) == 1, rows
    assert rows[0][0] == "ollama"


def test_seed_provider_ollama_base_url_incluye_v1(client):
    """Ollama expone /v1/chat/completions (OpenAI-compatible) -- confirmado
    en vivo. provider.base_url tenía 'http://localhost:11434' sin /v1
    (ningún código lo consumía todavía); ahora sí, worker.py lo usa."""
    rows = client.portal.call(
        _fetch_all, "SELECT base_url FROM provider WHERE id='ollama'",
    )
    assert rows[0][0] == "http://localhost:11434/v1", rows


def test_seed_code_swarm_no_incluye_jax_local(client):
    """jax_local compite por capabilities de razonamiento/generación
    (Task 4), no por code_swarm (alto riesgo, gateado humano) -- decisión
    de dato, documentada en el spec §7, no de código."""
    rows = client.portal.call(
        _fetch_all,
        "SELECT motor_key FROM capability_motor WHERE capability_key='code_swarm'",
    )
    assert "jax_local" not in [r[0] for r in rows], rows
