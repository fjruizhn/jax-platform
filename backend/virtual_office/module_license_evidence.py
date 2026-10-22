"""Pure license-evidence/v1 shape and artifact-attachment checks.

This module deliberately leaves authenticity, source freshness, rights review,
creative approval, and module publication to their owning authorities.
"""
from __future__ import annotations

import re
from ipaddress import IPv6Address
from dataclasses import dataclass
from datetime import date
from typing import Any

from .module_release_contract import ModuleReleaseContractRejected, validate_artifact_set


_EVIDENCE_FIELDS = {
    "schema_version",
    "evidence_id",
    "license_id",
    "rights_kind",
    "artifact_digest",
    "asset_path",
    "asset_digest",
    "source_uri",
    "source_digest",
    "source_verified_by",
    "source_verified_at",
    "verification_receipt",
    "attribution",
    "expires_at",
}
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
_ID = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_VERIFIER = re.compile(r"human:[a-z][a-z0-9-]{0,126}\Z")
_RECEIPT = re.compile(r"ledger:[a-z][a-z0-9._-]{0,255}\Z")
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")
_IPV_FUTURE = re.compile(r"[Vv][0-9A-Fa-f]+\.[A-Za-z0-9\-._~!$&'()*+,;=:]+\Z")
_DATE_TIME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})"
    r"(?:\.(\d{1,9}))?([Zz]|([+-])(\d{2}):(\d{2}))\Z"
)
_RIGHTS_KINDS = {"copyright", "trademark", "font", "asset", "third_party"}
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_SUB_DELIMS = frozenset("!$&'()*+,;=")
_PCHAR = _UNRESERVED | _SUB_DELIMS | frozenset(":@")
_ARTIFACT_DOMAIN = "axioma.office.module-artifact"
_MAX_ARTIFACT_ENTRIES = 10_000
_MAX_ARTIFACT_FILE_BYTES = 1 << 30
_MAX_ARTIFACT_TOTAL_BYTES = 2 << 30


class LicenseEvidenceRejected(ValueError):
    """The supplied data is not an attachable license-evidence/v1 record."""


@dataclass(frozen=True)
class LicenseEvidenceStatus:
    """A structural result that intentionally grants no release authority."""

    state: str
    artifact_digest: str
    evidence_id: str


def _reject(message: str) -> None:
    raise LicenseEvidenceRejected(message)


def _string(value: Any, field: str, minimum: int = 0, maximum: int | None = None) -> str:
    if type(value) is not str:
        _reject(f"{field} must be a string")
    if len(value) < minimum or (maximum is not None and len(value) > maximum):
        _reject(f"{field} exceeds its allowed length")
    return value


def _digest(value: Any, field: str) -> str:
    value = _string(value, field)
    if not _DIGEST.fullmatch(value):
        _reject(f"{field} must be a lowercase sha256 digest")
    return value


def _date_time(value: Any, field: str) -> str:
    value = _string(value, field, maximum=35)
    matched = _DATE_TIME.fullmatch(value)
    if matched is None:
        _reject(f"{field} must be an RFC3339 date-time")
    try:
        date(int(matched[1]), int(matched[2]), int(matched[3]))
    except ValueError as error:
        raise LicenseEvidenceRejected(f"{field} must be an RFC3339 date-time") from error
    if int(matched[4]) > 23 or int(matched[5]) > 59 or int(matched[6]) > 59:
        _reject(f"{field} must be an RFC3339 date-time")
    if matched[9] is not None and (int(matched[10]) > 23 or int(matched[11]) > 59):
        _reject(f"{field} must be an RFC3339 date-time")
    return value


def _rfc3986_component(value: str, allowed: frozenset[str]) -> bool:
    index = 0
    while index < len(value):
        character = value[index]
        if character == "%":
            if (
                index + 2 >= len(value)
                or value[index + 1] not in "0123456789abcdefABCDEF"
                or value[index + 2] not in "0123456789abcdefABCDEF"
            ):
                return False
            index += 3
        elif character in allowed:
            index += 1
        else:
            return False
    return True


def _rfc3986_ip_literal(value: str) -> bool:
    if _IPV_FUTURE.fullmatch(value):
        return True
    if "%" in value:
        return False
    try:
        IPv6Address(value)
    except ValueError:
        return False
    return True


def _rfc3986_authority(value: str) -> bool:
    if value.count("@") > 1:
        return False
    if "@" in value:
        userinfo, host_port = value.split("@")
        if not _rfc3986_component(userinfo, _UNRESERVED | _SUB_DELIMS | frozenset(":")):
            return False
    else:
        host_port = value
    if host_port.startswith("["):
        closing = host_port.find("]")
        if closing < 0 or not _rfc3986_ip_literal(host_port[1:closing]):
            return False
        remainder = host_port[closing + 1 :]
        return remainder in {"", ":"} or (
            remainder.startswith(":") and remainder[1:].isdigit()
        )
    if "[" in host_port or "]" in host_port:
        return False
    if ":" in host_port:
        host, port = host_port.rsplit(":", 1)
        if port and not port.isdigit():
            return False
    else:
        host = host_port
    return _rfc3986_component(host, _UNRESERVED | _SUB_DELIMS)


def _source_uri(value: Any) -> str:
    value = _string(value, "source_uri", maximum=2048)
    matched = _URI_SCHEME.match(value)
    if not value.isascii() or "\\" in value or matched is None:
        _reject("source_uri must be an absolute RFC3986 URI")
    remainder = value[matched.end() :]
    if remainder.count("#") > 1:
        _reject("source_uri must be an absolute RFC3986 URI")
    before_fragment, has_fragment, fragment = remainder.partition("#")
    if has_fragment and not _rfc3986_component(fragment, _PCHAR | frozenset("/?")):
        _reject("source_uri must be an absolute RFC3986 URI")
    hier_part, has_query, query = before_fragment.partition("?")
    if has_query and not _rfc3986_component(query, _PCHAR | frozenset("/?")):
        _reject("source_uri must be an absolute RFC3986 URI")
    if hier_part.startswith("//"):
        authority_and_path = hier_part[2:]
        authority, separator, path_tail = authority_and_path.partition("/")
        if not _rfc3986_authority(authority) or (
            separator and not _rfc3986_component(path_tail, _PCHAR | frozenset("/"))
        ):
            _reject("source_uri must be an absolute RFC3986 URI")
    elif not _rfc3986_component(hier_part, _PCHAR | frozenset("/")):
        _reject("source_uri must be an absolute RFC3986 URI")
    return value


def _artifact_snapshot(value: Any) -> dict[str, Any]:
    """Copy only bounded JSON-shaped artifact fields before contract validation."""

    if type(value) is not dict or len(value) != 4:
        _reject("artifact_set must be an artifact-set/v1 object")
    try:
        schema_version = value["schema_version"]
        domain = value["domain"]
        entries = value["entries"]
        root_digest = value["root_digest"]
    except KeyError as error:
        raise LicenseEvidenceRejected("artifact_set changed during snapshot") from error
    if type(entries) is not list:
        _reject("artifact_set has invalid entry count")
    entries_view = entries[: _MAX_ARTIFACT_ENTRIES + 1]
    if len(entries_view) > _MAX_ARTIFACT_ENTRIES:
        _reject("artifact_set has invalid entry count")
    if type(schema_version) is not int or type(domain) is not str or domain != _ARTIFACT_DOMAIN:
        _reject("artifact_set has invalid top-level field types")
    if type(root_digest) is not str or len(root_digest) > 71:
        _reject("artifact_set has an oversized root digest")
    total = 0
    copied_entries: list[dict[str, Any]] = []
    for entry in entries_view:
        if type(entry) is not dict or len(entry) != 4:
            _reject("artifact_set has an invalid entry")
        try:
            path = entry["path"]
            size = entry["size"]
            mode = entry["mode"]
            digest = entry["digest"]
        except KeyError as error:
            raise LicenseEvidenceRejected("artifact_set changed during snapshot") from error
        if (
            type(path) is not str
            or len(path) > 1024
            or type(size) is not int
            or isinstance(size, bool)
            or not 0 <= size <= _MAX_ARTIFACT_FILE_BYTES
            or type(mode) is not str
            or len(mode) > 4
            or type(digest) is not str
            or len(digest) > 71
        ):
            _reject("artifact_set has an invalid or oversized entry")
        total += size
        if total > _MAX_ARTIFACT_TOTAL_BYTES:
            _reject("artifact_set exceeds its byte limit")
        copied_entries.append({"path": path, "size": size, "mode": mode, "digest": digest})
    return {
        "schema_version": value["schema_version"],
        "domain": value["domain"],
        "entries": copied_entries,
        "root_digest": root_digest,
    }


def validate_license_evidence(
    evidence: Any, artifact_set: Any
) -> LicenseEvidenceStatus:
    """Validate shape and exact inventory attachment, returning no approval.

    A ``PENDING_VERIFICATION`` result only means the record is structurally
    suitable for a later authority-owned verification.
    """

    artifact_snapshot = _artifact_snapshot(artifact_set)
    try:
        validate_artifact_set(artifact_snapshot)
    except ModuleReleaseContractRejected as error:
        raise LicenseEvidenceRejected("artifact_set is invalid") from error
    root_digest = artifact_snapshot["root_digest"]
    inventory = frozenset(
        (entry["path"], entry["digest"]) for entry in artifact_snapshot["entries"]
    )
    if type(evidence) is not dict or set(evidence) != _EVIDENCE_FIELDS:
        _reject("evidence must be a license-evidence/v1 object without unknown fields")
    if type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1:
        _reject("schema_version must be 1")

    evidence_id = _string(evidence["evidence_id"], "evidence_id", minimum=1, maximum=256)
    if not evidence_id.isascii() or not _ID.fullmatch(evidence_id):
        _reject("evidence_id is invalid")
    _string(evidence["license_id"], "license_id", minimum=1, maximum=256)
    if type(evidence["rights_kind"]) is not str or evidence["rights_kind"] not in _RIGHTS_KINDS:
        _reject("rights_kind is invalid")
    artifact_digest = _digest(evidence["artifact_digest"], "artifact_digest")
    asset_path = _string(evidence["asset_path"], "asset_path", minimum=1, maximum=1024)
    asset_digest = _digest(evidence["asset_digest"], "asset_digest")
    _source_uri(evidence["source_uri"])
    _digest(evidence["source_digest"], "source_digest")
    verifier = _string(evidence["source_verified_by"], "source_verified_by")
    if not _VERIFIER.fullmatch(verifier):
        _reject("source_verified_by is invalid")
    _date_time(evidence["source_verified_at"], "source_verified_at")
    receipt = _string(evidence["verification_receipt"], "verification_receipt")
    if not _RECEIPT.fullmatch(receipt):
        _reject("verification_receipt is invalid")
    _string(evidence["attribution"], "attribution", minimum=1, maximum=2048)
    if evidence["expires_at"] is not None:
        _date_time(evidence["expires_at"], "expires_at")

    if artifact_digest != root_digest:
        _reject("artifact_digest does not bind the supplied artifact_set")
    if (asset_path, asset_digest) not in inventory:
        _reject("asset_path and asset_digest do not bind an artifact inventory entry")
    return LicenseEvidenceStatus(
        state="PENDING_VERIFICATION",
        artifact_digest=artifact_digest,
        evidence_id=evidence_id,
    )
