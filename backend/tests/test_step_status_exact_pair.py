"""STEP_STATUS crosses the exact JAX F2-B/F2-C/F2-D pair without aliases."""
import asyncio
from datetime import datetime, timezone
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.governed_chat import F2C_EXACT_PAIR_COMPATIBILITY, project_sealed_envelope
from jax_engine import status_resolution
from jax_engine.status_resolution import JacobsStepStatusResolver, RuntimeStatusBridgeUnavailable


NOW = datetime(2026, 10, 3, 15, tzinfo=timezone.utc)


def _core(monkeypatch):
    monkeypatch.setenv("JAX_REPO_PATH", os.environ["JAX_REPO_PATH"])
    monkeypatch.setenv("JAX_DB_HOST", "mariadb.test")
    monkeypatch.setenv("JAX_DB_PORT", "3308")
    monkeypatch.setenv("JAX_DB_NAME", "jax_memory_test")
    bridge = status_resolution._jax_runtime_status_bridge()
    assert Path(bridge.__file__).resolve().is_relative_to(Path(os.environ["JAX_REPO_PATH"]).resolve())
    assert bridge.RUNTIME_STATUS_API_VERSION == "f2-e.runtime-status.4"
    assert F2C_EXACT_PAIR_COMPATIBILITY == (
        "f2-c.renderer.3", "f2-c.domain.7", frozenset({"f2-c.1"}))
    return bridge, importlib.import_module("policy.governance.resolution")


def _scope(core):
    return core.ResponseScope("test", "tenant-a", None, "user-a", "service:platform",
        "user:user-a", "jacobs", "step-request", "step-trace")


def _platform_source_configuration():
    return {
        "FACET_RUNTIME_STATUS": {"state_contract": "JAXEngineState.FacetState",
            "status_field": "status", "observed_at_field": "resolver_read_time",
            "allowed_statuses": ["idle", "thinking", "error", "offline"]},
        "ENGINE_STATUS": {"endpoint_sha256": "sha256:" + "e" * 64, "method": "GET",
            "path": "/health", "timeout_seconds": 5, "poll_interval_seconds": 30,
            "success_status_code": 200},
    }


def test_exact_pair_step_status_receipt_render_and_f2d_unit(monkeypatch):
    bridge, resolution = _core(monkeypatch)
    from jacobs import store as jacobs_store
    from policy.governance.governed_domain import GovernedDomainSpecification
    from policy.governance.governed_renderer import GovernedDomainRegistry, RenderContext
    from policy.governance.loaders import load_templates
    from policy.governance.response import (
        ClaimDisposition, ClaimRecord, ContentBlock, ContentBlockKind, ContractState,
        EpistemicStatus, ExistenceState, GovernanceReceipt, GovernedResponseCandidate,
        ReferenceRef, ReferenceType, SourceClass, TemporalClass, TemplateContract,
        _seal_candidate_for_server,
    )
    from policy.governance.resolution import ReferenceLookupRecord

    snapshot = SimpleNamespace(step_id="step-1", status="blocked_human_gate", pipeline_id="pipe-1",
        tenant_id="tenant-a", user_id="user-a", owner_ack_at=1.0,
        pipeline_status="running", observed_at=NOW)
    monkeypatch.setattr(jacobs_store, "step_status_snapshot", AsyncMock(return_value=snapshot))
    scope = _scope(bridge)
    args = {"step_id": "step-1", "status": "blocked_human_gate"}
    evidence = asyncio.run(JacobsStepStatusResolver().evidence(args, scope))
    assert evidence.adapter_kind is resolution.AdapterKind.JACOBS_STEP_STATUS
    assert evidence.observation.result == args
    assert evidence.observation.observed_at == NOW

    registry = bridge.build_runtime_status_registry(scope,
        authenticator=resolution.ReceiptAuthenticator.for_testing(b"sr2-platform-pair-key-012345678901"),
        platform_source_configuration=_platform_source_configuration())
    resolution_receipt = registry.resolve("STEP_STATUS", args, scope,
        validation_time=NOW, runtime_status_evidence=evidence)
    assert resolution_receipt.status is resolution.ResolutionStatus.RESOLVED
    receipt_ref = ReferenceRef("step-resolution-ref", ReferenceType.RESOLUTION_RECEIPT,
        "test://step/receipt", "step-receipt-identity", resolution_receipt.receipt_id,
        scope.scope_digest, TemporalClass.CURRENT, ExistenceState.PRESENT)
    claim = ClaimRecord("step-claim", "STEP_STATUS", args, scope,
        SourceClass.CURRENT_SOURCE, EpistemicStatus.CURRENT_OBSERVATION,
        resolution_receipt_ref=receipt_ref.ref_id,
        disposition=ClaimDisposition.ASSERTABLE,
        template_contract=TemplateContract("STEP_STATUS", "f2-e.runtime-status.4", "es"))
    candidate = GovernedResponseCandidate("f2-c.1", "step-response", scope.request_id,
        scope.trace_id, scope, "jacobs", (),
        (ContentBlock(ContentBlockKind.CLAIM_REF_BLOCK, claim_refs=(claim.claim_id,)),),
        (claim,), (receipt_ref,))
    governance_receipt = GovernanceReceipt("test-policy", "test-vocabulary",
        registry.snapshot_digest, "test-validator", "f2-c.1")
    envelope = _seal_candidate_for_server(candidate, contract_state=ContractState.VALID,
        governance_receipt=governance_receipt)
    lookup = ReferenceLookupRecord(receipt_ref.ref_id, receipt_ref.ref_type,
        receipt_ref.canonical_locator, receipt_ref.immutable_identity,
        receipt_ref.revision_or_digest, receipt_ref.scope_digest,
        receipt_ref.temporal_class, receipt_ref.existence_state, True)
    templates = load_templates()
    context = RenderContext(registry, {receipt_ref.ref_id: resolution_receipt},
        {("STEP_STATUS", "f2-e.runtime-status.4", "es"): templates["STEP_STATUS"].template},
        {}, GovernedDomainRegistry(specification=GovernedDomainSpecification()),
        reference_validator=lambda _ref, _scope: True,
        now=lambda: NOW,
        receipt_reference_resolver=lambda _ref, _scope: lookup)

    projection = project_sealed_envelope(envelope, context)
    assert projection.contract_state == "VALID"
    assert projection.text == "Jacobs registra actualmente que el paso step-1 está blocked_human_gate."
    assert projection.transport_unit is not None
    assert projection.transport_unit.durable_projection()["effective_output_digest"]


def test_platform_step_bridge_rejects_aliases_and_unrecognized_status(monkeypatch):
    bridge, core = _core(monkeypatch)
    from jacobs import store as jacobs_store
    snapshot = SimpleNamespace(step_id="step-1", status="interrupted", pipeline_id="pipe-1",
        tenant_id="tenant-a", user_id="user-a", owner_ack_at=1.0,
        pipeline_status="running", observed_at=NOW)
    monkeypatch.setattr(jacobs_store, "step_status_snapshot", AsyncMock(return_value=snapshot))
    resolver = JacobsStepStatusResolver()
    scope = _scope(bridge)

    assert asyncio.run(resolver.evidence({"step_id": "step-1", "status": "waiting_gate"}, scope)) is None
    assert asyncio.run(resolver.evidence({"step_id": "step-1", "status": "interrupted"}, scope)) is None


def test_platform_step_bridge_rejects_unexpected_runtime_api_version(monkeypatch):
    _core(monkeypatch)
    runtime = importlib.import_module("policy.governance.runtime_status")
    with monkeypatch.context() as patched:
        patched.setattr(runtime, "RUNTIME_STATUS_API_VERSION", "f2-e.runtime-status.3")
        with pytest.raises(RuntimeStatusBridgeUnavailable, match="unsupported"):
            status_resolution._jax_runtime_status_bridge()


@pytest.mark.parametrize("module_name", ("jacobs.store", "jacobs.models"))
def test_platform_step_bridge_rejects_preloaded_jacobs_from_other_checkout(monkeypatch, module_name):
    """Mezcla de checkouts: un jacobs.store/models precargado desde OTRO JAX
    (p.ej. el 2b0c163 de los jobs genericos queda en sys.modules) se rechaza
    fail-closed; nunca se delega al store viejo."""
    import sys
    import types
    bridge, _ = _core(monkeypatch)
    fake = types.ModuleType(module_name)
    fake.__file__ = "/opt/jax-2b0c163/jacobs/" + module_name.split(".", 1)[1] + ".py"
    monkeypatch.setitem(sys.modules, module_name, fake)
    with pytest.raises(RuntimeStatusBridgeUnavailable, match="outside configured JAX"):
        asyncio.run(JacobsStepStatusResolver().evidence(
            {"step_id": "step-1", "status": "running"}, _scope(bridge)))


@pytest.mark.skipif(os.environ.get("SR2_STEP_STATUS_DB_TEST") != "1",
    reason="requires the isolated SR2 exact-pair MariaDB job")
def test_exact_pair_reads_real_canonical_step_owner_join_and_index_plan(monkeypatch):
    import aiomysql
    from jacobs import store as jacobs_store

    bridge, resolution = _core(monkeypatch)
    assert os.environ.get("JAX_DB_NAME") == "jax_memory_test"
    connection_settings = {
        "host": os.environ["JAX_DB_HOST"], "port": int(os.environ["JAX_DB_PORT"]),
        "user": os.environ["JAX_DB_USER"], "password": os.environ["JAX_DB_PASSWORD"],
        "db": "jax_memory_test", "autocommit": True,
    }
    pipeline_id, step_id = "sr2-step-it-pipeline", "sr2-step-it-step"

    async def seed_and_explain():
        conn = await aiomysql.connect(**connection_settings)
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""CREATE TABLE IF NOT EXISTS jacobs_pipelines (
                    pipeline_id VARCHAR(96) PRIMARY KEY, tenant_id VARCHAR(96),
                    user_id VARCHAR(96), owner_ack_at DOUBLE NULL, status VARCHAR(40) NOT NULL
                ) ENGINE=InnoDB""")
                await cur.execute("""CREATE TABLE IF NOT EXISTS jacobs_steps (
                    step_id VARCHAR(96) PRIMARY KEY, pipeline_id VARCHAR(96) NOT NULL,
                    status VARCHAR(40) NOT NULL, INDEX idx_steps_pipeline (pipeline_id)
                ) ENGINE=InnoDB""")
                await cur.execute("DELETE FROM jacobs_steps WHERE step_id=%s", (step_id,))
                await cur.execute("DELETE FROM jacobs_pipelines WHERE pipeline_id=%s", (pipeline_id,))
                await cur.execute("INSERT INTO jacobs_pipelines VALUES (%s,%s,%s,%s,%s)",
                    (pipeline_id, "tenant-a", "user-a", 1.0, "running"))
                await cur.execute("INSERT INTO jacobs_steps VALUES (%s,%s,%s)",
                    (step_id, pipeline_id, "blocked_human_gate"))
                await cur.execute("EXPLAIN " + jacobs_store._STEP_STATUS_SNAPSHOT_SQL, (step_id,))
                plan = await cur.fetchall()
            return plan
        finally:
            conn.close()

    plan = asyncio.run(seed_and_explain())
    assert len(plan) == 2
    assert plan[0]["key"] == "PRIMARY"
    assert plan[1]["key"] == "PRIMARY"
    assert plan[1]["type"] in {"eq_ref", "const"}

    evidence = asyncio.run(JacobsStepStatusResolver().evidence(
        {"step_id": step_id, "status": "blocked_human_gate"},
        _scope(bridge)))
    assert evidence.adapter_kind is resolution.AdapterKind.JACOBS_STEP_STATUS
    assert evidence.observation.status is resolution.ResolutionStatus.RESOLVED
    assert evidence.observation.result == {"step_id": step_id, "status": "blocked_human_gate"}
    assert evidence.observation.observed_at <= datetime.now(timezone.utc)

    registry = bridge.build_runtime_status_registry(_scope(bridge),
        authenticator=resolution.ReceiptAuthenticator.for_testing(b"sr2-platform-db-key-012345678901"),
        platform_source_configuration=_platform_source_configuration())
    receipt = registry.resolve("STEP_STATUS", evidence.observation.result, _scope(bridge),
        validation_time=datetime.now(timezone.utc), runtime_status_evidence=evidence)
    assert receipt.status is resolution.ResolutionStatus.RESOLVED
