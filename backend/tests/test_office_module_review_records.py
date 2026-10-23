from __future__ import annotations

import pytest

from virtual_office.module_release_contract import build_artifact_set
from virtual_office.module_review_records import (
    ModuleReviewRecordRejected,
    assess_review_records,
)


def _artifact_set(tmp_path):
    asset = tmp_path / "assets" / "logo.svg"
    asset.parent.mkdir()
    asset.write_bytes(b"<svg />")
    asset.chmod(0o644)
    return build_artifact_set(tmp_path)


def _creative(digest: str):
    return {
        "schema_version": 1,
        "record_type": "creative-approval",
        "artifact_set_digest": digest,
        "approver": "human:fernando",
        "approval_ref": "openproject:355:creative-1",
    }


def _rights(digest: str, decision: str = "PASS"):
    return {
        "schema_version": 1,
        "record_type": "rights-verdict",
        "artifact_set_digest": digest,
        "critic_principal": "agent:critic",
        "decision": decision,
        "receipt_ref": "critic:355:rights-1",
        "evidence_ids": ["license.logo-001"],
    }


def test_two_distinct_records_on_the_same_artifact_are_ready_for_a_separate_release_decision(tmp_path):
    artifact_set = _artifact_set(tmp_path)

    result = assess_review_records(
        artifact_set,
        [_creative(artifact_set["root_digest"]), _rights(artifact_set["root_digest"])],
    )

    assert result.state == "PENDING_RELEASE_DECISION"
    assert result.artifact_digest == artifact_set["root_digest"]
    assert result.creative_approved is True
    assert result.rights_decision == "PASS"


def test_rights_pass_is_not_a_human_creative_approval(tmp_path):
    artifact_set = _artifact_set(tmp_path)

    result = assess_review_records(artifact_set, [_rights(artifact_set["root_digest"])])

    assert result.state == "REVIEW_REQUIRED"
    assert result.creative_approved is False
    assert result.rights_decision == "PASS"


def test_rejects_records_bound_to_another_artifact_set(tmp_path):
    artifact_set = _artifact_set(tmp_path)

    with pytest.raises(ModuleReviewRecordRejected, match="artifact_set_digest"):
        assess_review_records(
            artifact_set,
            [_creative(artifact_set["root_digest"]), _rights("sha256:" + "f" * 64)],
        )


def test_rejects_critic_principal_as_a_creative_approver(tmp_path):
    artifact_set = _artifact_set(tmp_path)
    creative = _creative(artifact_set["root_digest"])
    creative["approver"] = "agent:critic"

    with pytest.raises(ModuleReviewRecordRejected, match="approver"):
        assess_review_records(artifact_set, [creative, _rights(artifact_set["root_digest"])])


def test_rights_failure_remains_review_required_even_with_human_approval(tmp_path):
    artifact_set = _artifact_set(tmp_path)

    result = assess_review_records(
        artifact_set,
        [_creative(artifact_set["root_digest"]), _rights(artifact_set["root_digest"], "FAIL")],
    )

    assert result.state == "REVIEW_REQUIRED"
    assert result.creative_approved is True
    assert result.rights_decision == "FAIL"


def test_rejects_boolean_schema_version_instead_of_coercing_it_to_v1(tmp_path):
    artifact_set = _artifact_set(tmp_path)
    rights = _rights(artifact_set["root_digest"])
    rights["schema_version"] = True

    with pytest.raises(ModuleReviewRecordRejected, match="shape"):
        assess_review_records(artifact_set, [_creative(artifact_set["root_digest"]), rights])
