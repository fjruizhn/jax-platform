"""
La Mesa con las capabilities de la DB, de punta a punta, y el TRIPWIRE ops∩DB
(tanda A v2, 2026-09-14).

1. Un claim sobre `generate` que cita su línea de catalog_capabilities queda
   VALID/OBSERVADO en shadow_claim_verdicts, por el MISMO camino que un turno
   real (run_shadow_validation). Con el modo equivocado, FACT_NOT_IN_SNAPSHOT:
   la acreditación lo corta antes del resolver (spec v2 §3.4). Hasta la v2 las
   capabilities de la DB no se podían citar: en producción, 2 AUTHORITY_INVALID
   sobre code_swarm (medido 2026-09-14).
2. TRIPWIRE: `ops` del TOML y `capability` de la DB no comparten nombres. Un
   nombre repetido da SOURCE_CONFLICT y dos líneas del snapshot con el mismo
   hecho. Medido el 2026-09-14: 11 ops, 17 capabilities, intersección vacía.
   Mira el MISMO objeto que ven resolver y snapshot. Alcance declarado: ve lo
   que SIEMBRAN las migraciones; el despliegue lo mide contra producción.
"""
from __future__ import annotations

import shutil
import uuid

import governance_context  # primero: pone policy/governance en sys.path
import claims as governance_claims  # noqa: E402
import grounding as governance_grounding  # noqa: E402
import validator as governance_validator  # noqa: E402
from api.chat import ContractResult


def _contract(claims):
    return ContractResult(contract_parsed=True, claims=claims, analysis="a", judgment=None,
                          degradation_reason=None, raw_text="...")


async def _veredictos(shadow_message_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT predicate, status, authority, evidence_pointer FROM shadow_claim_verdicts "
                "WHERE shadow_message_id = %s ORDER BY id", (shadow_message_id,))
            return await cur.fetchall()


def _snapshot_real(client):
    governance_context.validation_context.cache_clear()
    ctx, _, _ = client.portal.call(governance_context.validation_context)
    return ctx, governance_grounding.build_snapshot(ctx)


def _correr_la_mesa(client, snap, modo):
    from shadow_validation import run_shadow_validation
    ptr = next(e.pointer for e in snap.entries
               if e.pointer.startswith("/catalog_capabilities/") and e.args["name"] == "generate")
    smid = str(uuid.uuid4())
    client.portal.call(run_shadow_validation, "conv-tanda-a", smid, "jekyll", _contract([
        {"predicate": "CAPABILITY_AVAILABLE", "args": {"name": "generate", "mode": modo}, "evidence_pointer": ptr},
    ]), snap, "test")
    return ptr, client.portal.call(_veredictos, smid)


def test_en_la_mesa_un_claim_sobre_generate_queda_valid_observado(client):
    try:
        _, snap = _snapshot_real(client)
        ptr, filas = _correr_la_mesa(client, snap, "read_only")
    finally:
        governance_context.validation_context.cache_clear()
    assert filas == (("CAPABILITY_AVAILABLE", "VALID", "OBSERVADO", ptr),)


def test_en_la_mesa_el_modo_equivocado_queda_fact_not_in_snapshot(client):
    try:
        _, snap = _snapshot_real(client)
        ptr, filas = _correr_la_mesa(client, snap, "mutating")
    finally:
        governance_context.validation_context.cache_clear()
    assert filas == (("CAPABILITY_AVAILABLE", "FACT_NOT_IN_SNAPSHOT", "INFERIDO", ptr),)


def _en_ambos(ctx):
    return sorted(n for n in ctx.ops if ctx.catalog.get_capability(n) is not None)


def test_tripwire_ops_del_toml_y_capabilities_de_la_db_no_comparten_nombres(client):
    """TRIPWIRE. Ver docstring del módulo, punto 2."""
    try:
        ctx, _ = _snapshot_real(client)
    finally:
        governance_context.validation_context.cache_clear()
    assert ctx.ops, "ops vacío: el tripwire no estaría mirando nada"
    assert ctx.catalog.get_capability("generate") is not None, (
        "catálogo sin la capability sembrada: el tripwire no estaría mirando la DB")
    assert _en_ambos(ctx) == [], (
        f"{_en_ambos(ctx)} está en ops del TOML Y en capability de la DB: el resolver "
        "de CAPABILITY_AVAILABLE va a dar SOURCE_CONFLICT. Renombrar uno de los dos.")


def test_control_el_tripwire_ve_un_nombre_repetido(client, tmp_path, monkeypatch):
    """CONTROL del TRIPWIRE: con un config.toml que agrega `[ops.generate]`, la
    intersección da ['generate'] y el resolver da SOURCE_CONFLICT."""
    destino = tmp_path / "jax"
    (destino / "las_manos").mkdir(parents=True)
    shutil.copy(governance_context.JAX_REPO / "las_manos" / "config.toml", destino / "las_manos" / "config.toml")
    with open(destino / "las_manos" / "config.toml", "a", encoding="utf-8") as f:
        f.write("\n[ops.generate]\n")
    monkeypatch.setattr(governance_context, "JAX_REPO", destino)
    governance_context.validation_context.cache_clear()
    try:
        ctx, predicates, _ = client.portal.call(governance_context.validation_context)
    finally:
        governance_context.validation_context.cache_clear()
    assert _en_ambos(ctx) == ["generate"]
    claim = governance_claims.Claim(
        predicate="CAPABILITY_AVAILABLE", args={"name": "generate", "mode": "read_only"},
        authority="OBSERVADO", provenance_ref="test", evidence_pointer="test", scope="mesa_web")
    assert governance_validator.validate(claim, predicates, ctx).status == "SOURCE_CONFLICT"
