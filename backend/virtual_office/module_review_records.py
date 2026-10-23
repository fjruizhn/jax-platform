"""Pure review-record/v1 validation for one sealed client artifact set.

Creative approval, a critic's rights verdict, and an effective release are
different authorities.  This module validates the first two as review inputs;
it cannot publish an artifact or create a release decision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .module_release_contract import ModuleReleaseContractRejected, validate_artifact_set


_DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
_HUMAN = re.compile(r"human:[a-z][a-z0-9-]{0,126}\Z")
_CRITIC = re.compile(r"agent:[a-z][a-z0-9._-]{0,126}\Z")
_REF = re.compile(r"[a-z][a-z0-9._:-]{0,255}\Z")
_EVIDENCE_ID = re.compile(r"[a-z][a-z0-9._-]{0,255}\Z")
_CREATIVE_FIELDS = {
    "schema_version", "record_type", "artifact_set_digest", "approver", "approval_ref",
}
_RIGHTS_FIELDS = {
    "schema_version", "record_type", "artifact_set_digest", "critic_principal", "decision",
    "receipt_ref", "evidence_ids",
}


class ModuleReviewRecordRejected(ValueError):
    """The supplied review record is not review-record/v1 input."""


@dataclass(frozen=True)
class ReviewRecordStatus:
    """Structural state that deliberately grants no review or release authority."""

    state: str
    artifact_digest: str
    creative_approved: bool
    rights_decision: str | None


def _reject(message: str) -> None:
    raise ModuleReviewRecordRejected(message)


def _string(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _reject(f"{field} is invalid")
    return value


def _artifact_digest(value: Any) -> str:
    return _string(value, "artifact_set_digest", _DIGEST)


def _validate_creative(record: dict[str, Any], root_digest: str) -> None:
    if (
        set(record) != _CREATIVE_FIELDS
        or type(record.get("schema_version")) is not int
        or record["schema_version"] != 1
    ):
        _reject("creative approval shape is invalid")
    if record.get("record_type") != "creative-approval":
        _reject("creative approval type is invalid")
    if _artifact_digest(record.get("artifact_set_digest")) != root_digest:
        _reject("creative approval artifact_set_digest does not match artifact set")
    _string(record.get("approver"), "creative approver", _HUMAN)
    _string(record.get("approval_ref"), "creative approval_ref", _REF)


def _validate_rights(record: dict[str, Any], root_digest: str) -> str:
    if (
        set(record) != _RIGHTS_FIELDS
        or type(record.get("schema_version")) is not int
        or record["schema_version"] != 1
    ):
        _reject("rights verdict shape is invalid")
    if record.get("record_type") != "rights-verdict":
        _reject("rights verdict type is invalid")
    if _artifact_digest(record.get("artifact_set_digest")) != root_digest:
        _reject("rights verdict artifact_set_digest does not match artifact set")
    _string(record.get("critic_principal"), "rights critic_principal", _CRITIC)
    decision = record.get("decision")
    if decision not in {"PASS", "REVIEW_REQUIRED", "FAIL"}:
        _reject("rights decision is invalid")
    _string(record.get("receipt_ref"), "rights receipt_ref", _REF)
    evidence_ids = record.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids or len(evidence_ids) > 100:
        _reject("rights evidence_ids are invalid")
    normalized = [_string(value, "rights evidence_id", _EVIDENCE_ID) for value in evidence_ids]
    if len(set(normalized)) != len(normalized):
        _reject("rights evidence_ids must be unique")
    return decision


def assess_review_records(artifact_set: Any, records: Any) -> ReviewRecordStatus:
    """Validate detached v1 review-record shape without granting authority.

    Both record types must bind to the exact ``artifact-set/v1`` root.  The
    identity strings, receipt reference, and evidence identifiers are all
    unverified caller claims at this boundary, so they never become a creative
    approval, rights PASS, or release-ready state.  A later contract must
    authenticate the human, verify a signed critic receipt against its
    candidate/revision/manifest/artifact binding, and resolve evidence before
    any authority can be derived.
    """

    try:
        validate_artifact_set(artifact_set)
    except ModuleReleaseContractRejected as error:
        raise ModuleReviewRecordRejected("artifact_set is invalid") from error
    root_digest = artifact_set["root_digest"]
    if not isinstance(records, list) or len(records) > 2:
        _reject("review records must be a bounded list")

    creative_seen = False
    rights_seen = False
    for record in records:
        if not isinstance(record, dict):
            _reject("review record must be an object")
        record_type = record.get("record_type")
        if record_type == "creative-approval":
            if creative_seen:
                _reject("duplicate creative approval")
            _validate_creative(record, root_digest)
            creative_seen = True
        elif record_type == "rights-verdict":
            if rights_seen:
                _reject("duplicate rights verdict")
            _validate_rights(record, root_digest)
            rights_seen = True
        else:
            _reject("review record type is invalid")

    return ReviewRecordStatus("PENDING_CONTRACT", root_digest, False, None)
