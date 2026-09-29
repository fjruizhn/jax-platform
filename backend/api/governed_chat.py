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
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keep the platform importable until the paired JAX core is deployed
    from api.chat import ContractResult
    from jax.memory.b9 import ScopeContext


_UNAVAILABLE_NOTICE = "I could not verify the current state."
_DEGRADED_NOTICE = "The response could not be verified safely."


class GovernedChatUnavailable(RuntimeError):
    """The JAX F2-C core is unavailable; raw candidate text must not escape."""


@dataclass(frozen=True)
class GovernedChatProjection:
    text: str
    response_id: str | None
    envelope_digest: str | None
    source_envelope_digest: str | None
    contract_state: str
    contract_degraded: bool
    governed_plain: bool


def _core():
    """Load the paired JAX F2-C core lazily, after JAX_REPO_PATH is on sys.path."""
    root = os.environ.get("JAX_REPO_PATH")
    if not root or not os.path.isabs(root):
        raise GovernedChatUnavailable("JAX_REPO_PATH is unavailable for governed rendering")
    # Do this before importing: a prior request may have loaded ``policy``
    # from another checkout into sys.modules.  The configured repository is
    # the trusted paired core dependency, not whichever module happened to be
    # imported first in this process.
    root_path = Path(root).resolve()
    if not (root_path / "policy" / "governance" / "governed_renderer.py").is_file():
        raise GovernedChatUnavailable("configured JAX repository lacks the F2-C renderer")
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
        from policy.governance.governed_domain import (
            GOVERNED_DOMAIN_SPEC_VERSION, GOVERNED_ENVELOPE_SCHEMA_VERSIONS,
            GOVERNED_RENDERER_API_VERSION, GovernedDomainSpecification,
        )
        modules = (importlib.import_module("policy.governance.governed_renderer"),
                   importlib.import_module("policy.governance.governed_domain"),
                   importlib.import_module("policy.governance.response"))
        if any(Path(module.__file__).resolve().is_relative_to(root_path) is False for module in modules):
            raise GovernedChatUnavailable("loaded F2-C modules are outside configured JAX repository")
        if (GOVERNED_RENDERER_API_VERSION, GOVERNED_DOMAIN_SPEC_VERSION,
                GOVERNED_ENVELOPE_SCHEMA_VERSIONS) != ("f2-c.renderer.2", "f2-c.domain.1", frozenset({"f2-c.1"})):
            raise GovernedChatUnavailable("configured JAX F2-C compatibility is unsupported")
    except (ImportError, AttributeError) as exc:
        raise GovernedChatUnavailable("F2-C core renderer is unavailable") from exc
    return (GovernedDomainRegistry, GovernedRenderer, RenderContext,
            WebChatGovernanceAdapter, ContractState, GovernanceReceipt, ResponseScope,
            GovernedDomainSpecification)


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


def project_sealed_envelope(envelope, render_context) -> GovernedChatProjection:
    """Render a typed sealed envelope through the same platform projection.

    This is also the non-production composition seam for accredited F2-B
    integration tests. The caller must supply server-owned registry,
    authenticator-minted receipts, templates and reference validators.
    """
    try:
        (_, GovernedRenderer, _, _, _, _, _, _) = _core()
        rendered = GovernedRenderer().render_text(envelope, render_context)
        degraded = rendered.contract_state.value != "VALID"
        return GovernedChatProjection(
            text=rendered.text, response_id=rendered.response_id,
            envelope_digest=rendered.envelope_digest,
            source_envelope_digest=rendered.source_envelope_digest,
            contract_state=rendered.contract_state.value,
            contract_degraded=degraded, governed_plain=True,
        )
    except Exception:  # fail-soft: never expose an envelope or text if the renderer fails
        return GovernedChatProjection(
            text=_DEGRADED_NOTICE, response_id=None, envelope_digest=None,
            source_envelope_digest=None,
            contract_state="UNAVAILABLE", contract_degraded=True,
            governed_plain=True,
        )


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
         WebChatGovernanceAdapter, ContractState, GovernanceReceipt, ResponseScope,
         GovernedDomainSpecification) = _core()
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
            domain_registry=GovernedDomainRegistry(specification=GovernedDomainSpecification()),
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
            source_envelope_digest=rendered.source_envelope_digest,
            contract_state=rendered.contract_state.value,
            contract_degraded=rendered.contract_state.value != "VALID" or degraded, governed_plain=True,
        )
    except Exception:  # fail-soft: renderer/core failure emits only static non-current text, never provider prose
        # This is the only F2-C bridge failure fallback.  It is static,
        # server-owned and contains no provider candidate text.
        return GovernedChatProjection(
            text=_DEGRADED_NOTICE, response_id=None, envelope_digest=None,
            source_envelope_digest=None,
            contract_state="UNAVAILABLE", contract_degraded=True,
            governed_plain=True,
        )
