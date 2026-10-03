"""F2-D persistence and send boundary for immutable structured projections."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import uuid

import aiomysql

from db.transaccion import transaccion
from .core import load_structured_core


STRUCTURED_OUTBOX_RECORD_VERSION = "f2-e.structured-outbox.1"


class StructuredTransportUnavailable(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class StructuredTransportAuthorization:
    outbox_id: str
    tenant_id: int
    scope_digest: str
    request_id: str
    response_id: str
    subject_id: str
    idempotency_key: str
    payload: bytes
    payload_digest: str
    unit: object


class StructuredOutputOutboxRepository:
    """Persist only the exact effective structured bytes, never source DTOs."""

    async def prepare(self, unit: object) -> StructuredTransportAuthorization:
        core = load_structured_core()
        lifecycle = core.lifecycle
        if not isinstance(unit, lifecycle.StructuredGovernedTransportUnit):
            raise StructuredTransportUnavailable("structured unit must be server minted")
        lifecycle.validate_structured_lifecycle_version(unit.lifecycle_version)
        try:
            lifecycle.revalidate_structured_for_transport(unit, datetime.now(timezone.utc))
        except Exception as exc:
            raise StructuredTransportUnavailable("structured output is stale before preparation") from exc
        scope = unit.envelope.response_scope
        try:
            tenant_id = int(scope.tenant_id)
        except (TypeError, ValueError) as exc:
            raise StructuredTransportUnavailable("structured scope tenant is invalid") from exc
        payload = unit.wire_bytes
        projection = unit.durable_projection()
        metadata = projection["transport_metadata"]
        if payload != unit.wire_bytes or projection["wire_payload_digest"] != _digest(payload):
            raise StructuredTransportUnavailable("structured wire bytes differ from unit")
        try:
            channel_id = core.external_output.ExternalOutputChannelId(unit.metadata.channel_id)
            channel = core.external_output.CHANNELS[channel_id]
        except (ValueError, KeyError) as exc:
            raise StructuredTransportUnavailable("structured channel is not registered") from exc
        now = _now()
        outbox_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "f2e-structured:" + unit.idempotency_key))
        values = {
            "outbox_id": outbox_id, "attempt_id": attempt_id,
            "tenant_id": tenant_id, "project_id": scope.project_id,
            "subject_id": scope.subject_id, "audience": scope.audience,
            "scope_digest": scope.scope_digest, "request_id": scope.request_id,
            "trace_id": scope.trace_id, "response_id": projection["response_id"],
            "transport_kind": channel.transport_kind, "idempotency_key": unit.idempotency_key,
            "lifecycle_version": projection["lifecycle_version"],
            "record_schema_version": STRUCTURED_OUTBOX_RECORD_VERSION,
            "envelope_schema_version": unit.envelope.schema_version,
            "transport_projection_version": projection["structured_schema_version"],
            "renderer_api_version": projection["renderer_api_version"],
            "domain_spec_version": projection["domain_spec_version"],
            "contract_state": unit.rendered.contract_state.value,
            "effective_output_digest": projection["effective_output_digest"],
            "effective_projection_digest": projection["effective_projection_digest"],
            "original_envelope_digest": projection["source_envelope_digest"],
            "transport_payload_digest": _digest(payload),
            "governance_reference_ids": _json([ref.ref_id for ref in unit.envelope.references]),
            "claim_ids": _json(projection["claim_ids"]),
            "contains_current_claim": bool(projection["claim_ids"]),
            "state": lifecycle.OutputLifecycleState.OUTPUT_PREPARED.value,
            "response_payload": payload, "prepared_at": now,
        }
        try:
            async with transaccion("READ COMMITTED") as cur:
                await cur.execute(
                    """INSERT INTO governed_output_outbox (
                      outbox_id,attempt_id,previous_attempt_id,attempt_sequence,tenant_id,project_id,subject_id,audience,
                      scope_digest,request_id,trace_id,response_id,transport_kind,idempotency_key,lifecycle_version,
                      record_schema_version,envelope_schema_version,transport_projection_version,renderer_api_version,
                      domain_spec_version,contract_state,effective_output_digest,effective_projection_digest,
                      original_envelope_digest,transport_payload_digest,governance_reference_ids,claim_ids,
                      contains_current_claim,current_not_after,state,response_payload,prepared_at)
                      VALUES (%(outbox_id)s,%(attempt_id)s,NULL,1,%(tenant_id)s,%(project_id)s,%(subject_id)s,%(audience)s,
                      %(scope_digest)s,%(request_id)s,%(trace_id)s,%(response_id)s,%(transport_kind)s,%(idempotency_key)s,
                      %(lifecycle_version)s,%(record_schema_version)s,%(envelope_schema_version)s,
                      %(transport_projection_version)s,%(renderer_api_version)s,%(domain_spec_version)s,
                      %(contract_state)s,%(effective_output_digest)s,%(effective_projection_digest)s,
                      %(original_envelope_digest)s,%(transport_payload_digest)s,%(governance_reference_ids)s,%(claim_ids)s,
                      %(contains_current_claim)s,NULL,%(state)s,%(response_payload)s,%(prepared_at)s)""", values)
                await self._event(cur, values, 1, None, values["state"])
        except aiomysql.IntegrityError as exc:
            raise StructuredTransportUnavailable("structured output idempotency conflict") from exc
        return StructuredTransportAuthorization(outbox_id, tenant_id, scope.scope_digest, scope.request_id,
            projection["response_id"], scope.subject_id, unit.idempotency_key, payload, _digest(payload), unit)

    async def transition(self, authorization: StructuredTransportAuthorization, target: object, *, failure_class: str | None = None) -> None:
        if not isinstance(authorization, StructuredTransportAuthorization):
            raise StructuredTransportUnavailable("transport authorization is server minted")
        core = load_structured_core()
        lifecycle = core.lifecycle
        if not isinstance(authorization.unit, lifecycle.StructuredGovernedTransportUnit):
            raise StructuredTransportUnavailable("structured authorization core mismatch")
        async with transaccion("READ COMMITTED") as cur:
            await cur.execute(
                """SELECT state,row_version,lifecycle_version,record_schema_version,response_payload,transport_payload_digest
                   FROM governed_output_outbox WHERE outbox_id=%s AND tenant_id=%s AND scope_digest=%s
                   AND request_id=%s AND response_id=%s AND subject_id=%s AND idempotency_key=%s FOR UPDATE""",
                (authorization.outbox_id, authorization.tenant_id, authorization.scope_digest,
                 authorization.request_id, authorization.response_id, authorization.subject_id,
                 authorization.idempotency_key))
            row = await cur.fetchone()
            if row is None:
                raise StructuredTransportUnavailable("prepared structured output is outside authenticated scope")
            state, row_version, version, record_version, payload, payload_digest = row
            if version != lifecycle.STRUCTURED_OUTPUT_LIFECYCLE_API_VERSION or record_version != STRUCTURED_OUTBOX_RECORD_VERSION:
                raise StructuredTransportUnavailable("stored structured output version is unsupported")
            if bytes(payload) != authorization.payload or payload_digest != authorization.payload_digest:
                raise StructuredTransportUnavailable("stored structured bytes changed")
            current = lifecycle.OutputLifecycleState(state)
            lifecycle.validate_lifecycle_transition(current, target)
            now = _now()
            next_version = int(row_version) + 1
            await cur.execute(
                """UPDATE governed_output_outbox SET state=%s,row_version=%s,
                   committing_at=CASE WHEN %s='TRANSPORT_COMMITTING' THEN %s ELSE committing_at END,
                   committed_at=CASE WHEN %s='OUTPUT_COMMITTED_TO_TRANSPORT' THEN %s ELSE committed_at END,
                   failure_class=%s WHERE outbox_id=%s AND row_version=%s AND state=%s""",
                (target.value, next_version, target.value, now, target.value, now, failure_class,
                 authorization.outbox_id, row_version, state))
            if cur.rowcount != 1:
                raise StructuredTransportUnavailable("concurrent structured lifecycle transition lost")
            values = {"outbox_id": authorization.outbox_id, "tenant_id": authorization.tenant_id,
                "scope_digest": authorization.scope_digest}
            await self._event(cur, values, next_version, state, target.value, failure_class)

    async def _event(self, cur, values, sequence, before, after, failure_class=None):
        await cur.execute(
            """INSERT INTO governed_output_lifecycle_events
              (outbox_id,tenant_id,scope_digest,sequence_no,event_type,from_state,to_state,occurred_at,details_json)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (values["outbox_id"], values["tenant_id"], values["scope_digest"], sequence, after,
             before, after, _now(), _json({"failure_class": failure_class}) if failure_class else None))


async def commit_structured_wire(*, authorization: StructuredTransportAuthorization,
                                 repository: StructuredOutputOutboxRepository,
                                 send_wire) -> None:
    """Commit only after the exact F2-D wire bytes were accepted by transport.

    ``send_wire`` is a transport-owned async callable.  It receives the
    immutable bytes prepared by the core; serializers and route handlers do
    not receive the DTO again, so they cannot mutate it after rendering.
    """
    if not isinstance(repository, StructuredOutputOutboxRepository) or not callable(send_wire):
        raise StructuredTransportUnavailable("structured wire requires server transport implementation")
    core = load_structured_core()
    lifecycle = core.lifecycle
    try:
        lifecycle.revalidate_structured_for_transport(authorization.unit, datetime.now(timezone.utc))
        await repository.transition(authorization, lifecycle.OutputLifecycleState.TRANSPORT_COMMITTING)
        lifecycle.revalidate_structured_for_transport(authorization.unit, datetime.now(timezone.utc))
    except Exception as exc:
        try:
            await repository.transition(
                authorization, lifecycle.OutputLifecycleState.CANCELLED_BEFORE_COMMIT,
                failure_class="STRUCTURED_PRECOMMIT_REVALIDATION_FAILED",
            )
        except Exception:
            pass
        raise StructuredTransportUnavailable("structured output refused before transport") from exc
    try:
        await send_wire(authorization.payload)
    except BaseException:
        try:
            await repository.transition(
                authorization, lifecycle.OutputLifecycleState.TRANSPORT_OUTCOME_UNKNOWN,
                failure_class="STRUCTURED_TRANSPORT_OUTCOME_UNKNOWN",
            )
        except Exception:
            pass
        raise
    try:
        await repository.transition(authorization, lifecycle.OutputLifecycleState.OUTPUT_COMMITTED_TO_TRANSPORT)
    except Exception as exc:
        # The transport already accepted bytes.  The durable COMMITTING record
        # remains the only honest state; it must never become delivery ACK.
        raise StructuredTransportUnavailable("structured transport commit persistence failed") from exc
