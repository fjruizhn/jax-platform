"""Exact-byte FastAPI/ASGI transport adapter for governed Web Chat outputs."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging

from fastapi.responses import JSONResponse
from starlette.responses import Response

from api.governed_chat import _lifecycle_core
from webchat_f2d.repository import (
    OutputLifecycleUnavailable,
    OutputOutboxRepository,
    PreparedTransportAuthorization,
)

logger = logging.getLogger(__name__)
_PROTOCOL_ERROR = {"detail": {"code": "OUTPUT_LIFECYCLE_UNAVAILABLE"}}


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _validate_projection(response, unit, trusted_metadata: dict | None = None) -> dict:
    """Validate the Pydantic response against the opaque core render unit."""
    core = _lifecycle_core()
    if not isinstance(unit, core.GovernedTransportUnit):
        raise OutputLifecycleUnavailable("F2-D transport unit is missing or caller supplied")
    body = response.model_dump(mode="json")
    rendered = unit.rendered
    expected = {
        "response": rendered.text,
        "response_id": unit.response_id,
        "envelope_digest": rendered.envelope_digest,
        "source_envelope_digest": unit.original_envelope_digest,
        "contract_state": rendered.contract_state.value,
        "governed_plain": True,
    }
    if any(body.get(key) != value for key, value in expected.items()):
        raise OutputLifecycleUnavailable("ChatResponse differs from effective governed projection")
    # These fields are not part of the F2-C claim digest, but affect the actual
    # ChatResponse consumed by the frontend. Bind them to values derived by the
    # trusted route before constructing the response; governed outputs cannot
    # carry a canned notice that would replace the prepared response client-side.
    trusted_metadata = trusted_metadata or {}
    for key in ("facet", "timestamp", "contract_degraded"):
        if key not in trusted_metadata or body.get(key) != trusted_metadata[key]:
            raise OutputLifecycleUnavailable("ChatResponse metadata differs from trusted route projection")
    if body.get("aviso") is not None:
        raise OutputLifecycleUnavailable("governed dynamic output cannot carry a canned notice")
    if not body.get("response_id") or not body.get("envelope_digest"):
        raise OutputLifecycleUnavailable("effective governed identity is incomplete")
    return body


async def prepare_governed_chat_response(
    *, response, transport_unit, user, memory_scope, on_commit,
    trusted_metadata=None, background_tasks=None, repository=None,
) -> Response:
    """Persist OUTPUT_PREPARED before returning the response to FastAPI/ASGI."""
    repo = repository or OutputOutboxRepository()
    body = _validate_projection(response, transport_unit, trusted_metadata)
    payload = _json_bytes(body)
    projection = transport_unit.durable_projection()
    expected_audience = f"user:{user.user_id}"
    if projection["audience"] != expected_audience:
        raise OutputLifecycleUnavailable("prepared audience differs from authenticated user")
    if str(projection["tenant_id"]) != str(memory_scope.tenant_id):
        raise OutputLifecycleUnavailable("prepared tenant differs from resolved chat scope")
    if projection["project_id"] != memory_scope.project_id:
        raise OutputLifecycleUnavailable("prepared project differs from resolved chat scope")
    if str(projection["subject_id"]) != str(memory_scope.subject_user_id or user.user_id):
        raise OutputLifecycleUnavailable("prepared subject differs from resolved chat scope")
    authorization = await repo.prepare(
        transport_unit, payload,
        tenant_id=int(user.tenant_id),
        project_id=memory_scope.project_id,
        subject_id=str(memory_scope.subject_user_id or user.user_id),
        request_id=transport_unit.request_id,
    )
    return PreparedGovernedChatResponse(
        authorization=authorization, repository=repo, on_commit=on_commit,
        background=background_tasks,
    )


class PreparedGovernedChatResponse(Response):
    """Send only persisted bytes after a durable commit-intent transition.

    OUTPUT_COMMITTED_TO_TRANSPORT means only that the terminal ASGI body send
    returned successfully. It does not mean browser receipt, display, or read.
    """

    def __init__(self, *, authorization: PreparedTransportAuthorization,
                 repository: OutputOutboxRepository, on_commit, background=None):
        super().__init__(content=b"", status_code=200, media_type="application/json")
        self.authorization = authorization
        self.repository = repository
        self.on_commit = on_commit
        self.background = background
        self.body = authorization.payload
        self.raw_headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(self.body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ]

    async def _send_protocol_error(self, send):
        body = _json_bytes(_PROTOCOL_ERROR)
        await send({"type": "http.response.start", "status": 503,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(body)).encode("ascii")),
                                (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def __call__(self, scope, receive, send):
        core = _lifecycle_core()
        authorization = self.authorization
        try:
            # First freshness check is immediately before durable commit intent.
            core.revalidate_for_transport(authorization.unit, datetime.now(timezone.utc))
            await self.repository.transition(
                authorization, core.OutputLifecycleState.TRANSPORT_COMMITTING,
            )
        except Exception as exc:  # fail-soft: reject dynamic transport and send fixed unavailable response
            logger.warning("F2-D refused Web Chat transport before send (%s)", type(exc).__name__)
            try:
                await self.repository.transition(
                    authorization, core.OutputLifecycleState.FAILED_BEFORE_COMMIT,
                    failure_class="PRECOMMIT_AUTHORIZATION_FAILED",
                )
            except Exception:  # fail-soft: primary action is already a static fail-closed response
                pass
            await self._send_protocol_error(send)
            return

        try:
            # Freshness can expire during the durable intent transaction. This
            # check is still before the first ASGI send, so cancellation is true.
            core.revalidate_for_transport(authorization.unit, datetime.now(timezone.utc))
        except Exception as exc:  # fail-soft: expired output is withheld; only fixed unavailable text may send
            logger.info("F2-D cancelled stale Web Chat output before ASGI send (%s)", type(exc).__name__)
            try:
                await self.repository.transition(
                    authorization, core.OutputLifecycleState.CANCELLED_BEFORE_COMMIT,
                    failure_class="CURRENT_OUTPUT_EXPIRED_BEFORE_SEND", before_send=True,
                )
            except Exception:  # fail-soft: body remains withheld even if cancellation audit persistence fails
                logger.exception("F2-D could not persist pre-send cancellation")
            await self._send_protocol_error(send)
            return

        invoked_send = False
        try:
            invoked_send = True
            await send({"type": "http.response.start", "status": self.status_code,
                        "headers": self.raw_headers})
            # Headers may have been accepted while an accredited current
            # receipt expires. Withhold the body in that case; the HTTP status
            # is already committed, so record honest uncertainty, not cancel.
            try:
                core.revalidate_for_transport(authorization.unit, datetime.now(timezone.utc))
            except Exception as exc:  # fail-soft: withhold the body even if durable uncertainty cannot be recorded
                logger.info("F2-D withheld stale current body after response start (%s)", type(exc).__name__)
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                await self.repository.transition(
                    authorization, core.OutputLifecycleState.TRANSPORT_OUTCOME_UNKNOWN,
                    failure_class="CURRENT_OUTPUT_EXPIRED_AFTER_RESPONSE_START",
                )
                return
            await send({"type": "http.response.body", "body": authorization.payload,
                        "more_body": False})
        except BaseException:  # fail-soft: preserve uncertainty; never claim a successful transport commit
            if invoked_send:
                try:
                    await self.repository.transition(
                        authorization, core.OutputLifecycleState.TRANSPORT_OUTCOME_UNKNOWN,
                        failure_class="ASGI_SEND_OUTCOME_UNKNOWN",
                    )
                except Exception:  # fail-soft: durable COMMITTING state remains honest uncertainty
                    logger.exception("F2-D could not persist uncertain ASGI outcome")
            raise

        try:
            await self.repository.transition(
                authorization, core.OutputLifecycleState.OUTPUT_COMMITTED_TO_TRANSPORT,
            )
        except Exception:  # fail-soft: durable COMMITTING state remains honest uncertainty
            # The response may already have escaped. Leave TRANSPORT_COMMITTING
            # as durable uncertainty; never claim committed or acknowledged.
            logger.exception("F2-D could not persist post-send transport commitment")
            return

        try:
            await self.on_commit()
        except Exception:  # fail-soft: transport is already committed; lifecycle truth stays committed
            logger.exception("F2-D post-commit conversation projection failed")
            try:
                await self.repository.record_secondary_event(
                    authorization, "POST_COMMIT_PROJECTION_FAILED",
                )
            except Exception:  # fail-soft: secondary audit failure cannot rewrite committed transport state
                logger.exception("F2-D could not persist post-commit projection failure")
        if self.background is not None:
            try:
                await self.background()
            except Exception:  # fail-soft: observational work cannot revoke committed transport
                logger.exception("F2-D post-commit observational background task failed")
                try:
                    await self.repository.record_secondary_event(
                        authorization, "POST_COMMIT_OBSERVATION_FAILED",
                    )
                except Exception:  # fail-soft: secondary audit failure cannot rewrite committed transport state
                    logger.exception("F2-D could not persist post-commit observation failure")
