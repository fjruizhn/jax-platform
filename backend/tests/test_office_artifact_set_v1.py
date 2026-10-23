from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

_PACKAGE = types.ModuleType("virtual_office")
_PACKAGE.__path__ = [str(Path(__file__).parents[1] / "virtual_office")]
sys.modules.setdefault("virtual_office", _PACKAGE)
module = importlib.import_module("virtual_office.artifact_set")


def _file(root: Path, name: str, content: bytes, mode: int = 0o644) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(mode)


def test_artifact_set_v1_binds_inventory_metadata_but_not_release_authorities(tmp_path):
    _file(tmp_path, "bin/start", b"run", 0o755)
    _file(tmp_path, "assets/logo.svg", b"<svg/>")

    artifact = module.build_artifact_set(tmp_path)

    assert artifact["schema_version"] == 1
    assert artifact["domain"] == module.ARTIFACT_SET_V1_DOMAIN
    assert [entry["path"] for entry in artifact["entries"]] == ["assets/logo.svg", "bin/start"]
    assert artifact["entries"][1]["mode"] == "0755"
    assert artifact["root_digest"].startswith("sha256:")
    assert {"signature", "approval", "rights_verdict", "release_decision"}.isdisjoint(artifact)
    module.validate_artifact_set(artifact)


@pytest.mark.parametrize("name", ["e\u0301.txt", "a\\b.txt"])
def test_artifact_set_v1_rejects_noncanonical_paths(tmp_path, name):
    _file(tmp_path, name, b"x")

    with pytest.raises(module.ArtifactSetRejected):
        module.build_artifact_set(tmp_path)


def test_artifact_set_v1_rejects_inventory_with_release_authority_fields(tmp_path):
    _file(tmp_path, "asset.txt", b"x")
    artifact = module.build_artifact_set(tmp_path)
    artifact["approval"] = {"kind": "human-approval"}

    with pytest.raises(module.ArtifactSetRejected):
        module.validate_artifact_set(artifact)
