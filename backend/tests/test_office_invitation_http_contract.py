"""HTTP boundary contract shared with the isolated invitation UI."""
from __future__ import annotations

import inspect
import uuid

import pytest
from fastapi import FastAPI, HTTPException

from api.virtual_office.invitations import (
    _require_durable_idempotency,
    issue_invitation,
    revoke_invitation,
    router,
)


def _operation(path: str, method: str) -> dict:
    app = FastAPI()
    app.include_router(router)
    return app.openapi()["paths"][path][method]


def test_issue_and_revoke_expose_the_ui_header_and_expected_version_contract():
    issue = _operation("/api/virtual-office/projects/{project_id}/invitations", "post")
    revoke = _operation(
        "/api/virtual-office/projects/{project_id}/invitations/{invitation_id}", "delete")
    issue_header = next(parameter for parameter in issue["parameters"]
                        if parameter["name"] == "Idempotency-Key")
    revoke_header = next(parameter for parameter in revoke["parameters"]
                         if parameter["name"] == "Idempotency-Key")
    expected_version = next(parameter for parameter in revoke["parameters"]
                            if parameter["name"] == "expected_version")
    assert issue_header["in"] == revoke_header["in"] == "header"
    assert issue_header["required"] is revoke_header["required"] is True
    assert expected_version == {
        "name": "expected_version", "in": "query", "required": True,
        "schema": {"type": "integer", "minimum": 1, "title": "Expected Version"},
    }
    assert "idempotency_key" in inspect.signature(issue_invitation).parameters
    assert "idempotency_key" in inspect.signature(revoke_invitation).parameters


def test_invitation_retry_uuid_is_validated_and_returned_for_the_receipt_service():
    with pytest.raises(HTTPException) as invalid:
        _require_durable_idempotency("not-a-uuid")
    assert invalid.value.status_code == 422
    assert invalid.value.detail == "office_invitation_idempotency_invalid"

    key = str(uuid.uuid4())
    assert _require_durable_idempotency(key) == key
