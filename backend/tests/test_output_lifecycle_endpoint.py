"""F2-D route-order tests for governed history and B9 assistant projection."""
import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from auth.models import AuthUser
from api.chat import ChatRequest
from webchat_f2d import repository as outbox


class _RecordingOutbox:
    def __init__(self, fail_prepare=False):
        self.state = "OUTPUT_PREPARED"
        self.fail_prepare = fail_prepare
        self.events = []

    async def prepare(self, unit, payload, *, tenant_id, project_id, subject_id, request_id,
                      previous_attempt_id=None):
        if self.fail_prepare:
            raise RuntimeError("simulated durable prepare failure")
        projection = unit.durable_projection()
        authorization = outbox.PreparedTransportAuthorization._mint(
            outbox._AUTH_TOKEN, outbox_id="endpoint-test-outbox", attempt_id="endpoint-test-attempt",
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
        self.events.append(("prepared", self.state))
        return authorization

    async def transition(self, authorization, target, *, failure_class=None, before_send=False):
        from api.governed_chat import _lifecycle_core
        core = _lifecycle_core()
        core.validate_lifecycle_transition(core.OutputLifecycleState(self.state), target,
                                           before_send=before_send)
        self.state = target.value
        self.events.append((target.value, self.state))

    async def record_secondary_event(self, authorization, event_type):
        self.events.append((event_type, self.state))


@pytest.mark.usefixtures("chat_sin_memoria")
@pytest.mark.parametrize("scenario", ["success", "prepare_failure", "send_failure"])
@pytest.mark.parametrize("runtime_notice", [False, True])
def test_actual_chat_route_projects_only_governed_assistant_after_commit(
    client, monkeypatch, scenario, runtime_notice,
):
    from api import chat
    from webchat_f2d import transport

    outbox_repo = _RecordingOutbox(fail_prepare=scenario == "prepare_failure")
    monkeypatch.setattr(transport, "OutputOutboxRepository", lambda: outbox_repo)
    scope = chat.ScopeContext(
        actor_principal="user:7", actor_type="USER", subject_user_id="7",
        tenant_id="1", project_id=None, calling_component="jax-platform-web-chat",
    )
    async def resolved_scope(_user, _project):
        return scope
    async def invoke_provider(*_args, **_kwargs):
        if runtime_notice:
            # A parameter-free binding notice still carries runtime semantics;
            # it must become a governed F2-D response rather than an ``aviso``
            # shortcut the frontend can enrich independently.
            return (chat.AvisoDeChat(code="faceta_sin_binding"), None)
        return ('{"claim": [], "analysis": "A safe narrative.", "judgment": null}',
                SimpleNamespace(provider_id="test", model="test-model", tokens_in=1, tokens_out=1))
    async def noop_async(*_args, **_kwargs):
        return None
    async def memory_context(*_args, **_kwargs):
        return None
    async def conversation(*_args, **_kwargs):
        return "test-conversation"
    projected = []
    monkeypatch.setattr(chat, "_scope_for_chat", resolved_scope)
    monkeypatch.setattr(chat, "_get_conv_uuid", conversation)
    monkeypatch.setattr(chat, "_prompt_memory_context", memory_context)
    monkeypatch.setattr(chat, "_build_grounding", noop_async)
    monkeypatch.setattr(chat, "_invoke_facet", invoke_provider)
    monkeypatch.setattr(chat, "record_usage", noop_async)
    monkeypatch.setattr(chat.engine_state, "set_facet_status", noop_async)
    monkeypatch.setattr(chat, "_update_history", lambda *_args: projected.append(("history", outbox_repo.state, _args[2])))
    monkeypatch.setattr(chat, "_memory", SimpleNamespace(save_message=lambda *_args, **_kwargs:
                       projected.append(("memory", _args[1], outbox_repo.state, _args[2]))))
    async def completed(*_args):
        projected.append(("completed", outbox_repo.state, None))
    monkeypatch.setattr(chat, "_fire_completed", completed)
    monkeypatch.setattr("jax_engine.background.add_safe_task", lambda *_args, **_kwargs: None)

    user = AuthUser(user_id="7", tenant_id="1", role="operator", token_version=0)
    async def invoke_route_and_transport():
        result = await chat.chat(ChatRequest(message="hello", facet="jekyll"),
                                 BackgroundTasks(), user)
        messages = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            if scenario == "send_failure" and message["type"] == "http.response.body":
                raise OSError("simulated ASGI disconnect")
            messages.append(message)
        if scenario == "send_failure":
            with pytest.raises(OSError, match="ASGI disconnect"):
                await result({"type": "http", "method": "POST", "path": "/api/chat"},
                             receive, send)
        else:
            await result({"type": "http", "method": "POST", "path": "/api/chat"},
                         receive, send)
        return result, messages
    response, messages = asyncio.run(invoke_route_and_transport())
    if scenario == "prepare_failure":
        assert response.status_code == 503
        assert not [row for row in projected if row[0] == "history" or
                    (row[0] == "memory" and row[1] == "assistant") or row[0] == "completed"]
        assert outbox_repo.state == "OUTPUT_PREPARED"
        return
    if scenario == "send_failure":
        assert outbox_repo.state == "TRANSPORT_OUTCOME_UNKNOWN"
        assert not [row for row in projected if row[0] == "history" or
                    (row[0] == "memory" and row[1] == "assistant") or row[0] == "completed"]
        return
    assert outbox_repo.state == "OUTPUT_COMMITTED_TO_TRANSPORT"
    body = next(message["body"] for message in messages if message["type"] == "http.response.body")
    import json
    response_body = json.loads(body)
    expected_text = "The response could not be verified safely." if runtime_notice else "A safe narrative."
    assert response_body["response"] == expected_text
    if runtime_notice:
        assert response_body["aviso"] is None
        assert response_body["contract_state"] == "DEGRADED_STRUCTURED"
        assert "faceta_sin_binding" not in body.decode("utf-8")
    assistant_rows = [row for row in projected if row[0] == "history" or row[0] == "completed" or
                      (row[0] == "memory" and row[1] == "assistant")]
    assert assistant_rows
    assert all((row[1] if row[0] != "memory" else row[2]) == "OUTPUT_COMMITTED_TO_TRANSPORT"
               for row in assistant_rows)
    assert all((row[2] if row[0] != "memory" else row[3]) in (None, expected_text)
               for row in assistant_rows)
    assert all("Provider" not in ((row[2] if row[0] != "memory" else row[3]) or "")
               for row in assistant_rows)
