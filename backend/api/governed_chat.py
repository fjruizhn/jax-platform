"""F2-C Web Chat composition bridge.

This module is deliberately the *only* bridge from buffered provider text to
the F2-C renderer and the JAX-owned F2-D transport unit. It owns values used
to construct response scope and governance receipts; neither provider nor
request payloads can supply lifecycle authority.
"""
from __future__ import annotations

import os
import sys
import uuid
import importlib
import hashlib
import logging
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keep the platform importable until the paired JAX core is deployed
    from api.chat import ContractResult
    from jax.memory.b9 import ScopeContext


logger = logging.getLogger(__name__)

_UNAVAILABLE_NOTICE = "I could not verify the current state."
_DEGRADED_NOTICE = "The response could not be verified safely."

# This platform bridge is reviewed only against this exact JAX F2-C contract.
# Versions are a compatibility boundary, not a capability range: accepting an
# older or newer renderer/domain pair could make the transport project an
# envelope with semantics it has not been reviewed to preserve.
F2C_EXACT_PAIR_COMPATIBILITY = (
    "f2-c.renderer.3",
    "f2-c.domain.7",
    frozenset({"f2-c.1"}),
)


# F2-D lifecycle API que este puente revisó (ver _lifecycle_core).
F2D_LIFECYCLE_API_VERSION = "f2-d.lifecycle.2"


class GovernedChatUnavailable(RuntimeError):
    """The JAX F2-C core is unavailable; raw candidate text must not escape."""


def _registrar_fallo_cerrado(mensaje: str, *args, exc: BaseException) -> None:
    """logger.error de un fail-soft, sin dejar que el texto del proveedor llegue al log.

    GovernedChatUnavailable la escribe este modulo (versiones, rutas): se vuelca completa,
    con traza. Cualquier otra excepcion salio de codigo que pudo recibir el candidato del
    proveedor (seal/render/mint) y su mensaje podria contenerlo: solo se registran su tipo y
    los marcos de la traza, nunca ``str(exc)`` ni sus argumentos.
    """
    if isinstance(exc, GovernedChatUnavailable):
        logger.error(mensaje, *args, exc_info=exc)
        return
    marcos = "".join(traceback.format_tb(exc.__traceback__))
    logger.error(mensaje + " [%s, mensaje omitido]\n%s", *args, type(exc).__name__, marcos)


@dataclass(frozen=True)
class GovernedChatProjection:
    text: str
    response_id: str | None
    envelope_digest: str | None
    source_envelope_digest: str | None
    contract_state: str
    contract_degraded: bool
    governed_plain: bool
    # Private in-process F2-D authority; never included in ChatResponse JSON.
    transport_unit: object | None = None


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
        encontrado = (GOVERNED_RENDERER_API_VERSION, GOVERNED_DOMAIN_SPEC_VERSION,
                      GOVERNED_ENVELOPE_SCHEMA_VERSIONS)
        if encontrado != F2C_EXACT_PAIR_COMPATIBILITY:
            # 2026-10-05: el mensaje dice las dos versiones; un par roto en
            # produccion se diagnostico a ciegas porque no decia ninguna.
            raise GovernedChatUnavailable(
                "configured JAX F2-C compatibility is unsupported: platform expects "
                f"{F2C_EXACT_PAIR_COMPATIBILITY!r}, JAX at {root_path} exposes {encontrado!r}")
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
    """Preserve non-governed prose without relying on Markdown presentation.

    Decisión de Fernando (2026-10-05): el usuario ve SOLO el juicio; el
    análisis es razonamiento interno del modelo y no se muestra. Con juicio
    vacío o ausente se muestra el análisis, para no dejar la respuesta vacía.
    """
    if contract.judgment and contract.judgment.strip():
        return contract.judgment.strip()
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
        lifecycle = _lifecycle_core()
        unit = lifecycle.mint_governed_transport_unit(
            envelope, rendered, render_context, transport_kind="web-chat-http-json",
            idempotency_key=str(uuid.uuid4()),
        )
        degraded = rendered.contract_state.value != "VALID"
        return GovernedChatProjection(
            text=rendered.text, response_id=rendered.response_id,
            envelope_digest=rendered.envelope_digest,
            source_envelope_digest=rendered.source_envelope_digest,
            contract_state=rendered.contract_state.value,
            contract_degraded=degraded, governed_plain=True, transport_unit=unit,
        )
    except Exception as exc:  # fail-soft: never expose an envelope or text if the renderer fails
        # Sin exc_info el fallo era invisible (2026-10-05: 100 % de los chats caidos, journal
        # vacio). No se vuelca el envelope ni texto del proveedor: solo la excepcion.
        _registrar_fallo_cerrado("F2-C project_sealed_envelope failed closed (response withheld)",
                                 exc=exc)
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
        lifecycle = _lifecycle_core()
        idempotency_key = hashlib.sha256(
            f"{scope.scope_digest}:{request_id}:{response_id}:web-chat-http-json".encode("utf-8")
        ).hexdigest()
        unit = lifecycle.mint_governed_transport_unit(
            envelope, rendered, context, transport_kind="web-chat-http-json",
            idempotency_key=idempotency_key,
        )
        return GovernedChatProjection(
            text=rendered.text, response_id=rendered.response_id,
            envelope_digest=rendered.envelope_digest,
            source_envelope_digest=rendered.source_envelope_digest,
            contract_state=rendered.contract_state.value,
            contract_degraded=rendered.contract_state.value != "VALID" or degraded,
            governed_plain=True, transport_unit=unit,
        )
    except Exception as exc:  # fail-soft: renderer/core failure emits only static non-current text, never provider prose
        # Visible: un par plataforma/jax incompatible llega hasta aca en cada turno y el
        # cliente solo ve un 503 generico. Solo request_id/trace_id, nunca el contrato ni el texto.
        _registrar_fallo_cerrado(
            "F2-C project_provider_contract failed closed (request_id=%s trace_id=%s)",
            request_id, trace_id, exc=exc)
        # This is the only F2-C bridge failure fallback.  It is static,
        # server-owned and contains no provider candidate text.
        return GovernedChatProjection(
            text=_DEGRADED_NOTICE, response_id=None, envelope_digest=None,
            source_envelope_digest=None,
            contract_state="UNAVAILABLE", contract_degraded=True,
            governed_plain=True,
        )


def _lifecycle_core():
    """Load the paired JAX-owned F2-D lifecycle API, never a stale import."""
    root = os.environ.get("JAX_REPO_PATH")
    if not root or not os.path.isabs(root):
        raise GovernedChatUnavailable("JAX_REPO_PATH is unavailable for F2-D lifecycle")
    root_path = Path(root).resolve()
    source = root_path / "policy" / "governance" / "output_lifecycle.py"
    if not source.is_file():
        raise GovernedChatUnavailable("configured JAX repository lacks F2-D lifecycle API")
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        module = importlib.import_module("policy.governance.output_lifecycle")
        if not Path(module.__file__).resolve().is_relative_to(root_path):
            raise GovernedChatUnavailable("loaded F2-D API is outside configured JAX repository")
        module.validate_lifecycle_version(module.OUTPUT_LIFECYCLE_API_VERSION)
        if module.OUTPUT_LIFECYCLE_API_VERSION != F2D_LIFECYCLE_API_VERSION:
            raise GovernedChatUnavailable(
                "configured JAX F2-D lifecycle API is unsupported: platform expects "
                f"{F2D_LIFECYCLE_API_VERSION!r}, JAX at {root_path} exposes "
                f"{module.OUTPUT_LIFECYCLE_API_VERSION!r}")
        return module
    except (ImportError, AttributeError) as exc:
        raise GovernedChatUnavailable("F2-D lifecycle API is unavailable") from exc
