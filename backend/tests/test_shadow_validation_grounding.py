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


def test_migration_adds_contract_raw_column(client):
    sm = client.portal.call(_columns, "shadow_messages")
    assert sm["contract_raw"] == "longtext"


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


async def _fetch_contract_raw(shadow_message_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT contract_raw FROM shadow_messages WHERE shadow_message_id = %s",
                (shadow_message_id,),
            )
            row = await cur.fetchone()
            return row[0]


def _snapshot():
    ctx, _, _ = governance_context.validation_context()
    return governance_grounding.build_snapshot(ctx)


def _pointer_of(snap, name):
    return next(e.pointer for e in snap.entries if e.args["name"] == name)


def _contract(claims, raw_text="..."):
    return ContractResult(
        contract_parsed=True, claims=claims, analysis="a", judgment=None,
        degradation_reason=None, raw_text=raw_text,
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


def test_forged_citation_is_fact_not_in_snapshot(client):
    # write_file es mutating en el snapshot real (_pointer_of lo confirma
    # abajo) -- el claim afirma que es read_only, un hecho que NINGUNA
    # entrada del snapshot respalda, sin importar qué línea citó. Condición
    # mecánica de accredit()/mismatch() (repo jax, policy/governance/
    # grounding.py, 2026-09-03): no hay entrada con predicate=
    # CAPABILITY_AVAILABLE y args={name: write_file, mode: read_only} ->
    # FACT_NOT_IN_SNAPSHOT, el más grave de los dos (inventó el hecho, no
    # solo la cita).
    snap = _snapshot()
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "read_only"},
                                    "evidence_pointer": _pointer_of(snap, "write_file")}]), snap)
    (_, status, authority, _, _, _), = client.portal.call(_fetch_verdicts, smid)
    assert (status, authority) == ("FACT_NOT_IN_SNAPSHOT", "INFERIDO")


def test_pointer_mismatch_end_to_end_names_the_correct_pointer(client):
    # El caso que reprodujo el hallazgo de producción (repo jax, commit
    # 4617df6): el hecho afirmado (write_file/mutating) SÍ existe en el
    # snapshot -- solo el puntero citado está mal (apunta a otra entrada).
    # Condición mecánica: existe una entrada con predicate=
    # CAPABILITY_AVAILABLE y args={name: write_file, mode: mutating} ->
    # POINTER_MISMATCH, y el detail nombra el puntero que SÍ respalda el
    # claim (no el que se citó).
    snap = _snapshot()
    real_pointer = _pointer_of(snap, "write_file")
    wrong_pointer = _pointer_of(snap, "read_file")
    assert wrong_pointer != real_pointer
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "mutating"},
                                    "evidence_pointer": wrong_pointer}]), snap)
    (_, status, authority, pointer, detail, _), = client.portal.call(_fetch_verdicts, smid)
    assert (status, authority, pointer) == ("POINTER_MISMATCH", "INFERIDO", wrong_pointer)
    assert real_pointer in detail


def test_fact_not_in_snapshot_end_to_end(client):
    # write_file existe en el snapshot solo como mutating -- nunca como
    # read_only -- así que ninguna entrada respalda el hecho afirmado, sin
    # importar qué puntero se citó. Distinto del caso citado arriba
    # (test_forged_citation_is_fact_not_in_snapshot cita el puntero
    # CORRECTO de write_file): acá se cita el puntero de OTRA capability
    # (read_file) para confirmar que la condición mecánica mira los args
    # normalizados contra TODO el snapshot, no solo contra la entrada
    # citada.
    snap = _snapshot()
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "read_only"},
                                    "evidence_pointer": _pointer_of(snap, "read_file")}]), snap)
    (_, status, authority, _, _, _), = client.portal.call(_fetch_verdicts, smid)
    assert (status, authority) == ("FACT_NOT_IN_SNAPSHOT", "INFERIDO")


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
    # args={name: write_file, mode: mutating} ES el hecho verdadero del
    # snapshot (ver /capabilities/10 en _snapshot()) -- lo único roto acá es
    # el puntero (malformado/fuera de rango/sin barra inicial), así que la
    # condición mecánica encuentra la entrada real y cae en POINTER_MISMATCH,
    # no FACT_NOT_IN_SNAPSHOT.
    assert status == "POINTER_MISMATCH"
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
    # mismo hecho verdadero (write_file/mutating) que el caso de arriba --
    # el puntero está fuera de rango, pero el hecho existe -> POINTER_MISMATCH.
    assert status == "POINTER_MISMATCH"
    assert pointer == long[:100]
    assert long in detail


def test_9_1b_70000_char_ascii_pointer_clamps_detail_to_65000_bytes_without_data_too_long(client):
    huge = "/capabilities/" + "9" * 69986
    assert len(huge) == 70000
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "mutating"},
                                    "evidence_pointer": huge}]), _snapshot())
    (_, status, _, pointer, detail, _), = client.portal.call(_fetch_verdicts, smid)
    # write_file/mutating sigue siendo el hecho verdadero -- POINTER_MISMATCH.
    assert status == "POINTER_MISMATCH"
    assert pointer == huge[:100]
    assert len(detail.encode("utf-8")) <= 65000
    _, _, validated_at = client.portal.call(_fetch_message_grounding, smid)
    assert validated_at is not None


def test_9_1b_multibyte_pointer_clamps_detail_by_bytes_not_chars(client):
    # "ñ" son 2 bytes en utf8mb4: 35000 de ellos son ~70 KB en solo ~35.014
    # caracteres. Un clamp por CARACTERES (60000) NO toca esta cadena (35.014
    # << 60000) -- y aun así el INSERT revienta por bytes (TEXT = 65535
    # bytes, no caracteres: 70014 bytes solo del puntero). Medido antes de
    # este test: con 30000 "ñ" (~60014 bytes) el total queda por debajo de
    # 65535 y NO reproduce el bug (falso negativo) -- 35000 sí lo cruza con
    # margen. Este es el caso que el clamp char-based no cubría.
    huge = "/capabilities/" + "ñ" * 35000
    assert len(huge) == 35014
    smid = _run(client, _contract([{"predicate": "CAPABILITY_AVAILABLE",
                                    "args": {"name": "write_file", "mode": "mutating"},
                                    "evidence_pointer": huge}]), _snapshot())
    (_, status, _, pointer, detail, _), = client.portal.call(_fetch_verdicts, smid)
    # idem: write_file/mutating es verdadero, solo el puntero es enorme ->
    # POINTER_MISMATCH.
    assert status == "POINTER_MISMATCH"
    assert pointer == huge[:100]
    encoded = detail.encode("utf-8")
    assert len(encoded) <= 65000
    # sin punto de código partido a la mitad -- decode debe volver a andar.
    assert encoded.decode("utf-8") == detail
    _, _, validated_at = client.portal.call(_fetch_message_grounding, smid)
    assert validated_at is not None


def test_contract_raw_is_persisted_equal_to_raw_text(client):
    # contract_raw guarda el texto CRUDO tal como lo emitió el modelo -- es
    # el único lugar donde queda el bloque analysis (por qué eligió el
    # puntero que eligió), que hoy no se persiste en ningún otro lado.
    raw = '{"claims": [], "analysis": "razonamiento del modelo aquí", "judgment": null}'
    smid = _run(client, _contract([], raw_text=raw), _snapshot())
    persisted = client.portal.call(_fetch_contract_raw, smid)
    assert persisted == raw


def test_contract_raw_truncated_by_bytes_with_marker_naming_original_size(client):
    # Mismo argumento que el clamp de `detail` (test de arriba): "ñ" son 2
    # bytes en utf8mb4, así que un clamp por CARACTERES no reproduciría el
    # corte real por bytes. 40000 "ñ" son 80000 bytes -- muy por encima de
    # _RAW_COLUMN_BYTES=65000.
    huge = "ñ" * 40000
    original_bytes = len(huge.encode("utf-8"))
    assert original_bytes == 80000
    smid = _run(client, _contract([], raw_text=huge), _snapshot())
    persisted = client.portal.call(_fetch_contract_raw, smid)
    # el corte es sobre encode("utf-8")[:N].decode("utf-8", "ignore") -- sin
    # punto de código partido a la mitad (decode() lanzaría si lo estuviera).
    assert persisted.startswith("ñ" * 100)  # el contenido real sigue ahí, solo cortado
    assert persisted != huge
    # el marcador nombra explícitamente cuántos bytes tenía el original --
    # sin esto quien lea la fila no sabe que falta contenido ni cuánto.
    assert str(original_bytes) in persisted
    assert "TRUNC" in persisted.upper()


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
