"""Fast negative contracts for the Office-only model transport."""
from __future__ import annotations

import pytest


def _engine_row(**overrides):
    from virtual_office.engine_resolver import engine_binding_fingerprint
    row = {
        "engine_key": "glm", "display_name_key": "office.engine.glm", "model_ref": 42,
        "transport": "http_openai_compat", "allowed_data_classifications": '["INTERNAL_CONFIDENTIAL"]',
        "version": 1, "engine_enabled": 1, "activation_epoch": 1, "provider_id": "zhipu",
        "provider_status": "active", "provider_auth_type": "api_key", "model_id": "glm-5.3",
        "model_status": "available", "active_credential_count": 1,
        "provider_fingerprint": "a" * 64, "model_fingerprint": "b" * 64,
        "credential_fingerprint": "c" * 64,
    }
    row.update(overrides)
    row["binding_fingerprint"] = engine_binding_fingerprint(
        engine_key=row["engine_key"], model_ref=row["model_ref"], transport=row["transport"],
        allowed_data_classifications=frozenset({"INTERNAL_CONFIDENTIAL"}),
        provider_fingerprint=row["provider_fingerprint"], model_fingerprint=row["model_fingerprint"],
        credential_fingerprint=row["credential_fingerprint"], engine_enabled=row["engine_enabled"],
        activation_epoch=row["activation_epoch"], version=row["version"])
    return row


def test_engine_never_accepts_a_changed_model_or_credential_fingerprint():
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    row = _engine_row()
    assert resolve_office_engine_row(row, data_classification="INTERNAL_CONFIDENTIAL").model_id == "glm-5.3"
    row["credential_fingerprint"] = "d" * 64
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(row, data_classification="INTERNAL_CONFIDENTIAL")


@pytest.mark.parametrize("url", [
    "http://api.z.ai/api/paas/v4", "https://api.z.ai/other", "https://api.z.ai/api/paas/v4?next=x",
    "https://api.z.ai.evil.invalid/api/paas/v4", "https://api.z.ai:444/api/paas/v4",
])
def test_broker_rejects_noncanonical_or_redirectable_provider_urls(url):
    from virtual_office.agent_broker import OfficeBrokerUnavailable, _allowed_zai_url
    with pytest.raises(OfficeBrokerUnavailable):
        _allowed_zai_url(url)


def test_broker_uses_one_fixed_https_completion_url():
    from virtual_office.agent_broker import _allowed_zai_url
    assert _allowed_zai_url("https://api.z.ai/api/paas/v4/") == "https://api.z.ai/api/paas/v4/chat/completions"


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *_args):
        return None

    async def fetchall(self):
        return self.rows


@pytest.mark.asyncio
async def test_broker_rejects_zero_or_multiple_active_credentials(monkeypatch):
    import virtual_office.agent_broker as broker
    from virtual_office.engine_resolver import resolve_office_engine_row

    monkeypatch.setattr(broker, "decrypt_db_secret", lambda _value: "key")
    engine = resolve_office_engine_row(_engine_row(), data_classification="INTERNAL_CONFIDENTIAL")
    for rows in ([], [("https://api.z.ai/api/paas/v4", "one"), ("https://api.z.ai/api/paas/v4", "two")]):
        with pytest.raises(broker.OfficeBrokerUnavailable):
            await broker.load_dispatch_target_in_cursor(_Cursor(rows), engine=engine)
