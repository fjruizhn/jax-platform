"""Web Chat's B9-only memory prompt boundary."""
import asyncio

import pytest

import api.chat as chat
from auth.models import AuthUser
from jax.memory.b9 import (
    Lifecycle, MemoryEnvelope, MemoryObject, MemoryProvenance, MemoryRevision,
    ObjectKind, PromptMemoryContext, Visibility,
)


def _user(*, tenant="tenant-a", user_id="7"):
    return AuthUser(user_id=user_id, tenant_id=tenant, role="user")


def _context(payload="private note"):
    obj = MemoryObject("m-1", ObjectKind.FACT, "tenant-a", 1.0)
    rev = MemoryRevision("r-1", "m-1", "digest", Visibility.USER_PRIVATE,
                         "7", None, Lifecycle.ACTIVE, 1.0, payload)
    prov = MemoryProvenance("p-1", "r-1", (), "import", "1", "user:7",
                            "USER", "7", None, None, 1.0)
    return PromptMemoryContext((MemoryEnvelope(obj, rev, (prov,), (), {}),))


def test_scope_is_derived_from_authenticated_user_not_request_authority():
    scope = chat._scope_for_chat(_user(), None)
    assert scope.tenant_id == "tenant-a"
    assert scope.subject_user_id == "7"
    assert scope.actor_principal == "user:7"
    with pytest.raises(chat.HTTPException) as exc:
        chat._scope_for_chat(_user(), 99)
    assert exc.value.detail["code"] == "project_membership_unverified"


def test_conversation_cache_namespace_includes_tenant():
    assert chat._conversation_cache_key("tenant-a", "same-user") != chat._conversation_cache_key("tenant-b", "same-user")
    with pytest.raises(ValueError):
        chat._conversation_cache_key("", "same-user")


async def test_persistent_conversation_cache_keeps_equal_ids_in_tenants_isolated(monkeypatch):
    class Memory:
        is_connected = True
        def __init__(self): self.calls = 0
        async def start_conversation(self, **_kwargs):
            self.calls += 1
            return f"conversation-{self.calls}"
        async def end_conversation(self, _uuid): pass

    memory = Memory()
    async def ready(): return True
    monkeypatch.setattr(chat, "_ensure_memory", ready)
    monkeypatch.setattr(chat, "_memory", memory)
    chat._conv_uuids.clear()
    try:
        first = await chat._get_conv_uuid(7, "tenant-a", None)
        second = await chat._get_conv_uuid(7, "tenant-b", None)
        assert first != second
        assert memory.calls == 2
    finally:
        chat._conv_uuids.clear()


async def test_b9_context_is_rendered_in_system_prompt_not_as_raw_history(monkeypatch):
    captured = {}

    async def fake_resolve(_facet):
        class F:
            transport = "ollama"; model = "model"; provider_id = "provider"
        return F()

    async def fake_call(system_prompt, history, *_args, **_kwargs):
        captured["system"] = system_prompt
        captured["history"] = history
        return "ok", 1, 1

    monkeypatch.setattr(chat, "resolve_facet", fake_resolve)
    monkeypatch.setattr(chat, "_call_ollama", fake_call)
    config = {"personalities": {"jax_local": {"system_prompt": "system"}}}
    text, _usage, _outcome = await chat._invoke_facet_dispatch(
        "jax_local", config, "tenant-a:7", "hello", memory_context=_context())

    assert text == "ok"
    assert "[UNVERIFIED MEMORY id=m-1 revision=r-1]" in captured["system"]
    assert "private note" in captured["system"]
    assert captured["history"] == []


def test_b9_reader_failure_never_falls_back_to_legacy_rows(monkeypatch):
    class FailingReader:
        def __init__(self, _pool): pass
        async def prompt_context(self, *_args, **_kwargs): raise RuntimeError("db unavailable")

    async def pool(): return object()
    monkeypatch.setattr(chat, "MariaDBB9Reader", FailingReader)
    monkeypatch.setattr(chat, "get_pool", pool)
    result = asyncio.run(chat._prompt_memory_context(chat._scope_for_chat(_user(), None)))
    assert result.entries == ()
