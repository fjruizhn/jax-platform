"""Pure boundary for agency invocations under ``invocation-context/v1``.

The context attaches typed evidence references and one rights-verdict reference
to the exact ``artifact-set/v1`` digest being discussed.  It deliberately has
no model transport, filesystem, network, ledger, approval, or release power.
Those operations require their own contracts; this module only makes an
invocation structurally safe to hand to a future broker.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .artifact_set import ArtifactSetRejected, validate_artifact_set

_SCHEMA_VERSION = 1
_MAX_REFERENCES = 256
_MAX_REFERENCE_BYTES = 512
_EVIDENCE_KIND = "license-evidence/v1"
_RIGHTS_KIND = "rights-verdict/v1"


class InvocationContextRejected(ValueError):
    """The caller supplied data outside the frozen invocation contract."""


def _reject(message: str) -> None:
    raise InvocationContextRejected(message)


def _reference(value: Any, *, kind: str, digest: str) -> dict[str, str | int]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "kind", "reference", "artifact_set_digest"}:
        _reject("invocation reference has unknown fields or is incomplete")
    if value.get("schema_version") != _SCHEMA_VERSION or type(value["schema_version"]) is not int:
        _reject("invocation reference schema_version is invalid")
    if value.get("kind") != kind:
        _reject("invocation reference kind is invalid")
    reference = value.get("reference")
    if not isinstance(reference, str) or not reference or len(reference.encode("utf-8")) > _MAX_REFERENCE_BYTES:
        _reject("invocation reference is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in reference):
        _reject("invocation reference is invalid")
    if value.get("artifact_set_digest") != digest:
        _reject("invocation reference artifact_set_digest does not match artifact set")
    return {
        "schema_version": _SCHEMA_VERSION,
        "kind": kind,
        "reference": reference,
        "artifact_set_digest": digest,
    }


def build_invocation_context(
    artifact_set: dict[str, Any],
    *,
    evidence_refs: list[dict[str, Any]],
    rights_verdict: dict[str, Any],
    tools: list[str] | None = None,
    manifest_version: int = 1,
) -> dict[str, Any]:
    """Build a frozen, tool-free context bound to one client artifact digest."""
    if manifest_version != 1 or type(manifest_version) is not int:
        _reject("manifest v1 is the only supported manifest version")
    if tools not in (None, []):
        _reject("tools must be empty for invocation-context/v1")
    try:
        validate_artifact_set(artifact_set)
    except ArtifactSetRejected as error:
        raise InvocationContextRejected("artifact set is invalid") from error
    digest = artifact_set["root_digest"]
    if not isinstance(evidence_refs, list) or not evidence_refs or len(evidence_refs) > _MAX_REFERENCES:
        _reject("evidence_refs must contain bounded typed references")
    evidence = [_reference(value, kind=_EVIDENCE_KIND, digest=digest) for value in evidence_refs]
    if len({entry["reference"] for entry in evidence}) != len(evidence):
        _reject("evidence_refs contains duplicate references")
    return {
        "schema_version": _SCHEMA_VERSION,
        "manifest_version": 1,
        "artifact_set_digest": digest,
        "evidence_refs": evidence,
        "rights_verdict": _reference(rights_verdict, kind=_RIGHTS_KIND, digest=digest),
        "tools": [],
    }


def _validate_context(context: Any) -> dict[str, Any]:
    if not isinstance(context, dict) or set(context) != {
        "schema_version", "manifest_version", "artifact_set_digest", "evidence_refs", "rights_verdict", "tools",
    }:
        _reject("invocation context has unknown fields or is incomplete")
    digest = context.get("artifact_set_digest")
    if (
        context.get("schema_version") != _SCHEMA_VERSION
        or type(context["schema_version"]) is not int
        or context.get("manifest_version") != 1
        or type(context["manifest_version"]) is not int
        or not isinstance(digest, str)
        or len(digest) != 71
        or not digest.startswith("sha256:")
    ):
        _reject("invocation context version or artifact_set_digest is invalid")
    try:
        int(digest[7:], 16)
    except ValueError as error:
        raise InvocationContextRejected("invocation context artifact_set_digest is invalid") from error
    if context.get("tools") != []:
        _reject("tools must be empty for invocation-context/v1")
    evidence_refs = context.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs or len(evidence_refs) > _MAX_REFERENCES:
        _reject("evidence_refs must contain bounded typed references")
    evidence = [_reference(value, kind=_EVIDENCE_KIND, digest=digest) for value in evidence_refs]
    if len({entry["reference"] for entry in evidence}) != len(evidence):
        _reject("evidence_refs contains duplicate references")
    _reference(context.get("rights_verdict"), kind=_RIGHTS_KIND, digest=digest)
    return context


def request_tool(context: dict[str, Any], tool: str, *, executor: Callable[[], Any] | None = None) -> dict[str, str]:
    """Return a denial event before an optional executor can observe a request."""
    _validate_context(context)
    if not isinstance(tool, str) or not tool or len(tool.encode("utf-8")) > _MAX_REFERENCE_BYTES:
        _reject("requested tool is invalid")
    return {
        "type": "tool_denied",
        "tool": tool,
        "reason": "tools are disabled by invocation-context/v1",
        "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def delivery_gate(context: dict[str, Any]) -> dict[str, str]:
    """Block delivery until a distinct approval and release-decision contract exists."""
    _validate_context(context)
    return {
        "state": "BLOCKED",
        "reason": "office release decision is unavailable",
        "artifact_set_digest": context["artifact_set_digest"],
    }


__all__ = (
    "InvocationContextRejected",
    "build_invocation_context",
    "delivery_gate",
    "request_tool",
)
