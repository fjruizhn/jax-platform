"""The Platform never substitutes a processing-status authority.

The paired JAX checkout owns both the protected ProcessingJobStore singleton
and the F2-B resolver.  This regression exercises that real resolver against
an isolated immutable JSONL store; Platform has no processing resolver of its
own to select a path, invent a DTO, or reinterpret ownership.
"""
from datetime import datetime, timezone
import asyncio
import json
import secrets
import uuid

import httpx
import pytest

from jax_engine import status_resolution as platform_status


def _scope(response, *, subject_id="2", project_id="3", audience=None, request_id="request-processing"):
    return response.ResponseScope(
        "test", "1", project_id, subject_id, "service:platform",
        audience or f"user:{subject_id}", "processing-exact-pair", request_id, "trace-processing",
    )


def _sealed_processing_envelope(scope, registry, receipt, arguments):
    """Use the exact template contract issued by the real status registry."""
    from policy.governance.governed_domain import GOVERNED_RENDERER_API_VERSION
    from policy.governance.governed_renderer import GovernedDomainRegistry, RenderContext
    from policy.governance.response import (ClaimDisposition, ClaimRecord, ContentBlock,
        ContentBlockKind, ContractState, EpistemicStatus, ExistenceState, GovernanceReceipt,
        GovernedResponseCandidate, ReferenceRef, ReferenceType, SourceClass, TemplateContract,
        TemporalClass, _seal_candidate_for_server)
    from policy.governance.resolution import ReferenceLookupRecord

    reference = ReferenceRef("processing-receipt", ReferenceType.RESOLUTION_RECEIPT,
        "test://processing-receipt", "processing-receipt", receipt.receipt_id,
        scope.scope_digest, TemporalClass.CURRENT, ExistenceState.PRESENT)
    claim = ClaimRecord("processing-claim", "PROCESSING_JOB_STATUS", arguments, scope,
        SourceClass.CURRENT_SOURCE, EpistemicStatus.CURRENT_OBSERVATION,
        resolution_receipt_ref=reference.ref_id, disposition=ClaimDisposition.ASSERTABLE,
        template_contract=TemplateContract("PROCESSING_JOB_STATUS", "f2-e.runtime-status.3", "es"))
    candidate = GovernedResponseCandidate("f2-c.1", "processing-response", scope.request_id,
        scope.trace_id, scope, scope.component_id, (),
        (ContentBlock(ContentBlockKind.CLAIM_REF_BLOCK, claim_refs=(claim.claim_id,)),),
        (claim,), (reference,))
    envelope = _seal_candidate_for_server(candidate, contract_state=ContractState.VALID,
        governance_receipt=GovernanceReceipt("test-policy", "test-vocab", registry.snapshot_digest,
            "test-validator", GOVERNED_RENDERER_API_VERSION))
    known = ReferenceLookupRecord(reference.ref_id, reference.ref_type, reference.canonical_locator,
        reference.immutable_identity, reference.revision_or_digest, reference.scope_digest,
        reference.temporal_class, reference.existence_state, True)
    def resolve(candidate_ref, candidate_scope):
        return known if candidate_ref == reference and candidate_scope == scope else None
    context = RenderContext(registry, {reference.ref_id: receipt},
        {("PROCESSING_JOB_STATUS", "f2-e.runtime-status.3", "es"):
            "Trabajo de procesamiento {processing_job_id} está {status}."}, {},
        GovernedDomainRegistry(), lambda ref, candidate_scope: resolve(ref, candidate_scope) is not None,
        lambda: datetime.now(timezone.utc), receipt_reference_resolver=resolve)
    return envelope, context


def test_processing_status_is_resolved_only_by_paired_jax_singleton(monkeypatch, tmp_path):
    """An owner-bound JAX JSONL observation produces F2-B evidence once."""
    import procesamiento_routes
    from processing_job_store import ProcessingJobStore
    from processing_ownership import ProcessingOwnershipContext
    from policy.governance import response, resolution, runtime_status

    assert not hasattr(platform_status, "ProcessingJobStatusResolver")
    store = ProcessingJobStore(str(tmp_path / "processing-jobs.jsonl"))
    monkeypatch.setattr(procesamiento_routes, "_STORE", store)
    owner = ProcessingOwnershipContext("processing-owner.1", "1", "2", "3")
    job_id = store.create(
        ownership=owner, caller="jax-platform:proyectos-documentos",
        capability="ingesta_archivos", motor="n/a", trace_id="trace", prompt="n/a",
        recursion_depth=0,
    )
    scope = _scope(response)
    evidence = runtime_status.ProcessingJobStatusResolver().evidence(
        {"processing_job_id": job_id, "status": "pending"}, scope)
    registry = runtime_status.build_runtime_status_registry(
        scope, authenticator=resolution.ReceiptAuthenticator.for_testing(b"p" * 32),
        platform_source_configuration={
            "FACET_RUNTIME_STATUS": {
                "state_contract": "JAXEngineState.FacetState", "status_field": "status",
                "observed_at_field": "resolver_read_time",
                "allowed_statuses": ["idle", "thinking", "error", "offline"],
            },
            "ENGINE_STATUS": {
                "endpoint_sha256": "sha256:" + "a" * 64, "method": "GET", "path": "/health",
                "timeout_seconds": 5, "poll_interval_seconds": 30, "success_status_code": 200,
            },
        },
    )
    receipt = registry.resolve(
        "PROCESSING_JOB_STATUS", {"processing_job_id": job_id, "status": "pending"},
        scope, validation_time=datetime.now(timezone.utc), runtime_status_evidence=evidence,
    )
    assert receipt.status is resolution.ResolutionStatus.RESOLVED
    assert receipt.result_digest

    wrong_member = runtime_status.ProcessingJobStatusResolver().evidence(
        {"processing_job_id": job_id, "status": "pending"},
        _scope(response, subject_id="4"),
    )
    assert wrong_member.observation.status is resolution.ResolutionStatus.WRONG_SCOPE


def test_processing_status_rejects_noncanonical_arguments():
    """A native-looking status DTO alone is never a processing receipt."""
    from policy.governance import response, runtime_status

    with pytest.raises(Exception):
        runtime_status.ProcessingJobStatusResolver().evidence(
            {"job_id": "x", "status": "pending"}, _scope(response))


def test_processing_status_without_authoritative_jsonl_is_unavailable():
    """An unauthenticated/native DTO cannot replace the JAX source read."""
    from policy.governance import response, resolution, runtime_status

    evidence = runtime_status.ProcessingJobStatusResolver().evidence(
        {"processing_job_id": "not-a-job", "status": "not-a-status"}, _scope(response))
    assert evidence.observation.status is resolution.ResolutionStatus.UNAVAILABLE


def test_processing_exact_pair_dispatches_real_queue_through_protected_jax_asgi(client, monkeypatch, tmp_path):
    """The queue row, owner headers, JAX route, JSONL and F2-B source are one chain."""
    from fastapi import FastAPI
    import procesamiento_routes
    from auth_servicio import IDENTIDAD_PLATAFORMA, proteger
    from processing_job_store import ProcessingJobStore
    from policy.governance import response, resolution, runtime_status
    from proyectos_documentos import despachador, repositorio as repo
    from tests.identidades import cabeceras, sql, uid

    identity = f"sr3-{uuid.uuid4().hex}"
    headers = cabeceras(client, identity, tenant_id="1")
    user_id = int(uid(client, identity, tenant_id="1"))
    created = client.post("/api/proyectos", headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                          json={"nombre": "SR3", "descripcion": None})
    assert created.status_code == 201, created.text
    project = created.json()
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("JAX_LAS_MANOS_CREDENCIAL_PLATAFORMA", token)
    store = ProcessingJobStore(str(tmp_path / "processing-jobs.jsonl"))
    monkeypatch.setattr(procesamiento_routes, "_STORE", store)

    async def no_ocr(*_args, **_kwargs):
        return None

    monkeypatch.setattr(procesamiento_routes, "_ejecutar_trabajo", no_ocr)
    app = FastAPI()
    app.include_router(procesamiento_routes.router)
    proteger(app, {IDENTIDAD_PLATAFORMA: token.encode("ascii")})
    las_manos = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://las-manos.test")
    dispatched_bodies = []

    class RecordingLasManos:
        async def post(self, *args, **kwargs):
            dispatched_bodies.append(kwargs["json"])
            return await las_manos.post(*args, **kwargs)

        async def get(self, *args, **kwargs):
            return await las_manos.get(*args, **kwargs)

    recording_las_manos = RecordingLasManos()

    async def get_client():
        return recording_las_manos

    monkeypatch.setattr(despachador, "get_http_client", get_client)
    route = f"proyectos/{project['uuid']}/entrada/lote/documento.pdf"

    async def dispatch():
        from db.connection import get_pool
        pool = await get_pool()
        document_id = await repo.insertar(pool, project_id=project["id"], sha256="a" * 64,
            nombre_original="documento.pdf", ruta_entrada=route, bytes_=1, tipo="pdf",
            subido_por=user_id, roles_escritura=("OWNER", "CONTRIBUTOR", "REVIEWER"))
        await despachador._despachar(pool)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT job_id FROM project_documents WHERE id=%s", (document_id,))
                return document_id, (await cur.fetchone())[0]

    document_id, job_id = client.portal.call(dispatch)
    try:
        assert isinstance(job_id, str) and job_id
        from procesamiento_routes import TrabajoRequest
        assert TrabajoRequest.model_config["extra"] == "forbid"
        assert dispatched_bodies == [{"project_uuid": project["uuid"], "rutas": [route]}]
        assert TrabajoRequest.model_validate(dispatched_bodies[0]).model_dump() == dispatched_bodies[0]
        snapshot = store.authoritative_snapshot(job_id)
        assert snapshot is not None
        assert snapshot.view.caller == f"user:{user_id}"
        first_event = json.loads((tmp_path / "processing-jobs.jsonl").read_text().splitlines()[0])
        assert first_event["job_id"] == job_id
        assert first_event["processing_ownership"] == {
            "version": "processing-owner.1", "tenant_id": "1", "user_id": str(user_id),
            "project_id": str(project["id"]),
        }
        from credencial_las_manos import PlatformProcessingOwnership, encabezados_procesamiento
        lines_before_rejection = (tmp_path / "processing-jobs.jsonl").read_text().splitlines()
        async def reject_mismatched_owner():
            return await las_manos.post("/procesamiento/trabajos", json=dispatched_bodies[0],
                headers=encabezados_procesamiento(PlatformProcessingOwnership(
                    tenant_id=1, user_id=user_id, project_id=project["id"] + 1)))
        rejected = client.portal.call(reject_mismatched_owner)
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "proyecto_no_activo"
        assert (tmp_path / "processing-jobs.jsonl").read_text().splitlines() == lines_before_rejection
        scope = _scope(response, subject_id=str(user_id), project_id=str(project["id"]),
            audience=f"user:{user_id}", request_id=f"processing-{job_id}")
        evidence = runtime_status.ProcessingJobStatusResolver().evidence(
            {"processing_job_id": job_id, "status": "pending"}, scope)
        registry = runtime_status.build_runtime_status_registry(
            scope, authenticator=resolution.ReceiptAuthenticator.for_testing(b"r" * 32),
            platform_source_configuration={
                "FACET_RUNTIME_STATUS": {"state_contract": "JAXEngineState.FacetState", "status_field": "status", "observed_at_field": "resolver_read_time", "allowed_statuses": ["idle", "thinking", "error", "offline"]},
                "ENGINE_STATUS": {"endpoint_sha256": "sha256:" + "a" * 64, "method": "GET", "path": "/health", "timeout_seconds": 5, "poll_interval_seconds": 30, "success_status_code": 200},
            })
        receipt = registry.resolve("PROCESSING_JOB_STATUS", {"processing_job_id": job_id, "status": "pending"},
            scope, validation_time=datetime.now(timezone.utc), runtime_status_evidence=evidence)
        assert receipt.status is resolution.ResolutionStatus.RESOLVED
        from api.governed_chat import project_sealed_envelope
        envelope, render_context = _sealed_processing_envelope(
            scope, registry, receipt, {"processing_job_id": job_id, "status": "pending"})
        governed = project_sealed_envelope(envelope, render_context)
        assert governed.contract_state == "VALID"
        assert governed.text == f"Trabajo de procesamiento {job_id} está pending."
        assert governed.transport_unit is not None
        assert governed.transport_unit.durable_projection()["response_id"] == governed.response_id
        from api.chat import ChatResponse
        from auth.models import AuthUser
        from jax.memory.b9 import ScopeContext
        from webchat_f2d.transport import prepare_governed_chat_response
        response_body = ChatResponse(facet="processing", response=governed.text,
            timestamp=datetime.now(timezone.utc).isoformat(), contract_degraded=False,
            response_id=governed.response_id, envelope_digest=governed.envelope_digest,
            source_envelope_digest=governed.source_envelope_digest, contract_state=governed.contract_state,
            governed_plain=True)
        memory_scope = ScopeContext(actor_principal=f"user:{user_id}", actor_type="USER",
            subject_user_id=str(user_id), tenant_id="1", project_id=str(project["id"]),
            calling_component="processing-exact-pair", request_id=scope.request_id, trace_id=scope.trace_id)
        user = AuthUser(user_id=str(user_id), tenant_id="1", role="operator")
        async def no_projection():
            return None
        async def transport():
            prepared = await prepare_governed_chat_response(response=response_body,
                transport_unit=governed.transport_unit, user=user, memory_scope=memory_scope,
                on_commit=no_projection, trusted_metadata={"facet": "processing",
                    "timestamp": response_body.timestamp, "contract_degraded": False})
            messages = []
            async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message): messages.append(message)
            await prepared({"type": "http", "method": "POST", "path": "/api/chat"}, receive, send)
            return prepared.authorization.outbox_id, messages
        outbox_id, messages = client.portal.call(transport)
        assert [m["type"] for m in messages] == ["http.response.start", "http.response.body"]
        async def lifecycle():
            from db.connection import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT state FROM governed_output_outbox WHERE outbox_id=%s", (outbox_id,))
                    state = (await cur.fetchone())[0]
                    await cur.execute("SELECT from_state, to_state FROM governed_output_lifecycle_events WHERE outbox_id=%s ORDER BY sequence_no", (outbox_id,))
                    return state, await cur.fetchall()
        state, transitions = client.portal.call(lifecycle)
        assert state == "OUTPUT_COMMITTED_TO_TRANSPORT"
        assert transitions == ((None, "OUTPUT_PREPARED"), ("OUTPUT_PREPARED", "TRANSPORT_COMMITTING"),
            ("TRANSPORT_COMMITTING", "OUTPUT_COMMITTED_TO_TRANSPORT"))
    finally:
        asyncio.run(las_manos.aclose())
        client.portal.call(sql, "DELETE FROM project_documents WHERE id=%s", (document_id,))
