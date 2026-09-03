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


import governance_context  # noqa: E402  (ya está en sys.path por conftest→main)
import grounding as governance_grounding  # noqa: E402


async def _fetch_message_grounding(shadow_message_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT grounding_snapshot, grounding_snapshot_sha256, validated_at "
                "FROM shadow_messages WHERE shadow_message_id = %s",
                (shadow_message_id,),
            )
            return await cur.fetchone()


async def _fetch_verdicts(shadow_message_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT predicate, status, authority, evidence_pointer, detail, args "
                "FROM shadow_claim_verdicts WHERE shadow_message_id = %s ORDER BY id",
                (shadow_message_id,),
            )
            return await cur.fetchall()


def _snapshot():
    ctx, _, _ = governance_context.validation_context()
    return governance_grounding.build_snapshot(ctx)


def _pointer_of(snap, name):
    return next(e.pointer for e in snap.entries if e.args["name"] == name)


def _contract(claims):
    return ContractResult(
        contract_parsed=True, claims=claims, analysis="a", judgment=None,
        degradation_reason=None, raw_text="...",
    )


def _run(client, contract, grounding_result, smid=None):
    from shadow_validation import run_shadow_validation
    smid = smid or str(uuid.uuid4())
    client.portal.call(run_shadow_validation, "conv-sp3", smid, "jekyll", contract, grounding_result)
    return smid


def test_accredited_claim_is_observado_and_valid_with_pointer_persisted(client):
    snap = _snapshot()
    ptr = _pointer_of(snap, "write_file")
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "mutating"},
                                    "evidence_pointer": ptr}]), snap)
    (predicate, status, authority, pointer, detail, args), = client.portal.call(_fetch_verdicts, smid)
    assert (predicate, status, authority, pointer) == ("CAPABILITY_AVAILABLE", "VALID", "OBSERVADO", ptr)
    snapshot_json, sha, validated_at = client.portal.call(_fetch_message_grounding, smid)
    assert sha == snap.sha256
    assert json.loads(snapshot_json) == json.loads(snap.canonical_json)
    assert validated_at is not None


def test_no_pointer_is_authority_invalid_with_null_pointer(client):
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "mutating"}}]), _snapshot())
    (_, status, authority, pointer, _, _), = client.portal.call(_fetch_verdicts, smid)
    assert (status, authority, pointer) == ("AUTHORITY_INVALID", "INFERIDO", None)


def test_forged_citation_is_provenance_mismatch(client):
    snap = _snapshot()
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "read_only"},
                                    "evidence_pointer": _pointer_of(snap, "write_file")}]), snap)
    (_, status, authority, _, _, _), = client.portal.call(_fetch_verdicts, smid)
    assert (status, authority) == ("PROVENANCE_MISMATCH", "INFERIDO")


def test_job_status_with_pointer_is_resolver_not_implemented(client):
    snap = _snapshot()
    smid = _run(client, _contract([{"predicate": "JOB_STATUS",
                                    "args": {"job_id": "1", "status": "ok"},
                                    "evidence_pointer": _pointer_of(snap, "write_file")}]), snap)
    (_, status, authority, _, _, _), = client.portal.call(_fetch_verdicts, smid)
    assert (status, authority) == ("RESOLVER_NOT_IMPLEMENTED", "INFERIDO")


def test_snapshot_error_marks_turn_ERROR_and_claims_grounding_unavailable(client):
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "mutating"},
                                    "evidence_pointer": "/capabilities/0"}]),
                governance_grounding.SnapshotError("config ilegible"))
    snapshot_json, sha, validated_at = client.portal.call(_fetch_message_grounding, smid)
    assert sha == "ERROR"
    assert json.loads(snapshot_json) == {"error": "config ilegible"}
    assert validated_at is not None
    (_, status, authority, _, detail, _), = client.portal.call(_fetch_verdicts, smid)
    assert (status, authority) == ("GROUNDING_UNAVAILABLE", "INFERIDO")
    assert "config ilegible" in detail


@pytest.mark.parametrize("bad", ["", "capabilities/0", "/capabilities/abc", "/capabilities/-1"])
def test_9_1b_malformed_pointer_completes_the_task(client, bad):
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "mutating"},
                                    "evidence_pointer": bad}]), _snapshot())
    (_, status, _, pointer, _, _), = client.portal.call(_fetch_verdicts, smid)
    assert status == "PROVENANCE_MISMATCH"
    assert pointer == bad
    _, _, validated_at = client.portal.call(_fetch_message_grounding, smid)
    assert validated_at is not None


def test_9_1b_300_char_pointer_is_truncated_to_100_with_original_in_detail(client):
    long = "/capabilities/" + "9" * 286
    assert len(long) == 300
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "mutating"},
                                    "evidence_pointer": long}]), _snapshot())
    (_, status, _, pointer, detail, _), = client.portal.call(_fetch_verdicts, smid)
    assert status == "PROVENANCE_MISMATCH"
    assert pointer == long[:100]
    assert long in detail


def test_model_declared_authority_never_enters_the_authority_column(client):
    snap = _snapshot()
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "mutating"},
                                    "authority": "EJECUTADO"}]), snap)
    (_, status, authority, _, detail, _), = client.portal.call(_fetch_verdicts, smid)
    assert authority == "INFERIDO"          # derivada por el servidor: sin puntero
    assert status == "AUTHORITY_INVALID"
    assert "EJECUTADO" in detail            # lo que mandó el modelo queda en el raw


def test_every_row_written_by_run_shadow_validation_has_non_null_sha256(client):
    # spec §9.2: NULL después de SP3 es solo legado. Con snapshot:
    smid1 = _run(client, _contract([]), _snapshot())
    # y con error:
    smid2 = _run(client, _contract([]), governance_grounding.SnapshotError("x"))
    for smid in (smid1, smid2):
        _, sha, _ = client.portal.call(_fetch_message_grounding, smid)
        assert sha is not None


def test_fifth_argument_is_mandatory(client):
    import inspect
    from shadow_validation import run_shadow_validation
    p = inspect.signature(run_shadow_validation).parameters["grounding"]
    assert p.default is inspect.Parameter.empty
