"""Migración de las tablas de shadow validation (REFORMAS-v3 Fase 2
Sub-proyecto 2). Corre contra jax_memory_test (ver conftest.py)."""


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


def test_shadow_messages_table_has_expected_columns(client):
    cols = client.portal.call(_get_columns, "shadow_messages")
    expected = {
        "conv_uuid", "shadow_message_id", "facet", "contract_parsed",
        "degradation_reason", "has_claim", "has_analysis", "has_judgment",
        "queued_at", "validated_at",
    }
    assert expected.issubset(cols)


def test_shadow_claim_verdicts_table_has_expected_columns(client):
    cols = client.portal.call(_get_columns, "shadow_claim_verdicts")
    expected = {"conv_uuid", "shadow_message_id", "predicate", "status", "detail", "args"}
    assert expected.issubset(cols)


def test_shadow_vocab_hits_table_has_expected_columns(client):
    cols = client.portal.call(_get_columns, "shadow_vocab_hits")
    expected = {"conv_uuid", "shadow_message_id", "channel", "term", "category"}
    assert expected.issubset(cols)


async def _column_type(table_name, column_name):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DATA_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                (table_name, column_name),
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def _has_json_valid_check(table_name, column_name):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
                "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (table_name,),
            )
            rows = await cur.fetchall()
            needle = f"json_valid(`{column_name}`)"
            return any(needle in r[0] for r in rows)


def test_degradation_reason_is_text_not_varchar(client):
    dtype = client.portal.call(_column_type, "shadow_messages", "degradation_reason")
    assert dtype == "text"


def test_args_is_json_native_not_text(client):
    # MariaDB (unlike MySQL) has no distinct binary JSON storage type: the
    # `JSON` column keyword is an alias for LONGTEXT plus an automatic
    # CHECK (json_valid(...)) constraint, so DATA_TYPE reports "longtext"
    # even for a column declared as JSON. Verifying the json_valid CHECK
    # constraint is what actually distinguishes a real `args JSON` column
    # from a plain `args TEXT` column (which has no such constraint) on
    # this engine (MariaDB 12.3.2, verified via SELECT VERSION()).
    dtype = client.portal.call(_column_type, "shadow_claim_verdicts", "args")
    assert dtype == "longtext"
    assert client.portal.call(_has_json_valid_check, "shadow_claim_verdicts", "args")
