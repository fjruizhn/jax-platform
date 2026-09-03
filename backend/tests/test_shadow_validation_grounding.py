"""
Shadow validation con grounding (REFORMAS Fase 2 SP3) — spec §9.2.

Corre contra jax_memory_test (fixture `client` levanta la app y corre las
migraciones). Con JAX_CI_NO_DB=1 todo esto se salta por la Regla 1 de
conftest.py.
"""
from __future__ import annotations

import json
import uuid

import pytest

from api.chat import ContractResult


async def _columns(table):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (table,),
            )
            return {name: ctype for name, ctype in await cur.fetchall()}


def test_migration_adds_the_four_grounding_columns(client):
    sm = client.portal.call(_columns, "shadow_messages")
    assert sm["grounding_snapshot"] == "longtext"
    assert sm["grounding_snapshot_sha256"] == "char(64)"
    cv = client.portal.call(_columns, "shadow_claim_verdicts")
    assert cv["authority"] == "varchar(12)"
    assert cv["evidence_pointer"] == "varchar(100)"
