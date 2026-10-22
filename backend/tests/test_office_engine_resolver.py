"""Unit contract for the inert Office engine catalogue resolver."""
from __future__ import annotations

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


def test_malformed_or_duplicate_classifications_and_unallowed_data_fail_closed():
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    for classifications, requested in (("[{}]", "SYSTEM_SMOKE"), ('["SYSTEM_SMOKE","SYSTEM_SMOKE"]', "SYSTEM_SMOKE"), ('["SYSTEM_SMOKE"]', "CLIENT_CONFIDENTIAL")):
        with pytest.raises(OfficeEngineUnavailable):
            resolve_office_engine_row(_row(allowed_data_classifications=classifications), data_classification=requested)
