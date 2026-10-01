"""F2-D Web Chat ASGI boundary tests using a deterministic repository seam."""
import asyncio
import os
from datetime import datetime, timezone
import json

import pytest

from api.chat import ChatResponse, ContractResult
from api.governed_chat import project_provider_contract
from auth.models import AuthUser
from jax.memory.b9 import ScopeContext
from webchat_f2d import repository as outbox
from webchat_f2d.repository import OutputLifecycleUnavailable
from webchat_f2d.transport import (
    PreparedGovernedChatResponse,
    prepare_governed_chat_response,
)


def _composition(monkeypatch):
    # CI owns the exact paired JAX checkout; never substitute a developer path.
    assert os.environ.get("JAX_REPO_PATH")
    scope = ScopeContext(
        actor_principal="user:7", actor_type="USER", subject_user_id="7",
        tenant_id="1", project_id=None, calling_component="jax-platform-web-chat",
    )
    contract = ContractResult(
        contract_parsed=True, claims=[], analysis="A normal non-current response.",
        judgment=None, degradation_reason=None, raw_text="untrusted provider raw is not persisted",
    )
    governed = project_provider_contract(contract, memory_scope=scope, user_id="7")
    assert governed.transport_unit is not None
    response = ChatResponse(
        facet="jekyll", response=governed.text, timestamp="2026-09-29T00:00:00Z",
        response_id=governed.response_id, envelope_digest=governed.envelope_digest,
        source_envelope_digest=governed.source_envelope_digest,
        contract_state=governed.contract_state, governed_plain=True,
    )
    user = AuthUser(user_id="7", tenant_id="1", role="operator", token_version=0)
    return scope, governed, response, user


class _FakeRepository:
    def __init__(self):
        self.state = None
        self.payload = None
        self.authorization = None
        self.transitions = []
        self.fail_commit = False

    async def prepare(self, unit, payload, *, tenant_id, project_id, subject_id, request_id,
                      previous_attempt_id=None):
        self.payload = payload
        projection = unit.durable_projection()
        self.authorization = outbox.PreparedTransportAuthorization._mint(
            outbox._AUTH_TOKEN,
            outbox_id="server-only-outbox", attempt_id="attempt-1", tenant_id=tenant_id,
            scope_digest=projection["scope_digest"], request_id=request_id,
            response_id=projection["response_id"], subject_id=subject_id,
            idempotency_key=projection["idempotency_key"],
            effective_output_digest=projection["effective_output_digest"],
            effective_projection_digest=projection["effective_projection_digest"],
            original_envelope_digest=projection["original_envelope_digest"],
            contract_state=projection["effective_contract_state"],
            transport_payload_digest="sha256:" + __import__("hashlib").sha256(payload).hexdigest(),
            payload=payload, unit=unit,
        )
        self.state = "OUTPUT_PREPARED"
        return self.authorization

    async def transition(self, authorization, target, *, failure_class=None, before_send=False):
        if self.fail_commit and target.value == "OUTPUT_COMMITTED_TO_TRANSPORT":
            raise RuntimeError("simulated DB failure after ASGI send")
        core = __import__("api.governed_chat", fromlist=["_lifecycle_core"])._lifecycle_core()
        current = core.OutputLifecycleState(self.state)
        core.validate_lifecycle_transition(current, target, before_send=before_send)
        self.transitions.append((current.value, target.value, before_send))
        self.state = target.value

    async def record_secondary_event(self, authorization, event_type):
        self.transitions.append((self.state, event_type, False))


def _user_scope(scope):
    return scope


def _make_response(monkeypatch, repo, callback=lambda: asyncio.sleep(0)):
    scope, governed, chat_response, user = _composition(monkeypatch)
    result = asyncio.run(prepare_governed_chat_response(
        response=chat_response, transport_unit=governed.transport_unit,
        user=user, memory_scope=_user_scope(scope), on_commit=callback,
        trusted_metadata={"facet": chat_response.facet, "timestamp": chat_response.timestamp,
                          "contract_degraded": chat_response.contract_degraded},
        repository=repo,
    ))
    return result, governed, chat_response


def _run_asgi(response, send):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def async_send(message):
        result = send(message)
        if hasattr(result, "__await__"):
            await result
    asyncio.run(response({"type": "http", "method": "POST", "path": "/api/chat"}, receive, async_send))


def test_exact_prepared_json_bytes_commit_after_terminal_asgi_send(monkeypatch):
    repo = _FakeRepository()
    invoked = []
    response, governed, _ = _make_response(monkeypatch, repo, lambda: _record(invoked))
    messages = []
    _run_asgi(response, messages.append)
    body = next(message["body"] for message in messages if message["type"] == "http.response.body")
    assert body == repo.payload
    assert json.loads(body)["response"] == governed.text
    assert repo.transitions == [
        ("OUTPUT_PREPARED", "TRANSPORT_COMMITTING", False),
        ("TRANSPORT_COMMITTING", "OUTPUT_COMMITTED_TO_TRANSPORT", False),
    ]
    assert repo.state == "OUTPUT_COMMITTED_TO_TRANSPORT"
    assert invoked == ["committed"]


async def _record(values):
    values.append("committed")


def test_response_text_or_state_mutation_fails_before_durable_prepare(monkeypatch):
    _, governed, response, user = _composition(monkeypatch)
    repo = _FakeRepository()
    changed = response.model_copy(update={"response": "different prepared text"})
    with pytest.raises(OutputLifecycleUnavailable):
        asyncio.run(prepare_governed_chat_response(
            response=changed, transport_unit=governed.transport_unit,
            user=user, memory_scope=_user_scope(_composition(monkeypatch)[0]),
            trusted_metadata={"facet": response.facet, "timestamp": response.timestamp,
                              "contract_degraded": response.contract_degraded},
            on_commit=lambda: asyncio.sleep(0), repository=repo,
        ))
    assert repo.authorization is None


@pytest.mark.parametrize("field,value", [
    ("contract_state", "UNAVAILABLE"),
    ("response_id", "foreign-response"),
    ("envelope_digest", "sha256:" + "0" * 64),
    ("source_envelope_digest", "sha256:" + "1" * 64),
    ("governed_plain", False),
    ("facet", "other-facet"),
    ("timestamp", "2099-01-01T00:00:00Z"),
    ("contract_degraded", True),
    ("aviso", {"code": "estado_actual_no_disponible"}),
])
def test_effective_metadata_mutation_fails_before_prepare(monkeypatch, field, value):
    _, governed, response, user = _composition(monkeypatch)
    repo = _FakeRepository()
    changed = response.model_copy(update={field: value})
    scope, *_ = _composition(monkeypatch)
    with pytest.raises(OutputLifecycleUnavailable):
        asyncio.run(prepare_governed_chat_response(
            response=changed, transport_unit=governed.transport_unit,
            user=user, memory_scope=_user_scope(scope),
            trusted_metadata={"facet": response.facet, "timestamp": response.timestamp,
                              "contract_degraded": response.contract_degraded},
            on_commit=lambda: asyncio.sleep(0), repository=repo,
        ))
    assert repo.authorization is None


def test_durable_prepare_failure_cannot_return_dynamic_response(monkeypatch):
    _, governed, response, user = _composition(monkeypatch)

    class FailingPrepareRepository:
        async def prepare(self, *_args, **_kwargs):
            raise RuntimeError("simulated transactional prepare failure")

    with pytest.raises(RuntimeError, match="prepare failure"):
        asyncio.run(prepare_governed_chat_response(
            response=response, transport_unit=governed.transport_unit,
            user=user, memory_scope=_user_scope(_composition(monkeypatch)[0]),
            trusted_metadata={"facet": response.facet, "timestamp": response.timestamp,
                              "contract_degraded": response.contract_degraded},
            on_commit=lambda: asyncio.sleep(0), repository=FailingPrepareRepository(),
        ))


def test_lifecycle_unavailable_protocol_error_is_fixed_and_parameter_free(monkeypatch):
    """The narrow static exception cannot carry response/provider/runtime data."""
    repo = _FakeRepository()
    response, _, _ = _make_response(monkeypatch, repo)
    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(response._send_protocol_error(send))
    body = next(message["body"] for message in messages if message["type"] == "http.response.body")
    assert json.loads(body) == {"detail": {"code": "OUTPUT_LIFECYCLE_UNAVAILABLE"}}
    assert b"facet" not in body and b"provider" not in body and b"response" not in body


def test_asgi_failure_is_uncertain_and_never_acknowledged(monkeypatch):
    repo = _FakeRepository()
    response, _, _ = _make_response(monkeypatch, repo)
    calls = 0

    async def send(message):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disconnect")

    with pytest.raises(OSError):
        _run_asgi(response, send)
    assert repo.state == "TRANSPORT_OUTCOME_UNKNOWN"
    assert all(target != "DELIVERY_ACKNOWLEDGED" for _, target, _ in repo.transitions)


def test_current_expiry_after_commit_intent_cancels_before_dynamic_send(monkeypatch):
    repo = _FakeRepository()
    response, _, _ = _make_response(monkeypatch, repo)
    module = __import__("api.governed_chat", fromlist=["_lifecycle_core"])._lifecycle_core()
    original = module.revalidate_for_transport
    calls = 0

    def expire_second(unit, now):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated current receipt expiry")
        return original(unit, now)

    monkeypatch.setattr(module, "revalidate_for_transport", expire_second)
    messages = []
    _run_asgi(response, messages.append)
    assert repo.state == "CANCELLED_BEFORE_COMMIT"
    assert messages[0]["status"] == 503
    payload = next(message["body"] for message in messages if message["type"] == "http.response.body")
    assert b"A normal non-current response" not in payload
    assert json.loads(payload) == {"detail": {"code": "OUTPUT_LIFECYCLE_UNAVAILABLE"}}


def test_current_expiry_after_response_start_withholds_body_and_records_uncertainty(monkeypatch):
    repo = _FakeRepository()
    response, _, _ = _make_response(monkeypatch, repo)
    module = __import__("api.governed_chat", fromlist=["_lifecycle_core"])._lifecycle_core()
    original = module.revalidate_for_transport
    calls = 0

    def expire_before_body(unit, now):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("simulated current receipt expiry after headers")
        return original(unit, now)

    monkeypatch.setattr(module, "revalidate_for_transport", expire_before_body)
    messages = []
    _run_asgi(response, messages.append)
    assert repo.state == "TRANSPORT_OUTCOME_UNKNOWN"
    body = next(message["body"] for message in messages if message["type"] == "http.response.body")
    assert body == b""


def test_post_send_db_failure_leaves_committing_uncertainty(monkeypatch):
    repo = _FakeRepository()
    repo.fail_commit = True
    invoked = []
    response, _, _ = _make_response(monkeypatch, repo, lambda: _record(invoked))
    messages = []
    _run_asgi(response, messages.append)
    assert repo.state == "TRANSPORT_COMMITTING"
    assert len([m for m in messages if m["type"] == "http.response.body"]) == 1
    assert invoked == []


def test_post_commit_projection_failure_is_durably_appended_without_state_reversal(monkeypatch):
    repo = _FakeRepository()
    async def fail_projection():
        raise RuntimeError("simulated history projection failure")
    response, _, _ = _make_response(monkeypatch, repo, fail_projection)
    messages = []
    _run_asgi(response, messages.append)
    assert repo.state == "OUTPUT_COMMITTED_TO_TRANSPORT"
    assert repo.transitions[-1] == (
        "OUTPUT_COMMITTED_TO_TRANSPORT", "POST_COMMIT_PROJECTION_FAILED", False,
    )


def test_outbox_authorization_cannot_be_self_attested():
    with pytest.raises(OutputLifecycleUnavailable):
        outbox.PreparedTransportAuthorization(outbox_id="fabricated")
