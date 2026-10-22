"""Fail-closed mapping for the Office-owned engine catalogue."""
from __future__ import annotations

import pytest


def _row(**overrides):
    row = {
        "engine_key": "glm",
        "display_name_key": "office.engine.glm",
        "model_ref": 42,
        "transport": "http_openai_compat",
        "allowed_data_classifications": '["SYSTEM_SMOKE"]',
        "version": 3,
        "engine_enabled": 1,
        "provider_id": "zhipu",
        "provider_status": "active",
        "provider_auth_type": "api_key",
        "model_id": "glm-5.3",
        "model_status": "available",
        "has_active_credential": 1,
    }
    row.update(overrides)
    return row


def test_resolved_engine_exposes_effective_identity_without_a_secret_or_url():
    """Removing the provider/model binding must make this consumer contract fail."""
    from virtual_office.engine_resolver import resolve_office_engine_row

    engine = resolve_office_engine_row(_row(), data_classification="SYSTEM_SMOKE")

    assert (engine.engine_key, engine.provider_id, engine.model_id, engine.model_ref, engine.version) == (
        "glm", "zhipu", "glm-5.3", 42, 3,
    )
    assert engine.transport == "http_openai_compat"
    assert engine.allowed_data_classifications == frozenset({"SYSTEM_SMOKE"})
    assert not hasattr(engine, "credential")
    assert not hasattr(engine, "base_url")


@pytest.mark.parametrize("field,value", [
    ("engine_enabled", 0),
    ("provider_status", "deprecated"),
    ("model_status", "gone"),
    ("has_active_credential", 0),
])
def test_unavailable_catalog_dependencies_fail_closed(field, value):
    """Deleting any availability gate must be caught before a dispatch exists."""
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row

    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(_row(**{field: value}), data_classification="SYSTEM_SMOKE")


def test_unallowed_classification_fails_closed_instead_of_selecting_another_engine():
    """Changing classification enforcement to fallback/default must fail this test."""
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row

    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(_row(), data_classification="CLIENT_CONFIDENTIAL")
