"""_reclassify_provenance_mismatch (db/migrations.py, 2026-09-03) — repo jax
partió Verdict.status.PROVENANCE_MISMATCH en POINTER_MISMATCH (el hecho SÍ
está en el snapshot, se citó otro puntero) y FACT_NOT_IN_SNAPSHOT (ninguna
entrada del snapshot respalda el hecho afirmado). Esta migración reclasifica
las filas viejas -- RECLASIFICA, no borra -- contra esa misma condición
mecánica, vía JSON_CONTAINS.

Corre contra jax_memory_test (fixture `client`). Con JAX_CI_NO_DB=1 se salta
por la Regla 1 de conftest.py.
"""
from __future__ import annotations

import json
import uuid


async def _run_reclassify_scenario(run_count):
    from db.connection import get_pool
    from db.migrations import _reclassify_provenance_mismatch

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            conv_uuid = str(uuid.uuid4())
            smid = str(uuid.uuid4())
            # snapshot a mano, con la MISMA forma que build_snapshot() (repo
            # jax, policy/governance/grounding.py): {"capabilities": [...]}.
            snapshot = {
                "capabilities": [
                    {"name": "write_file", "mode": "mutating"},
                    {"name": "read_file", "mode": "read_only"},
                ]
            }
            await cur.execute(
                "INSERT INTO shadow_messages "
                "(conv_uuid, shadow_message_id, facet, grounding_snapshot, grounding_snapshot_sha256) "
                "VALUES (%s, %s, %s, %s, %s)",
                (conv_uuid, smid, "jekyll", json.dumps(snapshot), "a" * 64),
            )
            # Fila 1: los args SÍ están en el snapshot (write_file/mutating es
            # verdadero) -> tras reclasificar debe quedar POINTER_MISMATCH.
            await cur.execute(
                "INSERT INTO shadow_claim_verdicts "
                "(conv_uuid, shadow_message_id, predicate, status, detail, args, authority) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (conv_uuid, smid, "CAPABILITY_AVAILABLE", "PROVENANCE_MISMATCH",
                 "citó otro puntero", json.dumps({"name": "write_file", "mode": "mutating"}),
                 "INFERIDO"),
            )
            # Fila 2: los args NO están en ninguna entrada del snapshot
            # (write_file es mutating, nunca read_only) -> tras reclasificar
            # debe quedar FACT_NOT_IN_SNAPSHOT.
            await cur.execute(
                "INSERT INTO shadow_claim_verdicts "
                "(conv_uuid, shadow_message_id, predicate, status, detail, args, authority) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (conv_uuid, smid, "CAPABILITY_AVAILABLE", "PROVENANCE_MISMATCH",
                 "inventó el hecho", json.dumps({"name": "write_file", "mode": "read_only"}),
                 "INFERIDO"),
            )
            await conn.commit()

            rowcounts = []
            for _ in range(run_count):
                await _reclassify_provenance_mismatch(cur)
                rowcounts.append(cur.rowcount)
                await conn.commit()

            await cur.execute(
                "SELECT detail, status FROM shadow_claim_verdicts "
                "WHERE shadow_message_id = %s ORDER BY id",
                (smid,),
            )
            return await cur.fetchall(), rowcounts


def test_reclassifies_pointer_mismatch_and_fact_not_in_snapshot_correctly(client):
    (rows, rowcounts) = client.portal.call(_run_reclassify_scenario, 1)
    by_detail = {detail: status for detail, status in rows}
    assert by_detail["citó otro puntero"] == "POINTER_MISMATCH"
    assert by_detail["inventó el hecho"] == "FACT_NOT_IN_SNAPSHOT"
    # ninguna fila queda en el status retirado.
    assert "PROVENANCE_MISMATCH" not in by_detail.values()
    assert rowcounts == [2]  # las dos filas PROVENANCE_MISMATCH, reclasificadas


def test_second_run_is_a_no_op(client):
    # la primera corrida (adentro del helper) ya reclasificó las dos filas;
    # la segunda no encuentra ninguna PROVENANCE_MISMATCH que tocar. rowcount
    # de la segunda corrida en 0 es la prueba directa de "no cambia nada" --
    # más fuerte que solo releer el resultado final, que idéntico igual
    # saldría si la segunda corrida tocara las filas sin alterar su valor.
    (rows, rowcounts) = client.portal.call(_run_reclassify_scenario, 2)
    assert rowcounts == [2, 0]
    by_detail = {detail: status for detail, status in rows}
    assert by_detail["citó otro puntero"] == "POINTER_MISMATCH"
    assert by_detail["inventó el hecho"] == "FACT_NOT_IN_SNAPSHOT"
