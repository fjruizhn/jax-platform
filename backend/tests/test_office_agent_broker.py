"""Fast negative contracts for the Office-only model transport."""
from __future__ import annotations

import pytest
import httpx


def _engine_row(**overrides):
    from virtual_office.engine_resolver import engine_binding_fingerprint
    row = {
        "engine_key": "glm", "display_name_key": "office.engine.glm", "model_ref": 42,
        "transport": "http_openai_compat", "allowed_data_classifications": '["INTERNAL_CONFIDENTIAL"]',
        "version": 1, "engine_enabled": 1, "activation_epoch": 1, "provider_id": "zhipu",
        "provider_status": "active", "provider_auth_type": "api_key", "provider_api_key_transport": "header_bearer", "model_id": "glm-5.3",
            "model_status": "available", "max_tokens_param": "max_tokens", "max_output_tokens": 2048,
            "credential_id": 7, "credential_state": "active",
        "provider_fingerprint": "a" * 64, "model_fingerprint": "b" * 64,
        "credential_fingerprint": "c" * 64,
    }
    row.update(overrides)
    row["binding_fingerprint"] = engine_binding_fingerprint(
        engine_key=row["engine_key"], model_ref=row["model_ref"], transport=row["transport"],
        allowed_data_classifications=frozenset({"INTERNAL_CONFIDENTIAL"}),
        provider_fingerprint=row["provider_fingerprint"], model_fingerprint=row["model_fingerprint"],
            credential_id=row["credential_id"], credential_fingerprint=row["credential_fingerprint"], engine_enabled=row["engine_enabled"],
        activation_epoch=row["activation_epoch"], version=row["version"])
    return row


def test_engine_never_accepts_a_changed_model_or_credential_fingerprint():
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    row = _engine_row()
    assert resolve_office_engine_row(row, data_classification="INTERNAL_CONFIDENTIAL").model_id == "glm-5.3"
    row["credential_fingerprint"] = "d" * 64
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(row, data_classification="INTERNAL_CONFIDENTIAL")


def test_engine_rejects_a_provider_transport_the_broker_cannot_send():
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(_engine_row(provider_api_key_transport="query"), data_classification="INTERNAL_CONFIDENTIAL")


@pytest.mark.parametrize("url", [
    "http://api.z.ai/api/paas/v4", "https://api.z.ai/other", "https://api.z.ai/api/paas/v4?next=x",
    "https://api.z.ai.evil.invalid/api/paas/v4", "https://api.z.ai:444/api/paas/v4",
])
def test_broker_rejects_noncanonical_or_redirectable_provider_urls(monkeypatch, url):
    from virtual_office.agent_broker import OfficeBrokerUnavailable, _allowed_zai_url
    monkeypatch.setenv("OFFICE_BROKER_ALLOWED_BASE_URLS", "https://api.z.ai/api/paas/v4")
    with pytest.raises(OfficeBrokerUnavailable):
        _allowed_zai_url(url)


def test_broker_requires_explicit_base_url_configuration(monkeypatch):
    from virtual_office.agent_broker import OfficeBrokerUnavailable, _allowed_zai_url
    monkeypatch.delenv("OFFICE_BROKER_ALLOWED_BASE_URLS", raising=False)
    with pytest.raises(OfficeBrokerUnavailable):
        _allowed_zai_url("https://api.z.ai/api/paas/v4")


def test_broker_uses_the_explicitly_allowlisted_https_completion_url(monkeypatch):
    from virtual_office.agent_broker import _allowed_zai_url
    monkeypatch.setenv("OFFICE_BROKER_ALLOWED_BASE_URLS", "https://api.z.ai/api/paas/v4")
    assert _allowed_zai_url("https://api.z.ai/api/paas/v4/") == "https://api.z.ai/api/paas/v4/chat/completions"


def test_broker_rejects_a_url_not_in_the_explicit_allowlist(monkeypatch):
    from virtual_office.agent_broker import OfficeBrokerUnavailable, _allowed_zai_url
    monkeypatch.setenv("OFFICE_BROKER_ALLOWED_BASE_URLS", "https://api.z.ai/api/paas/v4")
    with pytest.raises(OfficeBrokerUnavailable):
        _allowed_zai_url("https://api.z.ai/api/paas/v5")


def test_agent_runtime_revision_fits_its_durable_column():
    from virtual_office.migrations import RUNTIME_REVISION
    assert len(RUNTIME_REVISION) <= 64


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.queries = []

    async def execute(self, *args):
        self.queries.append(args)
        return None

    async def fetchone(self):
        return self.row


@pytest.mark.asyncio
async def test_broker_uses_only_the_credential_pinned_by_the_engine(monkeypatch):
    import virtual_office.agent_broker as broker
    from virtual_office.engine_resolver import resolve_office_engine_row

    monkeypatch.setattr(broker, "decrypt_db_secret", lambda _value: "key")
    monkeypatch.setenv("OFFICE_BROKER_ALLOWED_BASE_URLS", "https://api.z.ai/api/paas/v4")
    engine = resolve_office_engine_row(_engine_row(), data_classification="INTERNAL_CONFIDENTIAL")
    called = []

    async def pinned(cur, *, engine, for_update):
        called.append((cur, engine.credential_id, for_update))
        return "ciphertext-for-pinned-credential"

    monkeypatch.setattr(broker, "load_pinned_office_credential_in_cursor", pinned)
    cursor = _Cursor(("https://api.z.ai/api/paas/v4",))
    target = await broker.load_dispatch_target_in_cursor(cursor, engine=engine)
    assert target.api_key == "key"
    assert target.url == "https://api.z.ai/api/paas/v4/chat/completions"
    assert called == [(cursor, 7, True)]
    assert "FROM provider WHERE id=%s" in cursor.queries[0][0]
    assert "credential" not in cursor.queries[0][0]


@pytest.mark.asyncio
async def test_dispatch_sends_an_explicit_json_content_type_and_body(monkeypatch):
    import virtual_office.agent_broker as broker
    from virtual_office.engine_resolver import resolve_office_engine_row

    captured = {}

    class Response:
        status_code = 200
        is_redirect = False
        headers = {}

        async def aiter_bytes(self):
            yield b'{"choices":[{"message":{"content":"ok"}}]}'

    class Stream:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, **kwargs):
            assert method == "POST"
            captured["url"] = url
            captured.update(kwargs)
            return Stream()

    monkeypatch.setattr(broker.httpx, "AsyncClient", Client)
    target = broker.OfficeDispatchTarget(
        resolve_office_engine_row(_engine_row(), data_classification="INTERNAL_CONFIDENTIAL"),
        "secret", "https://api.z.ai/api/paas/v4/chat/completions")
    assert await broker.dispatch(
        target, instructions="system", user_input="hello",
        max_tokens_field="max_tokens", max_output_tokens=123, max_response_bytes=1024, timeout_seconds=1,
    ) == "ok"
    assert captured["headers"] == {"Authorization": "Bearer secret", "Content-Type": "application/json"}
    assert captured["json"] == {"model": "glm-5.3", "messages": [
        {"role": "system", "content": "system"}, {"role": "user", "content": "hello"},
    ], "tools": [], "max_tokens": 123}


@pytest.mark.asyncio
async def test_dispatch_uses_the_model_declared_token_field_and_streams_a_bounded_response(monkeypatch):
    """The Office broker must not inherit a token parameter or buffer unbounded output."""
    import virtual_office.agent_broker as broker
    from virtual_office.engine_resolver import resolve_office_engine_row

    captured = {}

    class Response:
        status_code = 200
        is_redirect = False
        headers = {}

        async def aiter_bytes(self):
            yield b'{"choices":[{"message":{"content":"ok"}}]}'

    class Stream:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured.update(kwargs)
            return Stream()

    monkeypatch.setattr(broker.httpx, "AsyncClient", Client)
    target = broker.OfficeDispatchTarget(
        resolve_office_engine_row(_engine_row(), data_classification="INTERNAL_CONFIDENTIAL"),
        "secret", "https://api.z.ai/api/paas/v4/chat/completions")
    assert await broker.dispatch(
        target, instructions="system", user_input="hello",
        max_tokens_field="max_completion_tokens", max_output_tokens=123,
        max_response_bytes=1024, timeout_seconds=1,
    ) == "ok"
    assert captured["method"] == "POST"
    assert captured["headers"] == {"Authorization": "Bearer secret", "Content-Type": "application/json"}
    assert captured["json"]["max_completion_tokens"] == 123
    assert "max_tokens" not in captured["json"]


@pytest.mark.asyncio
async def test_dispatch_rejects_a_response_larger_than_the_office_limit(monkeypatch):
    import virtual_office.agent_broker as broker
    from virtual_office.engine_resolver import resolve_office_engine_row

    class Response:
        status_code = 200
        is_redirect = False
        headers = {}

        async def aiter_bytes(self):
            yield b"x" * 1025

    class Stream:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return Stream()

    monkeypatch.setattr(broker.httpx, "AsyncClient", Client)
    target = broker.OfficeDispatchTarget(
        resolve_office_engine_row(_engine_row(), data_classification="INTERNAL_CONFIDENTIAL"),
        "secret", "https://api.z.ai/api/paas/v4/chat/completions")
    with pytest.raises(broker.OfficeBrokerUnavailable):
        await broker.dispatch(
            target, instructions="system", user_input="hello",
            max_tokens_field="max_tokens", max_output_tokens=123,
            max_response_bytes=1024, timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_dispatch_has_an_absolute_timeout_while_provider_trickles(monkeypatch):
    import asyncio
    import virtual_office.agent_broker as broker
    from virtual_office.engine_resolver import resolve_office_engine_row
    class Response:
        status_code = 200; is_redirect = False; headers = {}
        async def aiter_bytes(self):
            while True:
                await asyncio.sleep(0.02)
                yield b" "
    class Stream:
        async def __aenter__(self): return Response()
        async def __aexit__(self, *_args): return None
    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        def stream(self, *_args, **_kwargs): return Stream()
    monkeypatch.setattr(broker.httpx, "AsyncClient", Client)
    target = broker.OfficeDispatchTarget(resolve_office_engine_row(_engine_row(), data_classification="INTERNAL_CONFIDENTIAL"), "secret", "https://api.z.ai/api/paas/v4/chat/completions")
    with pytest.raises(broker.OfficeBrokerUnavailable):
        await broker.dispatch(target, instructions="system", user_input="hello", max_tokens_field="max_tokens", max_output_tokens=1, max_response_bytes=1000000, timeout_seconds=1)
