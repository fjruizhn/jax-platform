"""Exact paired JAX structured-governance core loader.

The Platform adapter does not accept a compatible range.  It verifies both
module origin and every reviewed version before it handles an external DTO.
"""
from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path


class StructuredCoreUnavailable(RuntimeError):
    """The exact structured F2-C/F2-D core cannot safely be used."""


@dataclass(frozen=True)
class StructuredCore:
    structured_output: object
    lifecycle: object
    external_output: object
    response: object
    runtime_composition: object


_STRUCTURED_TUPLE = (
    "f2-c.structured-renderer.2",
    "f2-c.structured-output.2",
    "f2-d.structured.1",
)
_TEXT_TUPLE = (
    "f2-c.renderer.3",
    "f2-c.domain.5",
    frozenset({"f2-c.1"}),
)


def _configured_root() -> Path:
    configured = os.environ.get("JAX_REPO_PATH")
    if not configured or not os.path.isabs(configured):
        raise StructuredCoreUnavailable("JAX_REPO_PATH is unavailable for structured governance")
    root = Path(configured).resolve()
    if not all((root / "policy" / "governance" / name).is_file() for name in (
        "structured_output.py", "external_output.py", "runtime_output_composition.py",
    )):
        raise StructuredCoreUnavailable("configured JAX lacks F2-E structured output")
    return root


def _origin_is(root: Path, module: object) -> bool:
    source = getattr(module, "__file__", None)
    return isinstance(source, str) and Path(source).resolve().is_relative_to(root)


def load_structured_core() -> StructuredCore:
    """Load only the reviewed modules from the configured JAX checkout."""
    root = _configured_root()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    try:
        structured = importlib.import_module("policy.governance.structured_output")
        lifecycle = importlib.import_module("policy.governance.output_lifecycle")
        domain = importlib.import_module("policy.governance.governed_domain")
        external = importlib.import_module("policy.governance.external_output")
        runtime_composition = importlib.import_module("policy.governance.runtime_output_composition")
        response = importlib.import_module("policy.governance.response")
    except (ImportError, AttributeError) as exc:
        raise StructuredCoreUnavailable("structured governance modules cannot load") from exc
    if not all(_origin_is(root, module) for module in (structured, lifecycle, domain, external, runtime_composition, response)):
        raise StructuredCoreUnavailable("structured governance module is outside configured JAX")
    if getattr(runtime_composition, "GovernanceReceipt", None) is not getattr(response, "GovernanceReceipt", None):
        raise StructuredCoreUnavailable("paired runtime composition has inconsistent response module identity")
    structured_tuple = (
        getattr(structured, "STRUCTURED_RENDERER_API_VERSION", None),
        getattr(structured, "STRUCTURED_OUTPUT_SCHEMA_VERSION", None),
        getattr(lifecycle, "STRUCTURED_OUTPUT_LIFECYCLE_API_VERSION", None),
    )
    text_tuple = (
        getattr(domain, "GOVERNED_RENDERER_API_VERSION", None),
        getattr(domain, "GOVERNED_DOMAIN_SPEC_VERSION", None),
        getattr(domain, "GOVERNED_ENVELOPE_SCHEMA_VERSIONS", None),
    )
    if structured_tuple != _STRUCTURED_TUPLE or text_tuple != _TEXT_TUPLE:
        raise StructuredCoreUnavailable("configured structured governance compatibility is unsupported")
    if getattr(external, "EXTERNAL_OUTPUT_CHANNEL_REGISTRY_VERSION", None) != "f2-e.channels.1":
        raise StructuredCoreUnavailable("configured external channel registry is unsupported")
    return StructuredCore(structured_output=structured, lifecycle=lifecycle, external_output=external,
                          response=response, runtime_composition=runtime_composition)
