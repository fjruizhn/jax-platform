"""Server-owned Platform composition for F2-E structured output.

Only this module connects Platform's fixed runtime status bridges with the
paired JAX F2-B composition API.  It has no request payload inputs for source
selection, receipts, or resolver identity.
"""
from __future__ import annotations

from functools import lru_cache
import os
import uuid
from typing import Mapping

from auth.models import AuthUser
from jax_engine.state import las_manos_health_source_configuration
from jax_engine.status_resolution import FacetRuntimeStatusResolver, LasManosHealthStatusResolver

from .core import StructuredCore, load_structured_core


_COMPOSITION_COMPONENT = "platform-external-output"
_COMPOSITION_RECEIPT_VERSION = "f2-e.platform-structured.1"
_HTTP_FACET_KEYS = ("jax_local", "jekyll", "hyde", "hipatia", "thot", "kimi", "ada", "jacobs")


def _environment() -> str:
    value = os.environ.get("JAX_ENVIRONMENT", "production")
    return value if value in {"production", "staging", "test", "development"} else "production"


def _platform_source_configuration() -> dict[str, Mapping[str, object]]:
    return {
        "FACET_RUNTIME_STATUS": {
            "state_contract": "JAXEngineState.FacetState",
            "status_field": "status",
            "observed_at_field": "resolver_read_time",
            "allowed_statuses": ["idle", "thinking", "error", "offline"],
        },
        "ENGINE_STATUS": las_manos_health_source_configuration(),
    }


def _runtime_evidence(request, scope):
    """Read only the fixed Platform-owned status projections.

    The typed request comes from a server route contract.  Resolver selection
    is an enum branch owned here, never a request field or DTO annotation.
    """
    if request.predicate == "FACET_RUNTIME_STATUS":
        evidence = FacetRuntimeStatusResolver().evidence(request.canonical_arguments, scope)
    elif request.predicate == "ENGINE_STATUS":
        evidence = LasManosHealthStatusResolver().evidence(request.canonical_arguments, scope)
    else:
        raise ValueError("Platform cannot supply evidence for this runtime predicate")
    if evidence is None:
        raise ValueError("fixed Platform runtime status source is unavailable")
    return evidence


def output_scope(*, user: AuthUser, request_id: str | None = None, trace_id: str | None = None,
                 project_id: str | None = None):
    """Mint an output scope solely from authenticated server context."""
    if not isinstance(user, AuthUser):
        raise TypeError("external output scope requires authenticated server principal")
    core = load_structured_core()
    return core.response.ResponseScope(
        environment=_environment(),
        tenant_id=str(user.tenant_id),
        project_id=project_id,
        subject_id=str(user.user_id),
        actor_id="service:jax-platform",
        audience=f"user:{user.user_id}",
        component_id=_COMPOSITION_COMPONENT,
        request_id=request_id or str(uuid.uuid4()),
        trace_id=trace_id or str(uuid.uuid4()),
    )


def _templates(core: StructuredCore) -> dict[tuple[str, str, str], str]:
    runtime_version = __import__("policy.governance.runtime_status", fromlist=["RUNTIME_STATUS_API_VERSION"]).RUNTIME_STATUS_API_VERSION
    return {
        (predicate, runtime_version, "es"): predicate + " {status}"
        for predicate in ("JOB_STATUS", "PIPELINE_STATUS", "FACET_RUNTIME_STATUS", "ENGINE_STATUS")
    }


@lru_cache(maxsize=1)
def runtime_output_composer():
    """Create the process-owned composition with a non-test receipt key once."""
    core = load_structured_core()
    runtime = core.runtime_composition
    structured = core.structured_output
    # Claimed outputs seal with the exact per-scope ResolverRegistry snapshot
    # computed by the paired JAX core; no Platform placeholder is permitted.
    receipt = None
    return runtime.RuntimeOutputComposition(
        platform_source_configuration=_platform_source_configuration(),
        governance_receipt=receipt,
        templates=_templates(core),
        notices={},
        platform_evidence_bridge=_runtime_evidence,
        # These are route-contract facts.  They partition canonical F2-B
        # arguments between visible wire fields and values fixed by the
        # server, so existing event schemas need no hidden identity field.
        slot_contracts={
            ("ENGINE_STATUS", "/payload/alive"): runtime.RuntimeSlotContract(
                "ENGINE_STATUS", "/payload/alive", {"status": "/payload/alive"},
                {"name": "las_manos"},
            ),
            ("ENGINE_STATUS", "/las_manos"): runtime.RuntimeSlotContract(
                "ENGINE_STATUS", "/las_manos", {"status": "/las_manos"},
                {"name": "las_manos"},
            ),
            ("FACET_RUNTIME_STATUS", "/payload/status"): runtime.RuntimeSlotContract(
                "FACET_RUNTIME_STATUS", "/payload/status",
                {"name": "/payload/facet", "status": "/payload/status"},
            ),
            ("PIPELINE_STATUS", "/payload/status"): runtime.RuntimeSlotContract(
                "PIPELINE_STATUS", "/payload/status",
                {"pipeline_id": "/payload/pipeline_id", "status": "/payload/status"},
            ),
            ("PIPELINE_STATUS", "/status"): runtime.RuntimeSlotContract(
                "PIPELINE_STATUS", "/status", {"pipeline_id": "/pipeline_id", "status": "/status"},
            ),
            **{
                ("FACET_RUNTIME_STATUS", f"/facets/{name}/status"): runtime.RuntimeSlotContract(
                    "FACET_RUNTIME_STATUS", f"/facets/{name}/status",
                    {"name": f"/facets/{name}/name", "status": f"/facets/{name}/status"},
                ) for name in _HTTP_FACET_KEYS
            },
        },
        # Event routes choose this closed id in code.  The empty tuple is
        # intentional until an existing native event schema contains a float;
        # an unregistered float is rejected before F2-A sealing.
        number_binding_contracts={
            "platform-native-event.v1": structured.StructuredNumberBindingContract(),
            # Exact route schema: only this result array may materialize a
            # finite float and only at its declared per-step field. Decimal
            # monetary values already serialize as strings and are untouched.
            "platform-http.pipeline-results.v1": structured.StructuredNumberBindingContract(
                bindings=(structured.StructJSONNumberBinding("/duration_seconds", nullable=True),),
                patterns=(structured.StructJSONNumberPattern("/steps/*/duration_seconds", nullable=True),),
            ),
        },
    )


async def compose_runtime_output(*, user: AuthUser, tool_data: Mapping[str, object] | tuple[object, ...],
                                 requests: tuple[object, ...] = (), project_id: str | None = None,
                                 number_binding_contract_id: str | None = None):
    """Compose only server-declared runtime requests and typed untrusted data."""
    scope = output_scope(user=user, project_id=project_id)
    return await runtime_output_composer().compose(
        scope=scope,
        response_id=str(uuid.uuid4()),
        producer=_COMPOSITION_COMPONENT,
        tool_data=tool_data,
        requests=requests,
        number_binding_contract_id=number_binding_contract_id,
    )
