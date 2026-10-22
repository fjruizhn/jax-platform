from __future__ import annotations

import hashlib
import inspect
import importlib.util
import json
import sys
from pathlib import Path

import pytest


_BACKEND = Path(__file__).parents[1]
_FORMAT_FIXTURE = _BACKEND / "tests" / "fixtures" / "license-evidence-v1-formats.cases.json"
_FORMAT_FIXTURE_SHA256 = "f11942722d76cdfa1854c4bacb430a12371589e2502cbc8c3ee630d1c825bab0"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"virtual_office.{name}", _BACKEND / "virtual_office" / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


artifact_contract = _load("module_release_contract")
license_evidence = _load("module_license_evidence")
_FORMAT_VECTORS = json.loads(_FORMAT_FIXTURE.read_text(encoding="utf-8"))
_FORMAT_CASES = _FORMAT_VECTORS["cases"]


def _artifact_set(tmp_path):
    asset = tmp_path / "assets" / "logo.svg"
    asset.parent.mkdir()
    asset.write_bytes(b"<svg />")
    asset.chmod(0o644)
    return artifact_contract.build_artifact_set(tmp_path)


def _evidence(artifact_set):
    entry = artifact_set["entries"][0]
    return {
        "schema_version": 1,
        "evidence_id": "license.logo-001",
        "license_id": "stock-provider-license-123",
        "rights_kind": "asset",
        "artifact_digest": artifact_set["root_digest"],
        "asset_path": entry["path"],
        "asset_digest": entry["digest"],
        "source_uri": "https://licenses.example.test/records/logo-001",
        "source_digest": "sha256:" + "a" * 64,
        "source_verified_by": "human:fernando",
        "source_verified_at": "2026-10-22T12:00:00Z",
        "verification_receipt": "ledger:license-evidence.logo-001",
        "attribution": "Logo licensed from Example Stock.",
        "expires_at": None,
    }


def test_returns_pending_verification_for_evidence_bound_to_exact_artifact_asset(tmp_path):
    artifact_set = _artifact_set(tmp_path)

    result = license_evidence.validate_license_evidence(_evidence(artifact_set), artifact_set)

    assert result.state == "PENDING_VERIFICATION"
    assert result.artifact_digest == artifact_set["root_digest"]
    assert result.evidence_id == "license.logo-001"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_digest", "sha256:" + "b" * 64),
        ("asset_path", "assets/missing.svg"),
        ("asset_digest", "sha256:" + "b" * 64),
    ],
)
def test_rejects_evidence_not_bound_to_the_exact_artifact_inventory_entry(tmp_path, field, value):
    artifact_set = _artifact_set(tmp_path)
    evidence = _evidence(artifact_set)
    evidence[field] = value

    with pytest.raises(license_evidence.LicenseEvidenceRejected):
        license_evidence.validate_license_evidence(evidence, artifact_set)


def test_uses_one_artifact_snapshot_when_the_caller_mutates_after_validation(tmp_path, monkeypatch):
    artifact_set = _artifact_set(tmp_path)
    evidence = _evidence(artifact_set)
    evidence["asset_path"] = "evil.txt"
    evidence["asset_digest"] = "sha256:" + "f" * 64
    real_validate = license_evidence.validate_artifact_set

    def validate_then_swap(value):
        real_validate(value)
        artifact_set["entries"][0]["path"] = "evil.txt"
        artifact_set["entries"][0]["digest"] = "sha256:" + "f" * 64

    monkeypatch.setattr(license_evidence, "validate_artifact_set", validate_then_swap)

    with pytest.raises(license_evidence.LicenseEvidenceRejected):
        license_evidence.validate_license_evidence(evidence, artifact_set)


def test_rejects_non_object_top_level_values_with_the_domain_error(tmp_path):
    artifact_set = _artifact_set(tmp_path)
    with pytest.raises(license_evidence.LicenseEvidenceRejected):
        license_evidence.validate_license_evidence([], artifact_set)
    with pytest.raises(license_evidence.LicenseEvidenceRejected):
        license_evidence.validate_license_evidence(_evidence(artifact_set), [])


@pytest.mark.parametrize("extra", ["x" * 1_000_000, [[[[[]]]]]])
def test_rejects_oversize_or_nested_unknown_artifact_data_before_snapshot(tmp_path, extra):
    artifact_set = _artifact_set(tmp_path)
    if isinstance(extra, list):
        for _ in range(1_100):
            extra = [extra]
    artifact_set["untrusted_extra"] = extra

    with pytest.raises(license_evidence.LicenseEvidenceRejected):
        license_evidence.validate_license_evidence(_evidence(artifact_set), artifact_set)


def _mutate_on_snapshot_line(target_source, mutation):
    lines, first_line = inspect.getsourcelines(license_evidence._artifact_snapshot)
    target_line = first_line + next(index for index, line in enumerate(lines) if target_source in line)
    code = license_evidence._artifact_snapshot.__code__

    def trace(frame, event, _argument):
        if frame.f_code is code and event == "line" and frame.f_lineno == target_line:
            mutation()
            sys.settrace(None)
        return trace

    return trace


def test_rejects_entry_list_growth_during_bounded_projection(tmp_path):
    artifact_set = _artifact_set(tmp_path)
    entry = artifact_set["entries"][0].copy()
    tracer = _mutate_on_snapshot_line(
        "entries_view =", lambda: artifact_set["entries"].extend(entry.copy() for _ in range(10_000))
    )
    sys.settrace(tracer)
    try:
        with pytest.raises(license_evidence.LicenseEvidenceRejected):
            license_evidence.validate_license_evidence(_evidence(artifact_set), artifact_set)
    finally:
        sys.settrace(None)


def test_converts_top_level_key_deletion_during_snapshot_to_domain_error(tmp_path):
    artifact_set = _artifact_set(tmp_path)
    tracer = _mutate_on_snapshot_line("entries = value", lambda: artifact_set.pop("entries"))
    sys.settrace(tracer)
    try:
        with pytest.raises(license_evidence.LicenseEvidenceRejected):
            license_evidence.validate_license_evidence(_evidence(artifact_set), artifact_set)
    finally:
        sys.settrace(None)


def test_uses_captured_top_level_values_when_keys_disappear_before_return(tmp_path):
    artifact_set = _artifact_set(tmp_path)
    evidence = _evidence(artifact_set)
    tracer = _mutate_on_snapshot_line(
        "copied_entries.append", lambda: (artifact_set.pop("schema_version"), artifact_set.pop("domain"))
    )
    sys.settrace(tracer)
    try:
        assert license_evidence.validate_license_evidence(evidence, artifact_set).state == "PENDING_VERIFICATION"
        assert "schema_version" not in artifact_set and "domain" not in artifact_set
    finally:
        sys.settrace(None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rights_kind", "ownership"),
        ("rights_kind", []),
        ("source_verified_by", "agent:fernando"),
        ("source_verified_at", "2026-10-22T12:00:00"),
        ("verification_receipt", "receipt:license-evidence.logo-001"),
        ("source_uri", "not a uri"),
        ("expires_at", 0),
    ],
)
def test_rejects_invalid_evidence_shape_without_deciding_license_truth(tmp_path, field, value):
    artifact_set = _artifact_set(tmp_path)
    evidence = _evidence(artifact_set)
    evidence[field] = value

    with pytest.raises(license_evidence.LicenseEvidenceRejected):
        license_evidence.validate_license_evidence(evidence, artifact_set)


def test_rejects_unknown_field_boolean_version_and_oversize_text(tmp_path):
    artifact_set = _artifact_set(tmp_path)
    evidence = _evidence(artifact_set)
    evidence["unreviewed"] = True
    with pytest.raises(license_evidence.LicenseEvidenceRejected):
        license_evidence.validate_license_evidence(evidence, artifact_set)

    evidence = _evidence(artifact_set)
    evidence["schema_version"] = True
    with pytest.raises(license_evidence.LicenseEvidenceRejected):
        license_evidence.validate_license_evidence(evidence, artifact_set)

    evidence = _evidence(artifact_set)
    evidence["attribution"] = "x" * 2049
    with pytest.raises(license_evidence.LicenseEvidenceRejected):
        license_evidence.validate_license_evidence(evidence, artifact_set)


@pytest.mark.parametrize(
    ("source_uri", "timestamp"),
    [
        ("urn:example:animal:ferret:nose", "2026-02-28t23:59:59z"),
        ("custom+v1:opaque/%2F?x=one#fragment", "2026-10-22T12:00:00.123456789-00:00"),
        ("https://licenses.example.test/a%20record", "2026-10-22T12:00:00+23:59"),
    ],
)
def test_accepts_deterministic_absolute_ascii_uri_and_rfc3339_forms(tmp_path, source_uri, timestamp):
    artifact_set = _artifact_set(tmp_path)
    evidence = _evidence(artifact_set)
    evidence["source_uri"] = source_uri
    evidence["source_verified_at"] = timestamp

    assert license_evidence.validate_license_evidence(evidence, artifact_set).state == "PENDING_VERIFICATION"


@pytest.mark.parametrize(
    "source_uri",
    ["x:/", "x:/?q", "http://example.test:/p", "http://[::1]:/p", "https://[VF.example:future]/license"],
)
def test_accepts_rfc3986_empty_port_and_path_absolute_forms(tmp_path, source_uri):
    artifact_set = _artifact_set(tmp_path)
    evidence = _evidence(artifact_set)
    evidence["source_uri"] = source_uri

    assert license_evidence.validate_license_evidence(evidence, artifact_set).state == "PENDING_VERIFICATION"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_uri", "relative/path"),
        ("source_uri", "https://licenses.example.test/bad%2g"),
        ("source_uri", "https://licenses.example.test/a b"),
        ("source_uri", "https://licenses.example.test/a\\b"),
        ("source_uri", "https://licenses.example.test/caf\u00e9"),
        ("source_uri", "https://[::1"),
        ("source_uri", "https://[not-an-ip]"),
        ("source_uri", "https://[fe80::1%25eth0]/license"),
        ("source_uri", "x://host:bad-port"),
        ("source_uri", "x://user@@host"),
        ("source_uri", "x:a#b#c"),
        ("source_uri", "x:a[b"),
        ("source_verified_at", "2026-02-30T12:00:00Z"),
        ("source_verified_at", "2026-10-22T12:00:60Z"),
        ("source_verified_at", "2026-10-22T12:00:00+24:00"),
        ("source_verified_at", "2026-10-22T12:00:00.1234567890Z"),
        ("evidence_id", "lic\u00e9ncia.logo"),
        ("evidence_id", "a" * 257),
    ],
)
def test_rejects_noncanonical_uri_datetime_and_evidence_id_forms(tmp_path, field, value):
    artifact_set = _artifact_set(tmp_path)
    evidence = _evidence(artifact_set)
    evidence[field] = value

    with pytest.raises(license_evidence.LicenseEvidenceRejected):
        license_evidence.validate_license_evidence(evidence, artifact_set)


@pytest.mark.parametrize("case", _FORMAT_CASES, ids=lambda case: case["id"])
def test_matches_the_shared_cv_format_vectors(tmp_path, case):
    assert hashlib.sha256(_FORMAT_FIXTURE.read_bytes()).hexdigest() == _FORMAT_FIXTURE_SHA256
    artifact_set = _artifact_set(tmp_path)
    entry = artifact_set["entries"][0]
    evidence = {
        **_FORMAT_VECTORS["base_evidence"],
        "artifact_digest": artifact_set["root_digest"],
        "asset_path": entry["path"],
        "asset_digest": entry["digest"],
    }
    evidence.update(case["patch"])

    if case["valid"]:
        assert license_evidence.validate_license_evidence(evidence, artifact_set).state == "PENDING_VERIFICATION"
    else:
        with pytest.raises(license_evidence.LicenseEvidenceRejected):
            license_evidence.validate_license_evidence(evidence, artifact_set)
