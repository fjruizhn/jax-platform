"""Contract for the explicit first-engine activation command."""
from __future__ import annotations

import pytest
from pathlib import Path


def test_activation_cli_requires_unique_uppercase_classifications() -> None:
    from virtual_office.engine_activation_cli import parse_classifications

    assert parse_classifications("INTERNAL_CONFIDENTIAL,PROJECT_SECRET") == frozenset({
        "INTERNAL_CONFIDENTIAL", "PROJECT_SECRET",
    })
    with pytest.raises(ValueError):
        parse_classifications("INTERNAL_CONFIDENTIAL,INTERNAL_CONFIDENTIAL")
    with pytest.raises(ValueError):
        parse_classifications("internal")
    with pytest.raises(ValueError, match="INTERNAL_CONFIDENTIAL"):
        parse_classifications("INTERNAL")


def test_documented_first_engine_activation_uses_office_classification() -> None:
    recipe = (Path(__file__).parents[2] / "docs" / "historia" / "2026-10-22-selector-agentes-office.md").read_text(encoding="utf-8")
    assert "--classifications INTERNAL_CONFIDENTIAL --activate" in recipe


@pytest.mark.asyncio
async def test_activation_repins_to_the_newest_active_credential_during_rotation() -> None:
    from virtual_office.engine_activation_cli import _catalogue_fingerprints

    provider = ("zhipu", "https://api.z.ai/api/paas/v4", "api_key", "header_bearer", "active", 0)
    model = (42, "zhipu", "glm-5.3", "available", 0, 128000, 0, 0, "max_tokens", 2048, "text")
    old = (7, "zhipu", "active", "2026-10-22 10:00:00", None, "a" * 64)
    newest = (8, "zhipu", "active", "2026-10-22 10:01:00", None, "b" * 64)

    class Cursor:
        def __init__(self):
            self.queries = []
            self.ones = [provider, model, ("p" * 64,), ("m" * 64,), ("c" * 64,)]

        async def execute(self, query, params=()):
            self.queries.append((query, params))

        async def fetchone(self):
            return self.ones.pop(0)

        async def fetchall(self):
            return [newest, old]

    cursor = Cursor()
    model_ref, credential_id, *_ = await _catalogue_fingerprints(cursor, "zhipu", "glm-5.3")
    assert (model_ref, credential_id) == (42, 8)
    credential_query = cursor.queries[2][0]
    assert "ORDER BY activated_at DESC,id DESC FOR UPDATE" in credential_query


@pytest.mark.asyncio
async def test_activation_cli_rejects_a_non_glm_provider_before_database_access() -> None:
    from virtual_office.engine_activation_cli import _catalogue_fingerprints

    with pytest.raises(ValueError, match="zhipu"):
        await _catalogue_fingerprints(None, "openai", "gpt-any")


def test_office_resolver_rejects_an_openai_compatible_provider_other_than_glm() -> None:
    from virtual_office.engine_resolver import OfficeEngineUnavailable, engine_binding_fingerprint, resolve_office_engine_row

    row = {
        "engine_key": "glm", "display_name_key": "office.engine.glm", "model_ref": 42,
        "transport": "http_openai_compat", "allowed_data_classifications": '["INTERNAL_CONFIDENTIAL"]',
        "version": 1, "engine_enabled": 1, "activation_epoch": 1, "provider_id": "openai",
        "provider_status": "active", "provider_auth_type": "api_key", "provider_api_key_transport": "header_bearer",
        "model_id": "gpt-any", "model_status": "available", "max_tokens_param": "max_tokens", "max_output_tokens": 2048,
        "credential_id": 7, "credential_state": "active", "provider_fingerprint": "a" * 64,
        "model_fingerprint": "b" * 64, "credential_fingerprint": "c" * 64,
    }
    row["binding_fingerprint"] = engine_binding_fingerprint(
        engine_key="glm", model_ref=42, transport="http_openai_compat",
        allowed_data_classifications=frozenset({"INTERNAL_CONFIDENTIAL"}), provider_fingerprint="a" * 64,
        model_fingerprint="b" * 64, credential_id=7, credential_fingerprint="c" * 64,
        engine_enabled=1, activation_epoch=1, version=1,
    )
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(row, data_classification="INTERNAL_CONFIDENTIAL")


def test_rotation_and_directed_revocation_serialize_with_activation() -> None:
    source = (Path(__file__).parents[1] / "api" / "admin" / "credentials.py").read_text(encoding="utf-8")
    rotate = source[source.index("async def rotate_credential"):source.index("async def revoke_credential_by_id")]
    targeted = source[source.index("async def revoke_credential_by_id"):source.index("async def revoke_credential(")]
    assert "SELECT id FROM provider WHERE id=%s FOR UPDATE" in rotate
    assert "SELECT id FROM credential WHERE provider_id=%s AND state='active' FOR UPDATE" in rotate
    assert "WHERE id=%s AND provider_id=%s AND state='active' FOR UPDATE" in targeted
    assert "e.credential_id=%s AND e.enabled=TRUE FOR UPDATE" in targeted
