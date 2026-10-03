"""Exact paired-JAX F2-C/F2-D integration proof.

This test intentionally composes a test-only F2-B authenticator and registry;
it never reads or provisions a production receipt key. The F2-D success path
uses a deterministic recording repository here; MariaDB durability, restart,
and concurrency are exercised by the separate DB-backed platform tests.
"""
from dataclasses import replace
from datetime import datetime, timezone
import os

import pytest

from api.chat import ChatResponse, _parse_contract_response
from api.governed_chat import (
    F2C_EXACT_PAIR_COMPATIBILITY,
    GovernedChatUnavailable,
    _core,
    project_provider_contract,
    project_sealed_envelope,
)


def _f2b_composition():
    import policy.governance.response as response
    import policy.governance.resolution as resolution
    from policy.governance.governed_domain import GOVERNED_RENDERER_API_VERSION
    from policy.governance.governed_renderer import GovernedDomainRegistry, RenderContext
    from policy.governance.response import (
        ClaimDisposition, ClaimRecord, ContentBlock, ContentBlockKind,
        ContractState, EpistemicStatus, GovernanceReceipt, ReferenceRef,
        ReferenceType, ResponseScope, SourceClass, TemporalClass,
        TemplateContract, ExistenceState,
    )
    from policy.governance.resolution import (
        AdapterKind, ConflictPolicy, GovernedResolutionReceipt,
        PredicateAuthorityBinding, ReceiptAuthenticator, RegistryEntry,
        ReferenceLookupRecord, ResolutionObservation, ResolutionStatus, ResolverRegistry,
        ScopeRule, ServerAdapterInput, TrustedAdapterRegistration,
    )

    now = datetime.now(timezone.utc)
    scope = ResponseScope("test", "1", None, "7",
        "service:web-chat-test", "user:7", "web-chat", "request-it", "trace-it")
    rule = ScopeRule(scope.environment, scope.tenant_id, scope.project_id,
        scope.subject_id, scope.actor_id, scope.audience, scope.component_id)
    binding = PredicateAuthorityBinding("CAPABILITY_AVAILABLE", "v1",
        "test:capabilities", "authority:test", scope.environment, rule, rule,
        60, ConflictPolicy.SINGLE_SOURCE_REQUIRED, "test:resolver", "1",
        "sha256:" + "a" * 64, "test-binding-v1")
    template_contract_ref = "capability@1:en"
    entry = RegistryEntry(binding, TrustedAdapterRegistration(
        AdapterKind.CAPABILITY_AVAILABLE, "test:resolver", "1",
        "test:capabilities", binding.source_configuration_digest, {}),
        ("name", "mode"), template_contract_ref)
    registry = resolution._build_approved_registry_for_server(
        (entry,), authenticator=ReceiptAuthenticator.for_testing(
            b"non-production-f2-c-test-authenticator-material"))
    arguments = {"name": "x", "mode": "read"}
    observation = ResolutionObservation(ResolutionStatus.RESOLVED, now,
        "test:artifact", {"available": True})
    server_input = ServerAdapterInput._mint(resolution._ADAPTER_INPUT_TOKEN,
        (("test:capabilities", observation),))
    resolution_receipt = registry.resolve("CAPABILITY_AVAILABLE", arguments,
        scope, validation_time=now, server_input=server_input)
    ref = ReferenceRef("resolution-ref", ReferenceType.RESOLUTION_RECEIPT,
        "test://receipt", "test-receipt-identity", resolution_receipt.receipt_id,
        scope.scope_digest, TemporalClass.CURRENT, ExistenceState.PRESENT)
    claim = ClaimRecord("claim-it", "CAPABILITY_AVAILABLE", arguments, scope,
        SourceClass.CURRENT_SOURCE, EpistemicStatus.CURRENT_OBSERVATION,
        resolution_receipt_ref=ref.ref_id, disposition=ClaimDisposition.ASSERTABLE,
        template_contract=TemplateContract("capability", "1", "en"))
    governance_receipt = GovernanceReceipt("test-policy", "test-vocab",
        registry.snapshot_digest, "test-validator", GOVERNED_RENDERER_API_VERSION)
    candidate = response.GovernedResponseCandidate("f2-c.1", "response-it",
        scope.request_id, scope.trace_id, scope, "web-chat", (),
        (ContentBlock(ContentBlockKind.CLAIM_REF_BLOCK, claim_refs=(claim.claim_id,)),),
        (claim,), (ref,))
    envelope = response._seal_candidate_for_server(candidate,
        contract_state=ContractState.VALID, governance_receipt=governance_receipt)
    trusted_references = {ref.ref_id: ReferenceLookupRecord(ref.ref_id,
        ref.ref_type, ref.canonical_locator, ref.immutable_identity,
        ref.revision_or_digest, ref.scope_digest, ref.temporal_class,
        ref.existence_state, True)}

    def resolve_trusted_reference(candidate_ref, candidate_scope):
        known = trusted_references.get(candidate_ref.ref_id)
        if (known is None or known.ref_type is not candidate_ref.ref_type
                or known.canonical_locator != candidate_ref.canonical_locator
                or known.immutable_identity != candidate_ref.immutable_identity
                or known.revision_or_digest != candidate_ref.revision_or_digest
                or known.scope_digest != candidate_ref.scope_digest
                or known.scope_digest != candidate_scope.scope_digest
                or known.temporal_class is not candidate_ref.temporal_class
                or known.existence_state is not candidate_ref.existence_state
                or not known.accessible):
            return None
        return known

    def validate_trusted_reference(candidate_ref, candidate_scope):
        return resolve_trusted_reference(candidate_ref, candidate_scope) is not None

    context = RenderContext(registry, {ref.ref_id: resolution_receipt},
        {("capability", "1", "en"): "Capability {name} is available."}, {},
        GovernedDomainRegistry(), validate_trusted_reference, lambda: now,
        receipt_reference_resolver=resolve_trusted_reference)
    return envelope, context, governance_receipt


def test_f2c_exact_pair_accepts_current_contract_and_rejects_old_or_future(monkeypatch):
    """Platform accepts only the reviewed JAX F2-C contract triple."""
    monkeypatch.setenv("JAX_REPO_PATH", os.environ["JAX_REPO_PATH"])
    import policy.governance.governed_domain as domain

    assert _core()
    assert F2C_EXACT_PAIR_COMPATIBILITY == (
        "f2-c.renderer.3", "f2-c.domain.5", frozenset({"f2-c.1"})
    )

    for renderer_version, domain_version, envelope_versions in (
        ("f2-c.renderer.2", "f2-c.domain.2", frozenset({"f2-c.1"})),
        ("f2-c.renderer.4", "f2-c.domain.6", frozenset({"f2-c.1"})),
        ("f2-c.renderer.3", "f2-c.domain.5", frozenset({"f2-c.2"})),
    ):
        with monkeypatch.context() as patched:
            patched.setattr(domain, "GOVERNED_RENDERER_API_VERSION", renderer_version)
            patched.setattr(domain, "GOVERNED_DOMAIN_SPEC_VERSION", domain_version)
            patched.setattr(domain, "GOVERNED_ENVELOPE_SCHEMA_VERSIONS", envelope_versions)
            with pytest.raises(GovernedChatUnavailable, match="compatibility is unsupported"):
                _core()


def test_exact_pair_bridge_blocks_narrative_and_renders_valid_supported_claim(monkeypatch):
    monkeypatch.setenv("JAX_REPO_PATH", os.environ["JAX_REPO_PATH"])
    from api.governed_chat import _core
    _core()
    envelope, context, governance_receipt = _f2b_composition()
    from policy.governance.governed_renderer import GovernedRenderer
    assert GovernedRenderer().render_text(envelope, context).contract_state.value == "VALID"
    projection = project_sealed_envelope(envelope, context)
    response = ChatResponse(facet="jekyll", response=projection.text,
        timestamp="2026-09-29T12:00:00Z", contract_degraded=projection.contract_degraded,
        response_id=projection.response_id, envelope_digest=projection.envelope_digest,
        source_envelope_digest=projection.source_envelope_digest,
        contract_state=projection.contract_state, governed_plain=projection.governed_plain)
    assert response.contract_state == "VALID"
    assert response.contract_degraded is False
    assert response.response == "Capability x is available."
    assert response.governed_plain is True
    assert response.envelope_digest.startswith("sha256:")
    assert response.source_envelope_digest == envelope.envelope_digest

    # Receipt verification resolves the original immutable reference from
    # test-owned trusted state; caller substitutions cannot borrow it.
    from policy.governance.response import _seal_candidate_for_server
    for changed_ref in (
        replace(envelope.references[0], immutable_identity="substituted-identity"),
        replace(envelope.references[0], canonical_locator="test://substituted"),
    ):
        substituted = _seal_candidate_for_server(
            replace(envelope.candidate, references=(changed_ref,)),
            contract_state=envelope.contract_state,
            governance_receipt=governance_receipt)
        rejected = project_sealed_envelope(substituted, context)
        assert rejected.contract_state == "UNAVAILABLE"
        assert rejected.text != "Capability x is available."

    hostile = _parse_contract_response(
        '{"claim": [], "analysis": "Hall9000 is healthy.", "judgment": null}')
    blocked = project_provider_contract(hostile, memory_scope=__import__("jax.memory.b9", fromlist=["ScopeContext"]).ScopeContext(
        actor_principal="user:7", actor_type="USER", subject_user_id="7",
        tenant_id="1", project_id=None, calling_component="jax-platform-web-chat"), user_id="7")
    assert blocked.contract_state == "UNAVAILABLE"
    assert "Hall9000" not in blocked.text

    curly = _parse_contract_response(
        '{"claim": [], "analysis": "Hall9000 isn’t healthy.", "judgment": null}')
    curly_blocked = project_provider_contract(curly, memory_scope=__import__("jax.memory.b9", fromlist=["ScopeContext"]).ScopeContext(
        actor_principal="user:7", actor_type="USER", subject_user_id="7",
        tenant_id="1", project_id=None, calling_component="jax-platform-web-chat"), user_id="7")
    assert curly_blocked.contract_state == "UNAVAILABLE"
    assert "Hall9000" not in curly_blocked.text

    # A valid receipt for the original semantic arguments cannot support a
    # privileged substitute even through the actual platform bridge.
    candidate = replace(envelope.candidate, claims=(replace(
        envelope.claims[0], typed_arguments={"name": "root_shell", "mode": "admin"}),))
    mismatched = __import__("policy.governance.response", fromlist=["_seal_candidate_for_server"])._seal_candidate_for_server(
        candidate, contract_state=envelope.contract_state,
        governance_receipt=governance_receipt)
    denied = project_sealed_envelope(mismatched, context)
    assert denied.contract_state == "UNAVAILABLE"
    assert denied.text != "Capability root_shell is available."


def test_exact_pair_bridge_version_or_import_failure_is_static_safe(monkeypatch):
    monkeypatch.setenv("JAX_REPO_PATH", "/tmp/f2c-exact-pair-missing")
    raw = '{"claim": [], "analysis": "# VERIFIED Hall9000 is healthy", "judgment": null}'
    result = project_provider_contract(_parse_contract_response(raw), memory_scope=type("Scope", (), {
        "tenant_id": "1", "project_id": None, "subject_user_id": "7", "actor_principal": "user:7"})(), user_id="7")
    assert result.contract_state == "UNAVAILABLE"
    assert result.contract_degraded is True and result.governed_plain is True
    assert "Hall9000" not in result.text and "VERIFIED" not in result.text


def test_exact_pair_supported_claim_is_prepared_and_committed_as_exact_asgi_bytes(monkeypatch):
    """Exercise JAX F2-D authority through the platform Web Chat transport adapter."""
    import asyncio
    import hashlib
    import json

    from api.chat import ChatResponse
    from auth.models import AuthUser
    from jax.memory.b9 import ScopeContext
    from webchat_f2d import repository as outbox
    from webchat_f2d.transport import prepare_governed_chat_response

    monkeypatch.setenv("JAX_REPO_PATH", os.environ["JAX_REPO_PATH"])
    envelope, context, _ = _f2b_composition()
    from api.governed_chat import project_sealed_envelope

    governed = project_sealed_envelope(envelope, context)
    assert governed.transport_unit is not None
    assert governed.contract_state == "VALID"
    assert governed.text == "Capability x is available."
    response = ChatResponse(
        facet="jekyll", response=governed.text, timestamp="2026-09-29T12:00:00Z",
        contract_degraded=governed.contract_degraded,
        response_id=governed.response_id, envelope_digest=governed.envelope_digest,
        source_envelope_digest=governed.source_envelope_digest,
        contract_state=governed.contract_state, governed_plain=True,
    )
    user = AuthUser(user_id="7", tenant_id="1", role="operator")
    memory_scope = ScopeContext(
        actor_principal="user:7", actor_type="USER", subject_user_id="7",
        tenant_id="1", project_id=None, calling_component="jax-platform-web-chat",
    )

    class ExactPairRecordingTestRepository:
        def __init__(self):
            self.state = None
            self.payload = None
            self.transitions = []

        async def prepare(self, unit, payload, *, tenant_id, project_id, subject_id, request_id,
                          previous_attempt_id=None):
            assert unit is governed.transport_unit
            assert tenant_id == 1 and project_id is None and subject_id == "7"
            assert request_id == unit.request_id
            self.payload = payload
            projection = unit.durable_projection()
            self.state = "OUTPUT_PREPARED"
            return outbox.PreparedTransportAuthorization._mint(
                outbox._AUTH_TOKEN,
                outbox_id="test-outbox", attempt_id="test-attempt", tenant_id=tenant_id,
                scope_digest=projection["scope_digest"], request_id=request_id,
                response_id=projection["response_id"], subject_id=subject_id,
                idempotency_key=projection["idempotency_key"],
                effective_output_digest=projection["effective_output_digest"],
                effective_projection_digest=projection["effective_projection_digest"],
                original_envelope_digest=projection["original_envelope_digest"],
                contract_state=projection["effective_contract_state"],
                transport_payload_digest="sha256:" + hashlib.sha256(payload).hexdigest(),
                payload=payload, unit=unit,
            )

        async def transition(self, authorization, target, *, failure_class=None, before_send=False):
            from api.governed_chat import _lifecycle_core
            core = _lifecycle_core()
            current = core.OutputLifecycleState(self.state)
            core.validate_lifecycle_transition(current, target, before_send=before_send)
            self.transitions.append((current.value, target.value))
            self.state = target.value

    repository = ExactPairRecordingTestRepository()
    prepared_response = asyncio.run(prepare_governed_chat_response(
        response=response, transport_unit=governed.transport_unit, user=user,
        memory_scope=memory_scope,
        trusted_metadata={"facet": response.facet, "timestamp": response.timestamp,
                          "contract_degraded": response.contract_degraded},
        on_commit=lambda: asyncio.sleep(0), repository=repository,
    ))
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(prepared_response(
        {"type": "http", "method": "POST", "path": "/api/chat"}, receive, send,
    ))
    body = next(item["body"] for item in messages if item["type"] == "http.response.body")
    assert repository.state == "OUTPUT_COMMITTED_TO_TRANSPORT"
    assert repository.transitions == [
        ("OUTPUT_PREPARED", "TRANSPORT_COMMITTING"),
        ("TRANSPORT_COMMITTING", "OUTPUT_COMMITTED_TO_TRANSPORT"),
    ]
    assert body == repository.payload
    assert json.loads(body)["response"] == "Capability x is available."


def test_exact_pair_rejected_narrative_fallback_is_prepared_sent_and_never_projects_candidate(monkeypatch):
    """The F2-C effective UNAVAILABLE fallback, never rejected text, is F2-D output."""
    import asyncio
    import hashlib
    import json

    from api.chat import ChatResponse
    from auth.models import AuthUser
    from jax.memory.b9 import ScopeContext
    from webchat_f2d import repository as outbox
    from webchat_f2d.transport import prepare_governed_chat_response

    monkeypatch.setenv("JAX_REPO_PATH", os.environ["JAX_REPO_PATH"])
    scope = ScopeContext(
        actor_principal="user:7", actor_type="USER", subject_user_id="7",
        tenant_id="1", project_id=None, calling_component="jax-platform-web-chat",
    )
    rejected_candidate = "Hall9000 is healthy."
    governed = project_provider_contract(
        _parse_contract_response(
            '{"claim": [], "analysis": "Hall9000 is healthy.", "judgment": null}'),
        memory_scope=scope, user_id="7", request_id="fallback-request",
    )
    assert governed.transport_unit is not None
    assert governed.contract_state == "UNAVAILABLE"
    assert rejected_candidate not in governed.text
    response = ChatResponse(
        facet="jekyll", response=governed.text, timestamp="2026-10-01T00:00:00Z",
        contract_degraded=governed.contract_degraded,
        response_id=governed.response_id, envelope_digest=governed.envelope_digest,
        source_envelope_digest=governed.source_envelope_digest,
        contract_state=governed.contract_state, governed_plain=True,
    )
    user = AuthUser(user_id="7", tenant_id="1", role="operator")

    class RecordingRepository:
        def __init__(self):
            self.state = None
            self.payload = None
            self.authorization = None

        async def prepare(self, unit, payload, *, tenant_id, project_id, subject_id, request_id,
                          previous_attempt_id=None):
            self.payload = payload
            projection = unit.durable_projection()
            self.state = "OUTPUT_PREPARED"
            self.authorization = outbox.PreparedTransportAuthorization._mint(
                outbox._AUTH_TOKEN, outbox_id="fallback-outbox", attempt_id="fallback-attempt",
                tenant_id=tenant_id, scope_digest=projection["scope_digest"], request_id=request_id,
                response_id=projection["response_id"], subject_id=subject_id,
                idempotency_key=projection["idempotency_key"],
                effective_output_digest=projection["effective_output_digest"],
                effective_projection_digest=projection["effective_projection_digest"],
                original_envelope_digest=projection["original_envelope_digest"],
                contract_state=projection["effective_contract_state"],
                transport_payload_digest="sha256:" + hashlib.sha256(payload).hexdigest(),
                payload=payload, unit=unit,
            )
            return self.authorization

        async def transition(self, authorization, target, *, failure_class=None, before_send=False):
            from api.governed_chat import _lifecycle_core
            core = _lifecycle_core()
            core.validate_lifecycle_transition(core.OutputLifecycleState(self.state), target,
                                               before_send=before_send)
            self.state = target.value

        async def record_secondary_event(self, authorization, event_type):
            return None

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    repository = RecordingRepository()
    history = []
    prepared = asyncio.run(prepare_governed_chat_response(
        response=response, transport_unit=governed.transport_unit, user=user,
        memory_scope=scope,
        trusted_metadata={"facet": response.facet, "timestamp": response.timestamp,
                          "contract_degraded": response.contract_degraded},
        on_commit=lambda: _append(history, governed.text), repository=repository,
    ))
    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(prepared({"type": "http", "method": "POST", "path": "/api/chat"}, receive, send))
    body = next(item["body"] for item in messages if item["type"] == "http.response.body")
    decoded = json.loads(body)
    assert repository.state == "OUTPUT_COMMITTED_TO_TRANSPORT"
    assert decoded["response"] == governed.text
    assert decoded["contract_state"] == "UNAVAILABLE"
    assert rejected_candidate not in body.decode("utf-8")
    assert body == repository.payload
    assert repository.authorization.effective_output_digest == governed.envelope_digest
    assert history == [governed.text]
    assert all(rejected_candidate not in item for item in history)

    # A post-prepare send failure leaves the effective fallback prepared/unknown
    # and does not invoke successful assistant-history projection.
    failed_repository = RecordingRepository()
    failed_history = []
    failed = asyncio.run(prepare_governed_chat_response(
        response=response, transport_unit=governed.transport_unit, user=user,
        memory_scope=scope,
        trusted_metadata={"facet": response.facet, "timestamp": response.timestamp,
                          "contract_degraded": response.contract_degraded},
        on_commit=lambda: _append(failed_history, governed.text), repository=failed_repository,
    ))

    async def fail_send(message):
        if message["type"] == "http.response.body":
            raise OSError("simulated fallback transport failure")

    import pytest
    with pytest.raises(OSError, match="fallback transport failure"):
        asyncio.run(failed({"type": "http", "method": "POST", "path": "/api/chat"},
                           receive, fail_send))
    assert failed_repository.state == "TRANSPORT_OUTCOME_UNKNOWN"
    assert failed_history == []
    assert failed_repository.payload is not None
    assert rejected_candidate.encode("utf-8") not in failed_repository.payload


async def _append(target, value):
    target.append(value)


def test_exact_pair_usage_none_runtime_notice_enters_f2d_before_transport(monkeypatch):
    """The real chat route cannot return a runtime notice as a direct shortcut."""
    import asyncio
    import hashlib
    import json
    from types import SimpleNamespace

    from api import chat as chat_mod
    from auth.models import AuthUser
    from fastapi import BackgroundTasks
    from webchat_f2d import repository as outbox
    from webchat_f2d import transport
    from jax.memory.b9 import ScopeContext

    monkeypatch.setenv("JAX_REPO_PATH", os.environ["JAX_REPO_PATH"])
    scope = ScopeContext(
        actor_principal="user:7", actor_type="USER", subject_user_id="7",
        tenant_id="1", project_id=None, calling_component="jax-platform-web-chat",
    )
    runtime_notice = chat_mod.AvisoDeChat(code="faceta_sin_binding")
    projected = []

    async def scope_for_chat(*_args):
        return scope

    async def no_conversation(*_args):
        return None

    async def invoke(*_args, **_kwargs):
        return runtime_notice, None

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(chat_mod, "_load_config", lambda: {"personalities": {"jekyll": {}}})
    monkeypatch.setattr(chat_mod, "_scope_for_chat", scope_for_chat)
    monkeypatch.setattr(chat_mod, "_get_conv_uuid", no_conversation)
    monkeypatch.setattr(chat_mod, "_prompt_memory_context", noop)
    monkeypatch.setattr(chat_mod, "_build_grounding", noop)
    monkeypatch.setattr(chat_mod, "_invoke_facet", invoke)
    monkeypatch.setattr(chat_mod.engine_state, "set_facet_status", noop)
    monkeypatch.setattr(chat_mod, "_update_history",
                        lambda *_args: projected.append((repository.state, _args[2])))
    monkeypatch.setattr(chat_mod, "_fire_completed",
                        lambda *_args: noop())
    monkeypatch.setattr("jax_engine.background.add_safe_task", lambda *_args, **_kwargs: None)

    class RuntimeNoticeRepository:
        def __init__(self):
            self.state = None
            self.payload = None

        async def prepare(self, unit, payload, *, tenant_id, project_id, subject_id, request_id,
                          previous_attempt_id=None):
            self.payload = payload
            self.state = "OUTPUT_PREPARED"
            projection = unit.durable_projection()
            return outbox.PreparedTransportAuthorization._mint(
                outbox._AUTH_TOKEN, outbox_id="notice-outbox", attempt_id="notice-attempt",
                tenant_id=tenant_id, scope_digest=projection["scope_digest"],
                request_id=request_id, response_id=projection["response_id"],
                subject_id=subject_id, idempotency_key=projection["idempotency_key"],
                effective_output_digest=projection["effective_output_digest"],
                effective_projection_digest=projection["effective_projection_digest"],
                original_envelope_digest=projection["original_envelope_digest"],
                contract_state=projection["effective_contract_state"],
                transport_payload_digest="sha256:" + hashlib.sha256(payload).hexdigest(),
                payload=payload, unit=unit,
            )

        async def transition(self, authorization, target, *, failure_class=None, before_send=False):
            from api.governed_chat import _lifecycle_core
            core = _lifecycle_core()
            core.validate_lifecycle_transition(core.OutputLifecycleState(self.state), target,
                                               before_send=before_send)
            self.state = target.value

        async def record_secondary_event(self, authorization, event_type):
            return None

    repository = RuntimeNoticeRepository()
    monkeypatch.setattr(transport, "OutputOutboxRepository", lambda: repository)

    async def run_route():
        result = await chat_mod.chat(
            chat_mod.ChatRequest(message="hello", facet="jekyll"),
            BackgroundTasks(), AuthUser(user_id="7", tenant_id="1", role="operator"),
        )
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await result({"type": "http", "method": "POST", "path": "/api/chat"}, receive, send)
        body = next(item["body"] for item in messages if item["type"] == "http.response.body")
        return body

    body = asyncio.run(run_route())
    decoded = json.loads(body)
    assert repository.state == "OUTPUT_COMMITTED_TO_TRANSPORT"
    assert decoded["aviso"] is None
    assert decoded["contract_state"] == "DEGRADED_STRUCTURED"
    assert decoded["response"] == "The response could not be verified safely."
    assert "faceta_sin_binding" not in body.decode("utf-8")
    assert body == repository.payload
    assert projected == [("OUTPUT_COMMITTED_TO_TRANSPORT", decoded["response"])]
