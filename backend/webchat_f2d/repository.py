"""MariaDB authority for immutable Web Chat output preparations and attempts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import uuid

import aiomysql

from db.transaccion import transaccion
from webchat_f2d import OUTBOX_RECORD_SCHEMA_VERSION, TRANSPORT_PROJECTION_VERSION
from api.governed_chat import _lifecycle_core

logger = logging.getLogger(__name__)


class OutputLifecycleUnavailable(RuntimeError):
    """The durable F2-D boundary could not authorize this HTTP response."""


class OutputIdentityConflict(OutputLifecycleUnavailable):
    """An idempotency identity was reused for different immutable output."""


def _utc_naive() -> datetime:
    # MariaDB DATETIME is stored in UTC without a timezone suffix.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _wire_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validate_wire_payload(payload: bytes, unit, projection) -> None:
    try:
        body = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise OutputLifecycleUnavailable("prepared transport payload is not valid JSON") from exc
    expected = {
        "response": unit.rendered.text,
        "response_id": projection["response_id"],
        "envelope_digest": unit.rendered.envelope_digest,
        "source_envelope_digest": projection["original_envelope_digest"],
        "contract_state": projection["effective_contract_state"],
        "governed_plain": True,
    }
    if not isinstance(body, dict) or any(body.get(name) != value for name, value in expected.items()):
        raise OutputLifecycleUnavailable("prepared JSON differs from the effective F2-C output")


_AUTH_TOKEN = object()


@dataclass(frozen=True, init=False)
class PreparedTransportAuthorization:
    """Opaque server-side row capability; an outbox UUID alone is not authority."""
    outbox_id: str
    attempt_id: str
    tenant_id: int
    scope_digest: str
    request_id: str
    response_id: str
    subject_id: str
    idempotency_key: str
    effective_output_digest: str
    effective_projection_digest: str
    original_envelope_digest: str
    contract_state: str
    transport_payload_digest: str
    payload: bytes
    unit: object

    def __init__(self, *_args, **_kwargs):
        raise OutputLifecycleUnavailable("transport authorization is server minted")

    @classmethod
    def _mint(cls, token, **values):
        if token is not _AUTH_TOKEN:
            raise OutputLifecycleUnavailable("transport authorization is server minted")
        value = object.__new__(cls)
        for name, item in values.items():
            object.__setattr__(value, name, item)
        return value


class OutputOutboxRepository:
    """Transactional persistence. SQL rows, not Python locks, own transitions."""

    async def prepare(
        self, unit, payload: bytes, *, tenant_id: int, project_id: str | None,
        subject_id: str, request_id: str, previous_attempt_id: str | None = None,
    ) -> PreparedTransportAuthorization:
        core = _lifecycle_core()
        projection = unit.durable_projection()
        if not isinstance(payload, bytes) or not payload:
            raise OutputLifecycleUnavailable("serialized governed response is empty")
        _validate_wire_payload(payload, unit, projection)
        if (str(projection["tenant_id"]) != str(tenant_id)
                or projection["project_id"] != project_id
                or str(projection["subject_id"]) != str(subject_id)
                or projection["request_id"] != request_id
                or projection["transport_kind"] != "web-chat-http-json"):
            raise OutputLifecycleUnavailable("governed transport scope differs from authenticated request")
        core.validate_lifecycle_version(projection["lifecycle_version"])
        try:
            core.revalidate_for_transport(unit, datetime.now(timezone.utc))
        except Exception as exc:
            raise OutputLifecycleUnavailable("governed output is stale before durable preparation") from exc
        attempt_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "f2d-attempt:" + projection["idempotency_key"]))
        outbox_id = str(uuid.uuid4())
        payload_digest = _wire_digest(payload)
        now = _utc_naive()
        attempt_sequence = 1
        if previous_attempt_id is not None:
            attempt_sequence = await self._next_retry_sequence(
                tenant_id, projection["scope_digest"], request_id, previous_attempt_id,
            )
        values = {
            "outbox_id": outbox_id,
            "attempt_id": attempt_id,
            "previous_attempt_id": previous_attempt_id,
            "attempt_sequence": attempt_sequence,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "subject_id": subject_id,
            "audience": projection["audience"],
            "scope_digest": projection["scope_digest"],
            "request_id": request_id,
            "trace_id": projection["trace_id"],
            "response_id": projection["response_id"],
            "transport_kind": projection["transport_kind"],
            "idempotency_key": projection["idempotency_key"],
            "lifecycle_version": projection["lifecycle_version"],
            "record_schema_version": OUTBOX_RECORD_SCHEMA_VERSION,
            "envelope_schema_version": projection["schema_version"],
            "transport_projection_version": TRANSPORT_PROJECTION_VERSION,
            "renderer_api_version": projection["renderer_api_version"],
            "domain_spec_version": projection["domain_spec_version"],
            "contract_state": projection["effective_contract_state"],
            "effective_output_digest": projection["effective_output_digest"],
            "effective_projection_digest": projection["effective_projection_digest"],
            "original_envelope_digest": projection["original_envelope_digest"],
            "transport_payload_digest": payload_digest,
            "governance_reference_ids": _json(projection["governance_reference_ids"]),
            "claim_ids": _json(projection["claim_ids"]),
            "contains_current_claim": bool(projection["contains_current_claim"]),
            "state": core.OutputLifecycleState.OUTPUT_PREPARED.value,
            "response_payload": payload,
            "prepared_at": now,
        }
        try:
            async with transaccion("READ COMMITTED") as cur:
                if previous_attempt_id is not None:
                    await self._validate_retry_parent(
                        cur, tenant_id, projection["scope_digest"], request_id,
                        previous_attempt_id, core,
                    )
                await cur.execute(
                    """INSERT INTO governed_output_outbox (
                      outbox_id, attempt_id, previous_attempt_id, attempt_sequence,
                      tenant_id, project_id, subject_id, audience, scope_digest,
                      request_id, trace_id, response_id, transport_kind, idempotency_key,
                      lifecycle_version, record_schema_version, envelope_schema_version,
                      transport_projection_version,
                      renderer_api_version, domain_spec_version,
                      contract_state, effective_output_digest, effective_projection_digest,
                      original_envelope_digest, transport_payload_digest,
                      governance_reference_ids, claim_ids, contains_current_claim, state,
                      response_payload, prepared_at
                    ) VALUES (
                      %(outbox_id)s, %(attempt_id)s, %(previous_attempt_id)s, %(attempt_sequence)s,
                      %(tenant_id)s, %(project_id)s, %(subject_id)s, %(audience)s, %(scope_digest)s,
                      %(request_id)s, %(trace_id)s, %(response_id)s, %(transport_kind)s, %(idempotency_key)s,
                      %(lifecycle_version)s, %(record_schema_version)s, %(envelope_schema_version)s,
                      %(transport_projection_version)s,
                      %(renderer_api_version)s, %(domain_spec_version)s,
                      %(contract_state)s, %(effective_output_digest)s, %(effective_projection_digest)s,
                      %(original_envelope_digest)s, %(transport_payload_digest)s,
                      %(governance_reference_ids)s, %(claim_ids)s, %(contains_current_claim)s, %(state)s,
                      %(response_payload)s, %(prepared_at)s
                    )""", values,
                )
                await self._event(cur, outbox_id, tenant_id, projection["scope_digest"], 1,
                                  None, core.OutputLifecycleState.OUTPUT_PREPARED, now, "OUTPUT_PREPARED")
        except aiomysql.IntegrityError:
            existing = await self._read_idempotent(tenant_id, projection["idempotency_key"])
            if existing is None or not self._same_preparation(existing, values):
                raise OutputIdentityConflict("idempotency identity conflicts with existing preparation")
            values["outbox_id"] = existing[0]
            values["attempt_id"] = existing[1]
            values["attempt_sequence"] = existing[2]
        return PreparedTransportAuthorization._mint(
            _AUTH_TOKEN, outbox_id=values["outbox_id"], attempt_id=values["attempt_id"],
            tenant_id=tenant_id, scope_digest=projection["scope_digest"],
            request_id=request_id, response_id=projection["response_id"],
            subject_id=subject_id, idempotency_key=projection["idempotency_key"],
            effective_output_digest=projection["effective_output_digest"],
            effective_projection_digest=projection["effective_projection_digest"],
            original_envelope_digest=projection["original_envelope_digest"],
            contract_state=projection["effective_contract_state"],
            transport_payload_digest=payload_digest, payload=payload, unit=unit,
        )

    async def _next_retry_sequence(self, tenant_id, scope_digest, request_id, previous_attempt_id):
        async with transaccion("READ COMMITTED") as cur:
            await cur.execute(
                """SELECT attempt_sequence FROM governed_output_outbox
                   WHERE tenant_id=%s AND scope_digest=%s AND request_id=%s AND attempt_id=%s
                   FOR UPDATE""",
                (tenant_id, scope_digest, request_id, previous_attempt_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise OutputLifecycleUnavailable("retry parent is not in the authenticated request scope")
            return int(row[0]) + 1

    async def _validate_retry_parent(self, cur, tenant_id, scope_digest, request_id, attempt_id, core):
        await cur.execute(
            """SELECT state, lifecycle_version FROM governed_output_outbox
               WHERE tenant_id=%s AND scope_digest=%s AND request_id=%s AND attempt_id=%s
               FOR UPDATE""",
            (tenant_id, scope_digest, request_id, attempt_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise OutputLifecycleUnavailable("retry parent is unknown in the authenticated scope")
        core.validate_lifecycle_version(row[1])
        if row[0] not in {
            core.OutputLifecycleState.FAILED_BEFORE_COMMIT.value,
            core.OutputLifecycleState.CANCELLED_BEFORE_COMMIT.value,
        }:
            raise OutputLifecycleUnavailable("retry is not authorized from this transport state")

    async def _read_idempotent(self, tenant_id: int, idempotency_key: str):
        async with transaccion("READ COMMITTED") as cur:
            await cur.execute(
                """SELECT outbox_id, attempt_id, attempt_sequence, previous_attempt_id,
                          project_id, subject_id, audience, scope_digest, request_id, trace_id, response_id,
                          transport_kind, idempotency_key, lifecycle_version,
                          record_schema_version, envelope_schema_version, transport_projection_version,
                          renderer_api_version, domain_spec_version, contract_state,
                          effective_output_digest, effective_projection_digest,
                          original_envelope_digest, transport_payload_digest,
                          governance_reference_ids, claim_ids, contains_current_claim,
                          response_payload, state
                   FROM governed_output_outbox WHERE tenant_id=%s AND idempotency_key=%s FOR UPDATE""",
                (tenant_id, idempotency_key),
            )
            return await cur.fetchone()

    @staticmethod
    def _same_preparation(existing, values) -> bool:
        names = (
            "outbox_id", "attempt_id", "attempt_sequence", "previous_attempt_id",
            "project_id", "subject_id",
            "audience", "scope_digest", "request_id", "trace_id", "response_id",
            "transport_kind", "idempotency_key", "lifecycle_version",
            "record_schema_version", "envelope_schema_version", "transport_projection_version",
            "renderer_api_version",
            "domain_spec_version", "contract_state", "effective_output_digest",
            "effective_projection_digest", "original_envelope_digest", "transport_payload_digest",
            "governance_reference_ids", "claim_ids", "contains_current_claim",
            "response_payload", "state",
        )
        # Default aiomysql cursors return tuples; names above mirror the explicit
        # SELECT order and compare every immutable output/scope/version field.
        return len(existing) == len(names) and all(
            (bytes(existing[index]) == values[name] if name == "response_payload"
             else existing[index] == values[name])
            for index, name in enumerate(names)
            if name != "state" and name != "outbox_id"
        )

    @staticmethod
    async def _event(cur, outbox_id, tenant_id, scope_digest, seq, before, after, at, event_type, details=None):
        await cur.execute(
            """INSERT INTO governed_output_lifecycle_events
               (outbox_id, tenant_id, scope_digest, sequence_no, event_type,
                from_state, to_state, occurred_at, details_json)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (outbox_id, tenant_id, scope_digest, seq, event_type,
             before.value if before else None, after.value, at,
             _json(details) if details is not None else None),
        )

    async def transition(self, authorization, target, *, failure_class=None, before_send=False):
        if not isinstance(authorization, PreparedTransportAuthorization):
            raise OutputLifecycleUnavailable("server transport authorization is required")
        core = _lifecycle_core()
        now = _utc_naive()
        async with transaccion("READ COMMITTED") as cur:
            await cur.execute(
                """SELECT state, row_version, lifecycle_version, record_schema_version,
                          envelope_schema_version, transport_projection_version,
                          renderer_api_version, domain_spec_version,
                          transport_kind, contract_state, transport_payload_digest,
                          effective_output_digest, effective_projection_digest, original_envelope_digest,
                          response_payload, subject_id, response_id, request_id,
                          idempotency_key, scope_digest, project_id, audience, trace_id,
                          governance_reference_ids, claim_ids, contains_current_claim
                   FROM governed_output_outbox
                   WHERE outbox_id=%s AND tenant_id=%s AND scope_digest=%s
                     AND request_id=%s AND response_id=%s AND subject_id=%s
                     AND attempt_id=%s AND idempotency_key=%s
                   FOR UPDATE""",
                (authorization.outbox_id, authorization.tenant_id, authorization.scope_digest,
                 authorization.request_id, authorization.response_id, authorization.subject_id,
                 authorization.attempt_id, authorization.idempotency_key),
            )
            row = await cur.fetchone()
            if not row:
                raise OutputLifecycleUnavailable("prepared record is not valid in the authenticated scope")
            (state_value, row_version, lifecycle_version, record_schema_version,
             envelope_schema_version, transport_projection_version,
             renderer_api_version, domain_spec_version,
             transport_kind, contract_state, payload_digest, output_digest, projection_digest,
             source_digest, stored_payload, subject_id, response_id, request_id,
             idempotency_key, scope_digest, project_id, audience, trace_id,
             reference_ids_json, claim_ids_json, contains_current_claim) = row
            core.validate_lifecycle_version(lifecycle_version)
            projection = authorization.unit.durable_projection()
            if record_schema_version != OUTBOX_RECORD_SCHEMA_VERSION:
                raise OutputLifecycleUnavailable("outbox record schema version is unsupported")
            if (envelope_schema_version != projection["schema_version"]
                    or transport_projection_version != TRANSPORT_PROJECTION_VERSION
                    or renderer_api_version != projection["renderer_api_version"]
                    or domain_spec_version != projection["domain_spec_version"]
                    or transport_kind != projection["transport_kind"]
                    or contract_state != authorization.contract_state
                    or project_id != projection["project_id"]
                    or audience != projection["audience"]
                    or trace_id != projection["trace_id"]
                    or json.loads(reference_ids_json) != projection["governance_reference_ids"]
                    or json.loads(claim_ids_json) != projection["claim_ids"]
                    or bool(contains_current_claim) != bool(projection["contains_current_claim"])):
                raise OutputLifecycleUnavailable("immutable governed output metadata changed")
            if (payload_digest != authorization.transport_payload_digest
                    or output_digest != authorization.effective_output_digest
                    or projection_digest != authorization.effective_projection_digest
                    or source_digest != authorization.original_envelope_digest
                    or bytes(stored_payload) != authorization.payload
                    or subject_id != authorization.subject_id
                    or response_id != authorization.response_id
                    or request_id != authorization.request_id
                    or idempotency_key != authorization.idempotency_key
                    or scope_digest != authorization.scope_digest
                    or _wire_digest(authorization.payload) != payload_digest):
                raise OutputLifecycleUnavailable("prepared output identity or serialized bytes changed")
            current = core.OutputLifecycleState(state_value)
            core.validate_lifecycle_transition(current, target, before_send=before_send)
            version = int(row_version) + 1
            await cur.execute(
                """UPDATE governed_output_outbox SET state=%s, row_version=%s,
                     committing_at=CASE WHEN %s='TRANSPORT_COMMITTING' THEN %s ELSE committing_at END,
                     committed_at=CASE WHEN %s='OUTPUT_COMMITTED_TO_TRANSPORT' THEN %s ELSE committed_at END,
                     acknowledged_at=CASE WHEN %s='DELIVERY_ACKNOWLEDGED' THEN %s ELSE acknowledged_at END,
                     failure_class=%s
                   WHERE outbox_id=%s AND row_version=%s AND state=%s""",
                (target.value, version, target.value, now, target.value, now, target.value, now,
                 failure_class, authorization.outbox_id, row_version, state_value),
            )
            if cur.rowcount != 1:
                raise OutputLifecycleUnavailable("concurrent lifecycle transition lost its version")
            await self._event(
                cur, authorization.outbox_id, authorization.tenant_id, authorization.scope_digest,
                version, current, target, now, target.value,
                {"failure_class": failure_class} if failure_class else None,
            )
