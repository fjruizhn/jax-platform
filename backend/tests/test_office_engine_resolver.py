"""Unit contract for the inert Office engine catalogue resolver."""
from __future__ import annotations

import asyncio

import pytest


def _row(**overrides):
    from virtual_office.engine_resolver import engine_binding_fingerprint
    row = {"engine_key": "glm", "display_name_key": "office.engine.glm", "model_ref": 42,
           "transport": "http_openai_compat", "allowed_data_classifications": '["SYSTEM_SMOKE"]',
           "version": 3, "engine_enabled": 1, "provider_id": "zhipu", "provider_status": "active",
           "provider_auth_type": "api_key", "model_id": "glm-5.3", "model_status": "available",
           "active_credential_count": 1, "provider_fingerprint": "a" * 64,
           "model_fingerprint": "b" * 64, "credential_fingerprint": "c" * 64}
    row["binding_fingerprint"] = engine_binding_fingerprint(
        engine_key=row["engine_key"], model_ref=row["model_ref"], transport=row["transport"],
        allowed_data_classifications=frozenset(("SYSTEM_SMOKE",)),
        provider_fingerprint=row["provider_fingerprint"], model_fingerprint=row["model_fingerprint"],
        credential_fingerprint=row["credential_fingerprint"], version=row["version"])
    row.update(overrides)
    return row


def test_resolved_engine_exposes_identity_without_secret_or_url():
    from virtual_office.engine_resolver import resolve_office_engine_row
    engine = resolve_office_engine_row(_row(), data_classification="SYSTEM_SMOKE")
    assert (engine.engine_key, engine.provider_id, engine.model_id, engine.model_ref, engine.version) == ("glm", "zhipu", "glm-5.3", 42, 3)
    assert not hasattr(engine, "credential")
    assert not hasattr(engine, "base_url")


@pytest.mark.parametrize("field,value", [("engine_enabled", 0), ("provider_status", "deprecated"),
                                            ("model_status", "gone"), ("active_credential_count", 0),
                                            ("active_credential_count", 2)])
def test_unavailable_catalogue_dependencies_fail_closed(field, value):
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(_row(**{field: value}), data_classification="SYSTEM_SMOKE")


@pytest.mark.parametrize("field", ["provider_fingerprint", "model_fingerprint", "credential_fingerprint"])
def test_mutable_binding_drift_fails_closed(field):
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(_row(**{field: "d" * 64}), data_classification="SYSTEM_SMOKE")


def test_expanding_allowed_data_classifications_with_the_old_digest_fails_closed():
    """A catalogue mutator cannot widen the caller's data authority in place."""
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(
            _row(allowed_data_classifications='["SYSTEM_SMOKE","CLIENT_CONFIDENTIAL"]'),
            data_classification="CLIENT_CONFIDENTIAL",
        )


def test_binding_fingerprint_canonicalizes_classification_order_without_weakening_membership():
    from virtual_office.engine_resolver import engine_binding_fingerprint
    fields = dict(engine_key="glm", model_ref=42, transport="http_openai_compat",
                  provider_fingerprint="a" * 64, model_fingerprint="b" * 64,
                  credential_fingerprint="c" * 64, version=3)
    assert engine_binding_fingerprint(
        **fields, allowed_data_classifications=frozenset(("SYSTEM_SMOKE", "CLIENT_CONFIDENTIAL")),
    ) == engine_binding_fingerprint(
        **fields, allowed_data_classifications=frozenset(("CLIENT_CONFIDENTIAL", "SYSTEM_SMOKE")),
    )


def test_sql_binding_snapshot_includes_every_dispatch_route_field():
    """Removing a route field from the SQL snapshot cannot pass silently."""
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_in_cursor

    class Cursor:
        statement = ""

        async def execute(self, statement, _params):
            self.statement = statement

        async def fetchone(self):
            return None

    cursor = Cursor()
    with pytest.raises(OfficeEngineUnavailable):
        asyncio.run(resolve_office_engine_in_cursor(
            cursor, engine_key="glm", data_classification="SYSTEM_SMOKE"))
    for field in (
        "p.base_url", "p.auth_type", "p.api_key_transport", "p.status", "p.is_local",
        "m.model_id", "m.status", "m.context_window", "m.supports_tool_use",
        "m.supports_structured_output", "m.max_tokens_param", "m.max_output_tokens", "m.input_modalities",
        "SHA2(c.encrypted_value,256)",
    ):
        assert field in cursor.statement


def test_malformed_or_duplicate_classifications_and_unallowed_data_fail_closed():
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    for classifications, requested in (("[{}]", "SYSTEM_SMOKE"), ('["SYSTEM_SMOKE","SYSTEM_SMOKE"]', "SYSTEM_SMOKE"), ('["SYSTEM_SMOKE"]', "CLIENT_CONFIDENTIAL")):
        with pytest.raises(OfficeEngineUnavailable):
            resolve_office_engine_row(_row(allowed_data_classifications=classifications), data_classification=requested)
