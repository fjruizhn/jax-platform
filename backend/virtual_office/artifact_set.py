"""Canonical package inventory for ``artifact-set/v1``.

An artifact set identifies client bytes only.  Signatures, human approvals,
rights verdicts, and a release decision intentionally belong to later records
and cannot be added to this inventory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .module_release_contract import (
    ModuleReleaseContractRejected,
    build_artifact_set as _build_artifact_set,
    validate_artifact_set as _validate_artifact_set,
    verify_artifact_directory as _verify_artifact_directory,
)

ARTIFACT_SET_V1_DOMAIN = "axioma.office.module-artifact"
ArtifactSetRejected = ModuleReleaseContractRejected


def build_artifact_set(root: str | Path) -> dict[str, Any]:
    """Return the canonical v1 inventory and digest for a client artifact tree."""
    return _build_artifact_set(root)


def validate_artifact_set(artifact_set: dict[str, Any]) -> None:
    """Reject inventories that are not exactly ``artifact-set/v1``."""
    _validate_artifact_set(artifact_set)


def verify_artifact_directory(root: str | Path, artifact_set: dict[str, Any]) -> None:
    """Reject a directory whose bytes or metadata drift from its inventory."""
    _verify_artifact_directory(root, artifact_set)


__all__ = (
    "ARTIFACT_SET_V1_DOMAIN",
    "ArtifactSetRejected",
    "build_artifact_set",
    "validate_artifact_set",
    "verify_artifact_directory",
)
