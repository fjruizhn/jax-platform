"""Fixed JAX Platform sources for F2-B runtime-status claims.

The adapter reads only server-owned FacetState and the configured LAS MANOS
health observation. It accepts neither source identities nor resolver
selection from request data and never exports facet/user payload fields.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Mapping

from .state import JAXEngineState, engine_state, las_manos_health_source_configuration


class RuntimeStatusBridgeUnavailable(RuntimeError):
    """The configured JAX checkout does not expose the pinned F2-B bridge."""


def _jax_runtime_status_bridge():
    root = os.environ.get("JAX_REPO_PATH", "")
    if not root or not os.path.isabs(root):
        raise RuntimeStatusBridgeUnavailable("JAX_REPO_PATH is unavailable")
    root_path = Path(root).resolve()
    bridge_path = root_path / "policy" / "governance" / "runtime_status.py"
    if not bridge_path.is_file():
        raise RuntimeStatusBridgeUnavailable("configured JAX lacks runtime-status bridge")
    root_text = str(root_path)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    try:
        module = importlib.import_module("policy.governance.runtime_status")
    except (ImportError, AttributeError) as exc:
        raise RuntimeStatusBridgeUnavailable("JAX runtime-status bridge cannot load") from exc
    loaded_path = Path(module.__file__).resolve()
    if not loaded_path.is_relative_to(root_path):
        raise RuntimeStatusBridgeUnavailable("loaded runtime-status bridge is outside configured JAX")
    if getattr(module, "RUNTIME_STATUS_API_VERSION", None) != "f2-e.runtime-status.4":
        raise RuntimeStatusBridgeUnavailable("unsupported JAX runtime-status bridge version")
    return module


def _arguments(arguments: Mapping[str, object] | object, *, expected_name: str | None = None):
    if not isinstance(arguments, Mapping) or set(arguments) != {"name", "status"}:
        return None
    name, status = arguments["name"], arguments["status"]
    if not isinstance(name, str) or not name or not isinstance(status, str) or not status:
        return None
    if expected_name is not None and name != expected_name:
        return None
    return name, status


class FacetRuntimeStatusResolver:
    """Observe only the platform's registered transient FacetState status."""

    def __init__(self):
        # The server module singleton is selected by platform composition;
        # requests cannot substitute another state object/source.
        if not isinstance(engine_state, JAXEngineState):
            raise TypeError("facet status resolver requires server JAXEngineState")
        self._state = engine_state

    def evidence(self, arguments: Mapping[str, object], scope):
        parsed = _arguments(arguments)
        if parsed is None:
            return None
        name, requested_status = parsed
        snapshot = self._state.facet_runtime_status_snapshot(name)
        if snapshot is None:
            return None
        observed_status, observed_at = snapshot
        if requested_status != observed_status:
            return None
        bridge = _jax_runtime_status_bridge()
        typed_snapshot = bridge.PlatformRuntimeStatusSnapshot(
            predicate="FACET_RUNTIME_STATUS",
            arguments={"name": name, "status": observed_status},
            observed_at=observed_at,
            provenance_ref=f"facet:{name}",
            source_configuration={"state_contract": "JAXEngineState.FacetState", "status_field": "status",
                "observed_at_field": "resolver_read_time", "allowed_statuses": ["idle", "thinking", "error", "offline"]},
        )
        try:
            return bridge.platform_runtime_status_evidence(typed_snapshot, arguments, scope)
        except (TypeError, ValueError):
            return None


class LasManosHealthStatusResolver:
    """Observe the single fixed LAS MANOS probe, never an arbitrary engine."""

    _ENGINE_NAME = "las_manos"
    _ALLOWED_STATUS = frozenset({"alive", "down"})

    def __init__(self):
        if not isinstance(engine_state, JAXEngineState):
            raise TypeError("engine status resolver requires server JAXEngineState")
        self._state = engine_state

    def evidence(self, arguments: Mapping[str, object], scope):
        parsed = _arguments(arguments, expected_name=self._ENGINE_NAME)
        if parsed is None:
            return None
        name, requested_status = parsed
        if requested_status not in self._ALLOWED_STATUS:
            return None
        snapshot = self._state.engine_health_status_snapshot(name)
        if snapshot is None:
            return None
        observed_status, observed_at = snapshot
        if requested_status != observed_status:
            return None
        bridge = _jax_runtime_status_bridge()
        typed_snapshot = bridge.PlatformRuntimeStatusSnapshot(
            predicate="ENGINE_STATUS",
            arguments={"name": name, "status": observed_status},
            observed_at=observed_at,
            provenance_ref="health:las_manos",
            source_configuration=las_manos_health_source_configuration(),
        )
        try:
            return bridge.platform_runtime_status_evidence(typed_snapshot, arguments, scope)
        except (TypeError, ValueError):
            return None


class JacobsStepStatusResolver:
    """Use the configured JAX canonical step+owner resolver without aliases."""

    async def evidence(self, arguments: Mapping[str, object], scope):
        if (not isinstance(arguments, Mapping) or set(arguments) != {"step_id", "status"}
                or not isinstance(arguments.get("step_id"), str)
                or not arguments["step_id"]
                or not isinstance(arguments.get("status"), str)
                or not arguments["status"]):
            return None
        if getattr(scope, "project_id", None) is not None or getattr(scope, "subject_id", None) is None:
            return None
        bridge = _jax_runtime_status_bridge()
        try:
            return await bridge.JacobsStepStatusResolver().evidence(arguments, scope)
        except (TypeError, ValueError):
            return None
