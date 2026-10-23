"""Pure boundary for agency invocations under ``invocation-context/v1``.

The context attaches typed evidence references and one rights-verdict reference
to the exact ``artifact-set/v1`` digest being discussed.  It deliberately has
no model transport, filesystem, network, ledger, approval, or release power.
Those operations require their own contracts; this module only makes an
invocation structurally safe to hand to a future broker.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .artifact_set import ArtifactSetRejected, validate_artifact_set, verify_artifact_directory

_SCHEMA_VERSION = 1
_MAX_REFERENCES = 256
_MAX_REFERENCE_BYTES = 512
_EVIDENCE_KIND = "license-evidence/v1"
_RIGHTS_KIND = "rights-verdict/v1"
ReferenceResolver = Callable[[dict[str, str | int]], Any]
EventSink = Callable[[dict[str, str]], Any]


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


def _resolved_reference(value: Any, resolver: ReferenceResolver, *, kind: str, digest: str) -> dict[str, str | int]:
    requested = _reference(value, kind=kind, digest=digest)
    if not callable(resolver):
        _reject("invocation reference resolver is required")
    try:
        resolved = resolver(copy.deepcopy(requested))
        resolved_reference = _reference(resolved, kind=kind, digest=digest)
    except (InvocationContextRejected, TypeError, ValueError):
        _reject("invocation reference is not resolved for this artifact set")
    if resolved_reference != requested:
        _reject("invocation reference is not resolved for this artifact set")
    return requested


def build_invocation_context(
    artifact_set: dict[str, Any],
    *,
    evidence_refs: list[dict[str, Any]],
    rights_verdict: dict[str, Any],
    evidence_resolver: ReferenceResolver,
    rights_verdict_resolver: ReferenceResolver,
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
    artifact_snapshot = copy.deepcopy(artifact_set)
    digest = artifact_snapshot["root_digest"]
    if not isinstance(evidence_refs, list) or not evidence_refs or len(evidence_refs) > _MAX_REFERENCES:
        _reject("evidence_refs must contain bounded typed references")
    evidence = [_resolved_reference(value, evidence_resolver, kind=_EVIDENCE_KIND, digest=digest) for value in evidence_refs]
    if len({entry["reference"] for entry in evidence}) != len(evidence):
        _reject("evidence_refs contains duplicate references")
    return {
        "schema_version": _SCHEMA_VERSION,
        "manifest_version": 1,
        "artifact_set": artifact_snapshot,
        "artifact_set_digest": digest,
        "evidence_refs": evidence,
        "rights_verdict": _resolved_reference(rights_verdict, rights_verdict_resolver, kind=_RIGHTS_KIND, digest=digest),
        "tools": [],
    }


def _validate_context(context: Any) -> dict[str, Any]:
    if not isinstance(context, dict) or set(context) != {
        "schema_version", "manifest_version", "artifact_set", "artifact_set_digest", "evidence_refs", "rights_verdict", "tools",
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
    try:
        validate_artifact_set(context.get("artifact_set"))
    except ArtifactSetRejected as error:
        raise InvocationContextRejected("invocation context artifact set is invalid") from error
    if context["artifact_set"]["root_digest"] != digest:
        _reject("invocation context artifact set does not match artifact_set_digest")
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


def _verify_workspace(context: dict[str, Any], workspace: str | Path) -> None:
    try:
        verify_artifact_directory(workspace, context["artifact_set"])
    except ArtifactSetRejected as error:
        raise InvocationContextRejected("workspace does not match invocation artifact set") from error


def request_tool(
    context: dict[str, Any], tool: str, *, workspace: str | Path, executor: Callable[[], Any], event_sink: EventSink,
) -> dict[str, str]:
    """Deny a real executor callback before it can perform any tool effect."""
    _validate_context(context)
    if not isinstance(tool, str) or not tool or len(tool.encode("utf-8")) > _MAX_REFERENCE_BYTES:
        _reject("requested tool is invalid")
    if not callable(executor) or not callable(event_sink):
        _reject("tool executor and event sink are required")
    _verify_workspace(context, workspace)
    event = {
        "type": "tool_denied",
        "tool": tool,
        "reason": "tools are disabled by invocation-context/v1",
        "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    event_sink(copy.deepcopy(event))
    _verify_workspace(context, workspace)
    return event


def delivery_gate(
    context: dict[str, Any], *, workspace: str | Path, writer: Callable[[], Any], event_sink: EventSink,
) -> dict[str, str]:
    """Block a real delivery callback until a distinct release decision exists."""
    _validate_context(context)
    if not callable(writer) or not callable(event_sink):
        _reject("delivery writer and event sink are required")
    _verify_workspace(context, workspace)
    event = {
        "state": "BLOCKED",
        "reason": "office release decision is unavailable",
        "artifact_set_digest": context["artifact_set_digest"],
    }
    event_sink(copy.deepcopy(event))
    _verify_workspace(context, workspace)
    return event


__all__ = (
    "InvocationContextRejected",
    "build_invocation_context",
    "delivery_gate",
    "request_tool",
)
