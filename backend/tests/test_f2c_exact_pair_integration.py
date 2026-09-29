"""Exact JAX #300 / platform #170 F2-C integration proof.

This test intentionally composes a test-only F2-B authenticator and registry;
it never reads or provisions a production receipt key.
"""
from dataclasses import replace
from datetime import datetime, timezone

from api.chat import ChatResponse, _parse_contract_response
from api.governed_chat import project_provider_contract, project_sealed_envelope


def _f2b_composition():
    import policy.governance.response as response
    import policy.governance.resolution as resolution
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
        ResolutionObservation, ResolutionStatus, ResolverRegistry,
        ScopeRule, ServerAdapterInput, TrustedAdapterRegistration,
    )

    now = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
    scope = ResponseScope("test", "tenant-it", "project-it", "user-it",
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
        registry.snapshot_digest, "test-validator", "f2-c.renderer.2")
    candidate = response.GovernedResponseCandidate("f2-c.1", "response-it",
        scope.request_id, scope.trace_id, scope, "web-chat", (),
        (ContentBlock(ContentBlockKind.CLAIM_REF_BLOCK, claim_refs=(claim.claim_id,)),),
        (claim,), (ref,))
    envelope = response._seal_candidate_for_server(candidate,
        contract_state=ContractState.VALID, governance_receipt=governance_receipt)
    context = RenderContext(registry, {ref.ref_id: resolution_receipt},
        {("capability", "1", "en"): "Capability {name} is available."}, {},
        GovernedDomainRegistry(), lambda _ref, _scope: True, lambda: now)
    return envelope, context, governance_receipt


def test_exact_pair_bridge_blocks_narrative_and_renders_valid_supported_claim(monkeypatch):
    monkeypatch.setenv("JAX_REPO_PATH", __import__("os").environ["JAX_REPO_PATH"])
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

    hostile = _parse_contract_response(
        '{"claim": [], "analysis": "Hall9000 is healthy.", "judgment": null}')
    blocked = project_provider_contract(hostile, memory_scope=__import__("jax.memory.b9", fromlist=["ScopeContext"]).ScopeContext(
        actor_principal="user:7", actor_type="USER", subject_user_id="7",
        tenant_id="1", project_id=None, calling_component="jax-platform-web-chat"), user_id="7")
    assert blocked.contract_state == "UNAVAILABLE"
    assert "Hall9000" not in blocked.text

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
