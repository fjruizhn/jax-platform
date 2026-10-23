from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load(name: str):
    package = "virtual_office"
    if package not in sys.modules:
        root = Path(__file__).parents[1] / package
        shim = ModuleType(package)
        shim.__path__ = [str(root)]
        sys.modules[package] = shim
    module_name = f"{package}.{name}"
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).parents[1] / package / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


context = _load("invocation_context")
artifact_set = _load("artifact_set")


def _artifact(tmp_path):
    asset = tmp_path / "asset.txt"
    asset.write_text("approved source", encoding="utf-8")
    asset.chmod(0o644)
    return artifact_set.build_artifact_set(tmp_path)


def _evidence(digest: str):
    return {
        "schema_version": 1,
        "kind": "license-evidence/v1",
        "reference": "license:logo-001",
        "artifact_set_digest": digest,
    }


def _rights(digest: str):
    return {
        "schema_version": 1,
        "kind": "rights-verdict/v1",
        "reference": "rights:critic-001",
        "artifact_set_digest": digest,
    }


def _context(tmp_path):
    artifact = _artifact(tmp_path)
    return context.build_invocation_context(
        artifact,
        evidence_refs=[_evidence(artifact["root_digest"])],
        rights_verdict=_rights(artifact["root_digest"]),
    )


def test_builds_frozen_context_bound_to_one_artifact_digest(tmp_path):
    built = _context(tmp_path)

    assert built["schema_version"] == 1
    assert built["artifact_set_digest"] == built["evidence_refs"][0]["artifact_set_digest"]
    assert built["artifact_set_digest"] == built["rights_verdict"]["artifact_set_digest"]
    assert built["tools"] == []
    assert built["manifest_version"] == 1


def test_rejects_evidence_or_rights_bound_to_another_artifact(tmp_path):
    artifact = _artifact(tmp_path)
    other = "sha256:" + "1" * 64

    with pytest.raises(context.InvocationContextRejected, match="artifact_set_digest"):
        context.build_invocation_context(
            artifact,
            evidence_refs=[_evidence(other)],
            rights_verdict=_rights(artifact["root_digest"]),
        )
    with pytest.raises(context.InvocationContextRejected, match="artifact_set_digest"):
        context.build_invocation_context(
            artifact,
            evidence_refs=[_evidence(artifact["root_digest"])],
            rights_verdict=_rights(other),
        )


def test_rejects_manifest_v2_or_any_declared_tool(tmp_path):
    artifact = _artifact(tmp_path)

    with pytest.raises(context.InvocationContextRejected, match="manifest v1"):
        context.build_invocation_context(
            artifact,
            evidence_refs=[_evidence(artifact["root_digest"])],
            rights_verdict=_rights(artifact["root_digest"]),
            manifest_version=2,
        )
    with pytest.raises(context.InvocationContextRejected, match="tools"):
        context.build_invocation_context(
            artifact,
            evidence_refs=[_evidence(artifact["root_digest"])],
            rights_verdict=_rights(artifact["root_digest"]),
            tools=["Bash"],
        )


@pytest.mark.parametrize("tool", ["Bash", "WebSearch", "Write"])
def test_denies_every_tool_before_execution_and_emits_observable_event(tmp_path, tool):
    built = _context(tmp_path)
    called = False

    def forbidden_executor():
        nonlocal called
        called = True

    event = context.request_tool(built, tool, executor=forbidden_executor)

    assert event["type"] == "tool_denied"
    assert event["tool"] == tool
    assert event["reason"] == "tools are disabled by invocation-context/v1"
    assert isinstance(event["occurred_at"], str)
    assert called is False


def test_delivery_gate_never_attributes_or_performs_a_release(tmp_path):
    built = _context(tmp_path)
    before = artifact_set.build_artifact_set(tmp_path)["root_digest"]
    result = context.delivery_gate(built)

    assert result["state"] == "BLOCKED"
    assert result["reason"] == "office release decision is unavailable"
    assert "authorized_by" not in result
    assert "published_by" not in result
    assert artifact_set.build_artifact_set(tmp_path)["root_digest"] == before


def test_rejects_model_invented_license_reference(tmp_path):
    artifact = _artifact(tmp_path)
    invented = _evidence(artifact["root_digest"])
    invented["source"] = "model"

    with pytest.raises(context.InvocationContextRejected, match="unknown fields"):
        context.build_invocation_context(
            artifact,
            evidence_refs=[invented],
            rights_verdict=_rights(artifact["root_digest"]),
        )
