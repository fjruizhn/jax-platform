"""Govern the authenticated pipeline-list DTO with the exact paired JAX core."""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Response

from auth.models import AuthUser


class GovernedPipelineListUnavailable(RuntimeError):
    """The list cannot cross the governed structured-output boundary."""


_SAFE_FALLBACK = b'{"detail":{"code":"governed_output_unavailable"}}'
_F2C_TUPLE = ("f2-c.renderer.3", "f2-c.domain.6", frozenset({"f2-c.1"}))
_RUNTIME_STATUS_VERSION = "f2-e.runtime-status.3"
_STRUCTURED_PROJECTION_VERSION = "f2-c.structured-projection.1"
_STRUCTURED_BYTES_VERSION = "f2-d.structured-bytes.1"
logger = logging.getLogger(__name__)


def _environment() -> str:
    configured = os.environ.get("JAX_ENVIRONMENT", "production")
    return configured if configured in {"production", "staging", "test", "development"} else "production"


def _paired_core():
    root = os.environ.get("JAX_REPO_PATH", "")
    if not root or not os.path.isabs(root):
        raise GovernedPipelineListUnavailable("paired JAX checkout is not configured")
    root_path = Path(root).resolve()
    required = (
        "policy/governance/governed_renderer.py",
        "policy/governance/governed_domain.py",
        "policy/governance/response.py",
        "policy/governance/runtime_status.py",
        "policy/governance/resolution.py",
        "policy/governance/structured_projection.py",
        "policy/governance/structured_lifecycle.py",
    )
    if any(not (root_path / relative).is_file() for relative in required):
        raise GovernedPipelineListUnavailable("paired JAX checkout lacks structured governance APIs")
    root_text = str(root_path)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    try:
        domain = importlib.import_module("policy.governance.governed_domain")
        renderer = importlib.import_module("policy.governance.governed_renderer")
        response = importlib.import_module("policy.governance.response")
        resolution = importlib.import_module("policy.governance.resolution")
        runtime = importlib.import_module("policy.governance.runtime_status")
        projection = importlib.import_module("policy.governance.structured_projection")
        lifecycle = importlib.import_module("policy.governance.structured_lifecycle")
        modules = (domain, renderer, response, resolution, runtime, projection, lifecycle)
        if any(not Path(module.__file__).resolve().is_relative_to(root_path) for module in modules):
            raise GovernedPipelineListUnavailable("loaded governance modules are outside configured JAX checkout")
        if (domain.GOVERNED_RENDERER_API_VERSION,
                domain.GOVERNED_DOMAIN_SPEC_VERSION,
                domain.GOVERNED_ENVELOPE_SCHEMA_VERSIONS) != _F2C_TUPLE:
            raise GovernedPipelineListUnavailable("paired JAX F2-C compatibility tuple is unsupported")
        if runtime.RUNTIME_STATUS_API_VERSION != _RUNTIME_STATUS_VERSION:
            raise GovernedPipelineListUnavailable("paired JAX runtime-status API is unsupported")
        if projection.STRUCTURED_PROJECTION_API_VERSION != _STRUCTURED_PROJECTION_VERSION:
            raise GovernedPipelineListUnavailable("paired JAX structured projection API is unsupported")
        if lifecycle.STRUCTURED_BYTES_LIFECYCLE_API_VERSION != _STRUCTURED_BYTES_VERSION:
            raise GovernedPipelineListUnavailable("paired JAX structured lifecycle API is unsupported")
    except (ImportError, AttributeError, OSError) as exc:
        raise GovernedPipelineListUnavailable("paired JAX governance API is unavailable") from exc
    return domain, renderer, response, resolution, runtime, projection, lifecycle, root_path


def _validate_loaded_jacobs_origin(root_path: Path) -> None:
    for module_name in ("jacobs", "jacobs.store", "jacobs.models"):
        module = sys.modules.get(module_name)
        if module is None or not Path(module.__file__).resolve().is_relative_to(root_path):
            raise GovernedPipelineListUnavailable("canonical Jacobs modules are outside paired JAX checkout")


def _platform_status_source_configuration():
    from jax_engine.state import las_manos_health_source_configuration

    return {
        "FACET_RUNTIME_STATUS": {
            "state_contract": "JAXEngineState.FacetState",
            "status_field": "status",
            "observed_at_field": "resolver_read_time",
            "allowed_statuses": ["idle", "thinking", "error", "offline"],
        },
        "ENGINE_STATUS": las_manos_health_source_configuration(),
    }


async def govern_pipeline_list(payload: dict, user: AuthUser) -> "GovernedPipelineListResponse":
    """Return exact F2-C canonical bytes or a static server-owned fallback."""
    try:
        core = _paired_core()
    except GovernedPipelineListUnavailable as exc:
        logger.warning("governed pipeline-list unavailable (%s)", type(exc).__name__)
        return GovernedPipelineListResponse(_SAFE_FALLBACK, status_code=503)
    try:
        return await _govern_pipeline_list(payload, user, core)
    except (GovernedPipelineListUnavailable, core[2].GovernanceContractError,
            TypeError, ValueError, KeyError, ImportError,
            AttributeError, OSError, RuntimeError, OverflowError) as exc:
        # Fail closed: exception details and candidate DTO values never leave.
        logger.warning("governed pipeline-list unavailable (%s)", type(exc).__name__)
        return GovernedPipelineListResponse(_SAFE_FALLBACK, status_code=503)


async def _govern_pipeline_list(payload: dict, user: AuthUser, core=None) -> "GovernedPipelineListResponse":
    domain, renderer, response, resolution, runtime, structured, f2d, root_path = core or _paired_core()
    if not isinstance(payload, dict) or not isinstance(user, AuthUser):
        raise GovernedPipelineListUnavailable("authenticated producer payload is invalid")
    request_id, trace_id, response_id = (str(uuid.uuid4()) for _ in range(3))
    scope = response.ResponseScope(
        environment=_environment(),
        tenant_id=str(user.tenant_id), project_id=None, subject_id=str(user.user_id),
        actor_id=f"authenticated-user:{user.user_id}", audience="authenticated_user",
        component_id="pipeline-list", request_id=request_id, trace_id=trace_id,
    )
    authenticator = resolution.ReceiptAuthenticator(
        secrets.token_bytes(32), key_id=f"pipeline-list:{response_id}")
    registry = runtime.build_runtime_status_registry(
        scope, authenticator=authenticator,
        platform_source_configuration=_platform_status_source_configuration())
    resolver = runtime.JacobsPipelineStatusResolver()

    arguments_seq = []
    source_rows = []
    seen_pipeline_ids = set()
    for row in payload.get("pipelines", []):
        if not isinstance(row, dict):
            raise GovernedPipelineListUnavailable("producer emitted invalid pipeline row")
        pipeline_id, status = row.get("pipeline_id"), row.get("status")
        if not isinstance(pipeline_id, str) or not pipeline_id or not isinstance(status, str):
            raise GovernedPipelineListUnavailable("producer emitted invalid pipeline identity or status")
        if pipeline_id in seen_pipeline_ids:
            raise GovernedPipelineListUnavailable("producer emitted duplicate pipeline identity")
        seen_pipeline_ids.add(pipeline_id)
        source_rows.append(row)
        arguments_seq.append({"pipeline_id": pipeline_id, "status": status})

    evidences = await resolver.evidence_many(tuple(arguments_seq), scope)
    if not isinstance(evidences, tuple) or len(evidences) != len(arguments_seq):
        raise GovernedPipelineListUnavailable("canonical pipeline status batch cardinality is invalid")
    for arguments, evidence in zip(arguments_seq, evidences, strict=True):
        evidence_scope = getattr(evidence, "observation_scope", None)
        if evidence_scope is None or evidence_scope.scope_digest != scope.scope_digest:
            raise GovernedPipelineListUnavailable("canonical pipeline status batch scope is invalid")

    tool_rows = []
    claims = []
    references = []
    receipts = {}
    lookups = {}
    claim_ids = []
    for index, (row, arguments, evidence) in enumerate(zip(source_rows, arguments_seq, evidences, strict=True)):
        receipt = registry.resolve("PIPELINE_STATUS", arguments, scope,
            validation_time=datetime.now(timezone.utc), runtime_status_evidence=evidence)
        if receipt.status is not resolution.ResolutionStatus.RESOLVED:
            raise GovernedPipelineListUnavailable("canonical pipeline status did not resolve")
        _validate_loaded_jacobs_origin(root_path)
        claim_id = f"pipeline-status-{index}-{uuid.uuid4().hex}"
        receipt_ref_id = f"resolution-ref-{uuid.uuid4().hex}"
        claim = response.ClaimRecord(
            claim_id=claim_id, predicate="PIPELINE_STATUS", typed_arguments=arguments,
            claim_scope=scope, source_class=response.SourceClass.CURRENT_SOURCE,
            epistemic_status=response.EpistemicStatus.CURRENT_OBSERVATION,
            resolution_receipt_ref=receipt_ref_id,
            disposition=response.ClaimDisposition.ASSERTABLE,
            template_contract=response.TemplateContract("PIPELINE_STATUS",
                "f2-e.runtime-status.3", "es"),
        )
        reference = response.ReferenceRef(
            receipt_ref_id, response.ReferenceType.RESOLUTION_RECEIPT,
            f"axioma://{receipt_ref_id}", f"immutable:{receipt_ref_id}",
            receipt.receipt_id, scope.scope_digest,
            response.TemporalClass.CURRENT, response.ExistenceState.PRESENT)
        lookup = resolution.ReferenceLookupRecord(
            reference.ref_id, reference.ref_type, reference.canonical_locator,
            reference.immutable_identity, reference.revision_or_digest,
            reference.scope_digest, reference.temporal_class,
            reference.existence_state, True)
        claims.append(claim)
        references.append(reference)
        receipts[receipt_ref_id] = receipt
        lookups[receipt_ref_id] = lookup
        claim_ids.append(claim_id)
        untrusted_row = {key: value for key, value in row.items() if key != "status"}
        tool_rows.append(untrusted_row)

    tool_data = {key: value for key, value in payload.items() if key != "pipelines"}
    tool_data["pipelines"] = tool_rows
    encoded_data = json.dumps(tool_data, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False)
    blocks = (
        response.ContentBlock(response.ContentBlockKind.TOOL_DATA, encoded_data),
        *( (response.ContentBlock(response.ContentBlockKind.CLAIM_REF_BLOCK,
             claim_refs=tuple(claim_ids)),) if claim_ids else () ),
    )
    candidate = response.GovernedResponseCandidate(
        "f2-c.1", response_id, request_id, trace_id, scope, "pipeline-list", (),
        blocks, tuple(claims), tuple(references))
    envelope = response._seal_candidate_for_server(
        candidate, contract_state=response.ContractState.VALID,
        governance_receipt=response.GovernanceReceipt(
            "f2-e-structured.1", "predicates.yaml", registry.snapshot_digest,
            structured.STRUCTURED_PROJECTION_API_VERSION, "f2-c.structured-projection.1"))
    context = renderer.RenderContext(
        registry, receipts=receipts, domain_registry=renderer.GovernedDomainRegistry(),
        reference_validator=lambda ref, current_scope: (
            ref.scope_digest == current_scope.scope_digest
            and lookups.get(ref.ref_id) == resolution.ReferenceLookupRecord(
                ref.ref_id, ref.ref_type, ref.canonical_locator, ref.immutable_identity,
                ref.revision_or_digest, ref.scope_digest, ref.temporal_class,
                ref.existence_state, True)),
        now=lambda: datetime.now(timezone.utc),
        receipt_reference_resolver=lambda ref, current_scope: (
            lookups.get(ref.ref_id) if ref.scope_digest == current_scope.scope_digest else None),
    )
    projection = structured.GovernedStructuredRenderer().render_json(envelope, context)
    prepared = f2d.prepare_structured_output(envelope, projection, context, now=datetime.now(timezone.utc))
    return GovernedPipelineListResponse(
        prepared.canonical_bytes, status_code=200, transport_unit=prepared.transport_unit,
        f2d=f2d)


class GovernedPipelineListResponse(Response):
    """Send only prepared canonical bytes and record the real ASGI commit edge."""

    media_type = "application/json"

    def __init__(self, body: bytes, *, status_code: int,
                 transport_unit=None, f2d=None) -> None:
        self._transport_unit = transport_unit
        self._f2d = f2d
        self.lifecycle_state = (f2d.OutputLifecycleState.OUTPUT_PREPARED
            if transport_unit is not None else None)
        super().__init__(content=body, status_code=status_code, media_type=self.media_type)

    async def __call__(self, scope, receive, send) -> None:
        if self._transport_unit is None:
            await super().__call__(scope, receive, send)
            return
        state = self._f2d.OutputLifecycleState
        try:
            exact_bytes = self._f2d.revalidate_structured_for_transport(
                self._transport_unit, datetime.now(timezone.utc))
            if exact_bytes is not self._transport_unit.canonical_bytes:
                raise GovernedPipelineListUnavailable("transport altered canonical bytes")
            self._f2d.validate_structured_lifecycle_transition(
                state.OUTPUT_PREPARED, state.TRANSPORT_COMMITTING)
            self.lifecycle_state = state.TRANSPORT_COMMITTING
            await send({"type": "http.response.start", "status": self.status_code,
                "headers": self.raw_headers})
            await send({"type": "http.response.body", "body": exact_bytes, "more_body": False})
        except BaseException:
            if self.lifecycle_state is state.TRANSPORT_COMMITTING:
                self.lifecycle_state = state.TRANSPORT_OUTCOME_UNKNOWN
            raise
        self._f2d.validate_structured_lifecycle_transition(
            state.TRANSPORT_COMMITTING, state.OUTPUT_COMMITTED_TO_TRANSPORT)
        self.lifecycle_state = state.OUTPUT_COMMITTED_TO_TRANSPORT
