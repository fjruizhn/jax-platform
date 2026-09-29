"""F2-C Web Chat composition bridge.

This module is deliberately the *only* bridge from buffered provider text to
the F2-C renderer.  It owns all values used to construct the response scope
and governance receipt; neither a provider candidate nor a request payload
can supply them.  F2-D transport durability is intentionally not implemented
here.
"""
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keep the platform importable until the paired JAX core is deployed
    from api.chat import ContractResult
    from jax.memory.b9 import ScopeContext


# This is a deliberately small, deterministic grammar.  It is a tripwire for
# known current-system wording, not an NLP claim classifier.  A parsed provider
# claim is blocked independently of this grammar.
_GOVERNED_PHRASES = {
    "capability_available": "CAPABILITY_AVAILABLE",
    "is available": "CAPABILITY_AVAILABLE",
    "está disponible": "CAPABILITY_AVAILABLE",
    "facet_exists": "FACET_EXISTS",
    "engine_status": "ENGINE_STATUS",
    "is running": "ENGINE_STATUS",
    "está ejecutándose": "ENGINE_STATUS",
    "server is healthy": "ENGINE_STATUS",
    "el servidor está saludable": "ENGINE_STATUS",
    "config_value": "CONFIG_VALUE",
    "file_exists": "FILE_EXISTS",
    "audit_event_exists": "AUDIT_EVENT_EXISTS",
    "job_status": "JOB_STATUS",
    "memory_entry_exists": "MEMORY_ENTRY_EXISTS",
}

_UNAVAILABLE_NOTICE = "I could not verify the current state."
_DEGRADED_NOTICE = "The response could not be verified safely."


class GovernedChatUnavailable(RuntimeError):
    """The JAX F2-C core is unavailable; raw candidate text must not escape."""


@dataclass(frozen=True)
class GovernedChatProjection:
    text: str
    response_id: str | None
    envelope_digest: str | None
    contract_state: str
    contract_degraded: bool
    governed_plain: bool


def _core():
    """Load the paired JAX F2-C core lazily, after JAX_REPO_PATH is on sys.path."""
    root = os.environ.get("JAX_REPO_PATH")
    if not root or not os.path.isabs(root):
        raise GovernedChatUnavailable("JAX_REPO_PATH is unavailable for governed rendering")
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from policy.governance.governed_renderer import (
            GovernedDomainRegistry,
            GovernedRenderer,
            RenderContext,
            WebChatGovernanceAdapter,
        )
        from policy.governance.response import ContractState, GovernanceReceipt, ResponseScope
    except (ImportError, AttributeError) as exc:
        raise GovernedChatUnavailable("F2-C core renderer is unavailable") from exc
    return (GovernedDomainRegistry, GovernedRenderer, RenderContext,
            WebChatGovernanceAdapter, ContractState, GovernanceReceipt, ResponseScope)


def _environment() -> str:
    # Server-owned process configuration only.  A missing setting remains
    # explicit rather than taking a client supplied environment.
    value = os.environ.get("JAX_ENVIRONMENT", "production")
    if value not in {"production", "staging", "test", "development"}:
        return "production"
    return value


def _narrative(contract: "ContractResult") -> str:
    """Preserve non-governed prose without relying on Markdown presentation."""
    if contract.judgment:
        return f"{contract.analysis}\n\n{contract.judgment}"
    return contract.analysis


def project_provider_contract(
    contract: "ContractResult | None", *, memory_scope: "ScopeContext",
    user_id: str, request_id: str | None = None, trace_id: str | None = None,
) -> GovernedChatProjection:
    """Seal and render a fully-buffered Web Chat provider candidate.

    Parsed provider claims are not F2-B accredited in the platform process:
    production receipt-key/registry composition and F2-D transport preparation
    are deliberately deferred.  They therefore receive only the server-owned
    unavailable rendering, never provider wording.
    """
    try:
        (GovernedDomainRegistry, GovernedRenderer, RenderContext,
         WebChatGovernanceAdapter, ContractState, GovernanceReceipt, ResponseScope) = _core()
        response_id = str(uuid.uuid4())
        request_id = request_id or str(uuid.uuid4())
        trace_id = trace_id or str(uuid.uuid4())
        scope = ResponseScope(
            environment=_environment(),
            tenant_id=str(memory_scope.tenant_id),
            project_id=memory_scope.project_id,
            subject_id=memory_scope.subject_user_id or str(user_id),
            actor_id=memory_scope.actor_principal,
            audience=f"user:{user_id}",
            component_id="web-chat",
            request_id=request_id,
            trace_id=trace_id,
        )
        receipt = GovernanceReceipt(
            policy_version="f2-c-web-chat.1",
            vocabulary_version="predicates.yaml",
            registry_snapshot_digest="unconfigured-f2-b-production",
            validator_version="f2-c-web-chat.1",
            renderer_plan_version="f2-c.1",
        )
        adapter = WebChatGovernanceAdapter(scope, receipt)
        context = RenderContext(
            registry=None, receipts={}, templates={},
            notices={"unavailable": _UNAVAILABLE_NOTICE, "degraded": _DEGRADED_NOTICE},
            domain_registry=GovernedDomainRegistry(_GOVERNED_PHRASES),
        )
        if contract is None or not contract.contract_parsed:
            envelope = adapter.seal_safe_notice(
                response_id=response_id, notice_id="degraded",
                contract_state=ContractState.DEGRADED_STRUCTURED,
            )
            degraded = True
        elif contract.claims:
            envelope = adapter.seal_safe_notice(
                response_id=response_id, notice_id="unavailable",
                contract_state=ContractState.BLOCKED_SYSTEM_CLAIM,
            )
            degraded = False
        else:
            envelope = adapter.seal_non_governed_candidate(
                response_id=response_id, candidate_text=_narrative(contract),
            )
            degraded = False
        rendered = GovernedRenderer().render_text(envelope, context)
        return GovernedChatProjection(
            text=rendered.text, response_id=rendered.response_id,
            envelope_digest=rendered.envelope_digest,
            contract_state=rendered.contract_state.value,
            contract_degraded=degraded, governed_plain=True,
        )
    except Exception:  # fail-soft: renderer/core failure emits only static non-current text, never provider prose
        # This is the only F2-C bridge failure fallback.  It is static,
        # server-owned and contains no provider candidate text.
        return GovernedChatProjection(
            text=_DEGRADED_NOTICE, response_id=None, envelope_digest=None,
            contract_state="UNAVAILABLE", contract_degraded=True,
            governed_plain=True,
        )
