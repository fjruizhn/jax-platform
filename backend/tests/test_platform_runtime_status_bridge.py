"""Platform status sources may enter F2-B only through the fixed JAX bridge."""
from dataclasses import replace
from datetime import datetime, timezone
import os
import importlib
from pathlib import Path

import pytest

from jax_engine.state import JAXEngineState
from jax_engine import status_resolution as status_bridge
from jax_engine.status_resolution import (
    FacetRuntimeStatusResolver,
    LasManosHealthStatusResolver,
    RuntimeStatusBridgeUnavailable,
)


def _scope(core):
    return core.ResponseScope(
        "test", "tenant-a", None, "user-a", "service:platform",
        "human:fernando", "status-test", "request-a", "trace-a",
    )


def _source_configuration():
    from jax_engine.state import las_manos_health_source_configuration
    return {
        "FACET_RUNTIME_STATUS": {"state_contract": "JAXEngineState.FacetState", "status_field": "status",
            "observed_at_field": "resolver_read_time", "allowed_statuses": ["idle", "thinking", "error", "offline"]},
        "ENGINE_STATUS": las_manos_health_source_configuration(),
    }


def _core(monkeypatch):
    root = Path(os.environ["JAX_REPO_PATH"]).resolve()
    monkeypatch.setenv("JAX_REPO_PATH", str(root))
    from jax_engine.status_resolution import _jax_runtime_status_bridge
    try:
        runtime_status = _jax_runtime_status_bridge()
    except RuntimeStatusBridgeUnavailable:
        # The broad no-DB suite intentionally checks compatibility against
        # JAX master. The branch-pinned exact-pair job sets this marker and
        # must fail if the bridge is absent or incompatible.
        if os.environ.get("JAX_RUNTIME_STATUS_EXPECTED_SHA"):
            raise
        pytest.skip("requires the exact-pair F2-E runtime-status JAX bridge")
    resolution = importlib.import_module("policy.governance.resolution")
    return runtime_status, resolution


def test_facet_runtime_status_bridge_carries_only_registered_status_and_exact_scope(monkeypatch):
    core, resolution = _core(monkeypatch)
    state = JAXEngineState()
    facet = state._state.facets["hyde"]
    facet.status = "thinking"
    facet.last_message = "secret per-user payload"
    facet.display_name = "Private label"
    facet.last_update = "2026-10-02T12:00:00Z"

    monkeypatch.setattr(status_bridge, "engine_state", state)
    evidence = FacetRuntimeStatusResolver().evidence(
        {"name": "hyde", "status": "thinking"}, _scope(resolution)
    )

    assert evidence.adapter_kind is resolution.AdapterKind.FACET_RUNTIME_STATUS
    assert evidence.observation.result == {"name": "hyde", "status": "thinking"}
    assert evidence.observation.observed_at > datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    assert evidence.observation.observed_at <= datetime.now(timezone.utc)
    assert evidence.observation_scope.subject_id == "user-a"
    assert "secret per-user payload" not in repr(evidence)
    assert "Private label" not in repr(evidence)


def test_facet_runtime_status_never_accepts_facet_state_as_engine_health(monkeypatch):
    _, resolution = _core(monkeypatch)
    state = JAXEngineState()
    facet = state._state.facets["hyde"]
    facet.status = "offline"
    monkeypatch.setattr(status_bridge, "engine_state", state)
    evidence = FacetRuntimeStatusResolver().evidence(
        {"name": "hyde", "status": "offline"}, _scope(resolution)
    )
    assert evidence.adapter_kind is resolution.AdapterKind.FACET_RUNTIME_STATUS
    assert evidence.observation.result != {"name": "las_manos", "status": "down"}


def test_unknown_facet_or_wrong_claimed_status_has_no_resolved_evidence(monkeypatch):
    _, resolution = _core(monkeypatch)
    state = JAXEngineState()
    monkeypatch.setattr(status_bridge, "engine_state", state)
    resolver = FacetRuntimeStatusResolver()

    assert resolver.evidence({"name": "unknown", "status": "idle"}, _scope(resolution)) is None
    assert resolver.evidence({"name": "hyde", "status": "offline"}, _scope(resolution)) is None
    assert resolver.evidence({"name": "hyde", "status": "healthy"}, _scope(resolution)) is None


def test_engine_health_bridge_is_fixed_to_las_manos_and_completed_probe(monkeypatch):
    core, resolution = _core(monkeypatch)
    state = JAXEngineState()
    monkeypatch.setattr(status_bridge, "engine_state", state)
    resolver = LasManosHealthStatusResolver()

    assert resolver.evidence({"name": "las_manos", "status": "down"}, _scope(resolution)) is None
    assert resolver.evidence({"name": "other", "status": "alive"}, _scope(resolution)) is None

    state._commit_las_manos_health_observation(True, datetime.now(timezone.utc))
    evidence = resolver.evidence(
        {"name": "las_manos", "status": "alive"}, _scope(resolution)
    )
    assert evidence.adapter_kind is resolution.AdapterKind.ENGINE_STATUS
    assert evidence.observation.result == {"name": "las_manos", "status": "alive"}

    registry = core.build_runtime_status_registry(
        _scope(resolution), authenticator=resolution.ReceiptAuthenticator.for_testing(b"x" * 32),
        platform_source_configuration=_source_configuration(),
    )
    receipt = registry.resolve("ENGINE_STATUS", {"name": "las_manos", "status": "alive"},
        _scope(resolution), validation_time=datetime.now(timezone.utc), runtime_status_evidence=evidence)
    assert receipt.status is resolution.ResolutionStatus.RESOLVED


def test_platform_runtime_status_exact_pair_resolves_facet_without_health_conflation(monkeypatch):
    core, resolution = _core(monkeypatch)
    state = JAXEngineState()
    facet = state._state.facets["hyde"]
    facet.status = "thinking"
    facet.last_message = "private user message must not enter evidence"
    facet.last_update = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(status_bridge, "engine_state", state)
    evidence = FacetRuntimeStatusResolver().evidence(
        {"name": "hyde", "status": "thinking"}, _scope(resolution)
    )
    registry = core.build_runtime_status_registry(
        _scope(resolution), authenticator=resolution.ReceiptAuthenticator.for_testing(b"y" * 32),
        platform_source_configuration=_source_configuration(),
    )
    receipt = registry.resolve("FACET_RUNTIME_STATUS", {"name": "hyde", "status": "thinking"},
        _scope(resolution), validation_time=datetime.now(timezone.utc), runtime_status_evidence=evidence)
    assert receipt.status is resolution.ResolutionStatus.RESOLVED
    assert receipt.result_digest
    assert "private user message" not in repr(receipt)

    wrong_domain = registry.resolve("ENGINE_STATUS", {"name": "hyde", "status": "thinking"},
        _scope(resolution), validation_time=datetime.now(timezone.utc), runtime_status_evidence=evidence)
    assert wrong_domain.status is resolution.ResolutionStatus.UNAVAILABLE


def test_platform_global_status_evidence_is_not_replayable_between_tenants(monkeypatch):
    core, resolution = _core(monkeypatch)
    state = JAXEngineState()
    facet = state._state.facets["hyde"]
    facet.status = "idle"
    facet.last_update = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(status_bridge, "engine_state", state)
    evidence = FacetRuntimeStatusResolver().evidence(
        {"name": "hyde", "status": "idle"}, _scope(resolution)
    )
    registry = core.build_runtime_status_registry(
        _scope(resolution), authenticator=resolution.ReceiptAuthenticator.for_testing(b"z" * 32),
        platform_source_configuration=_source_configuration(),
    )
    other = replace(_scope(resolution), tenant_id="tenant-b", request_id="request-b")
    receipt = registry.resolve("FACET_RUNTIME_STATUS", {"name": "hyde", "status": "idle"},
        other, validation_time=datetime.now(timezone.utc), runtime_status_evidence=evidence)
    assert receipt.status is resolution.ResolutionStatus.WRONG_SCOPE


def test_engine_probe_configuration_is_bound_into_f2b_registry_and_receipt(monkeypatch):
    core, resolution = _core(monkeypatch)
    from jax_engine import state as state_module
    state = JAXEngineState()
    state._commit_las_manos_health_observation(True, datetime.now(timezone.utc))
    monkeypatch.setattr(status_bridge, "engine_state", state)
    config = state_module.las_manos_health_source_configuration()
    digest = core.runtime_status_source_configuration_digest("ENGINE_STATUS", config)
    evidence = LasManosHealthStatusResolver().evidence(
        {"name": "las_manos", "status": "alive"}, _scope(resolution))
    assert evidence.source_configuration_digest == digest
    registry = core.build_runtime_status_registry(_scope(resolution),
        authenticator=resolution.ReceiptAuthenticator.for_testing(b"runtime-status-test-key-material-32-bytes"),
        platform_source_configuration={
            "FACET_RUNTIME_STATUS": {"state_contract": "JAXEngineState.FacetState", "status_field": "status",
                "observed_at_field": "resolver_read_time", "allowed_statuses": ["idle", "thinking", "error", "offline"]},
            "ENGINE_STATUS": config,
        })
    receipt = registry.resolve("ENGINE_STATUS", {"name": "las_manos", "status": "alive"},
        _scope(resolution), validation_time=evidence.observation.observed_at,
        runtime_status_evidence=evidence)
    assert receipt.status is resolution.ResolutionStatus.RESOLVED
    assert receipt.source_configuration_digest == digest

    original_digest = digest
    monkeypatch.setattr(state_module, "LAS_MANOS_URL", "http://different-server.invalid:7777")
    changed_config = state_module.las_manos_health_source_configuration()
    assert core.runtime_status_source_configuration_digest("ENGINE_STATUS", changed_config) != original_digest


def test_engine_probe_rejects_the_previous_public_unauthenticated_contract(monkeypatch):
    core, _resolution = _core(monkeypatch)
    config = __import__("jax_engine.state", fromlist=["las_manos_health_source_configuration"]).las_manos_health_source_configuration()
    old = {key: value for key, value in config.items()
           if key not in {"service_authentication_identity", "service_authentication_header"}}
    old["path"] = "/health"
    with pytest.raises(core.GovernanceContractError):
        core.runtime_status_source_configuration_digest("ENGINE_STATUS", old)


def test_engine_health_evidence_becomes_stale_without_another_completed_probe(monkeypatch):
    core, resolution = _core(monkeypatch)
    state = JAXEngineState()
    observed_at = datetime(2026, 10, 3, 1, tzinfo=timezone.utc)
    state._commit_las_manos_health_observation(True, observed_at)
    monkeypatch.setattr(status_bridge, "engine_state", state)
    evidence = LasManosHealthStatusResolver().evidence(
        {"name": "las_manos", "status": "alive"}, _scope(resolution))
    registry = core.build_runtime_status_registry(
        _scope(resolution), authenticator=resolution.ReceiptAuthenticator.for_testing(b"s" * 32),
        platform_source_configuration=_source_configuration(),
    )

    receipt = registry.resolve(
        "ENGINE_STATUS", {"name": "las_manos", "status": "alive"}, _scope(resolution),
        validation_time=observed_at.replace(minute=2), runtime_status_evidence=evidence,
    )

    assert receipt.status is resolution.ResolutionStatus.STALE


def test_bridge_rejects_missing_or_wrong_jax_runtime_status_checkout(monkeypatch, tmp_path):
    monkeypatch.setenv("JAX_REPO_PATH", str(tmp_path))
    with pytest.raises(RuntimeStatusBridgeUnavailable):
        FacetRuntimeStatusResolver().evidence(
            {"name": "hyde", "status": "idle"}, object()
        )
