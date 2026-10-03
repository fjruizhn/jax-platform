"""Closed, versioned HTTP output contracts owned by Platform routes.

The table is deliberately keyed by the effective FastAPI method/path pair.
It is not derived from a request, response model, header, or caller input.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping


HTTP_ROUTE_CONTRACT_REGISTRY_VERSION = "platform-http-output-contracts.1"
_FACET_KEYS = ("jax_local", "jekyll", "hyde", "hipatia", "thot", "kimi", "ada", "jacobs")


class HTTPOutputClassification(str, Enum):
    GOVERNED_TOOL_DATA = "GOVERNED_TOOL_DATA"
    STATIC_SAFE = "STATIC_SAFE"
    ADAPTED = "ADAPTED"


@dataclass(frozen=True)
class RuntimeClaimContract:
    """One accredited current-state slot, with no caller-selected resolver."""
    predicate: str
    json_pointer: str
    argument_pointers: Mapping[str, str]
    server_constants: Mapping[str, object]
    presentation_map_id: str | None = None


@dataclass(frozen=True)
class HTTPRouteContract:
    route_id: str
    method: str
    path: str
    classification: HTTPOutputClassification
    status_code: int = 200
    number_binding_contract_id: str | None = None
    origins: tuple[tuple[str, str, str], ...] = (("", "TOOL", "platform:http-route"),)
    runtime_claims: tuple[RuntimeClaimContract, ...] = ()

    def __post_init__(self) -> None:
        if (not isinstance(self.route_id, str) or not self.route_id
                or not isinstance(self.method, str) or not self.method
                or not isinstance(self.path, str) or not self.path.startswith("/")):
            raise ValueError("HTTP output route contract must be server-owned and complete")
        if not isinstance(self.classification, HTTPOutputClassification):
            raise TypeError("HTTP output classification must be closed")
        if not isinstance(self.status_code, int) or not 100 <= self.status_code <= 599:
            raise ValueError("HTTP output status must be valid")
        if self.number_binding_contract_id is not None and not isinstance(self.number_binding_contract_id, str):
            raise TypeError("HTTP numeric binding id must be server-owned")
        if (not isinstance(self.origins, tuple)
                or not all(isinstance(item, tuple) and len(item) == 3 for item in self.origins)
                or not isinstance(self.runtime_claims, tuple)
                or not all(isinstance(item, RuntimeClaimContract) for item in self.runtime_claims)):
            raise TypeError("HTTP output provenance and claims must be immutable server contracts")


def route_contract_index(contracts: tuple[HTTPRouteContract, ...]) -> Mapping[tuple[str, str], HTTPRouteContract]:
    """Validate a static route table once, rejecting duplicate effective routes."""
    if not isinstance(contracts, tuple) or not all(isinstance(item, HTTPRouteContract) for item in contracts):
        raise TypeError("HTTP output route contracts must be an immutable tuple")
    indexed = {(item.method.upper(), item.path): item for item in contracts}
    if len(indexed) != len(contracts) or len({item.route_id for item in contracts}) != len(contracts):
        raise ValueError("HTTP output route contracts must be unique")
    return MappingProxyType(indexed)


# Chat already owns its F2-C/F2-D adapter and must never be wrapped a second
# time.  These fixed responses have no dynamic model/tool output to govern.
PLATFORM_HTTP_ROUTE_CONTRACTS = (
    HTTPRouteContract(
        "platform-health.v1", "GET", "/api/health", HTTPOutputClassification.GOVERNED_TOOL_DATA,
        origins=(("", "SYSTEM", "platform:health-probe"),),
        runtime_claims=(RuntimeClaimContract(
            "ENGINE_STATUS", "/las_manos", {"status": "/las_manos"}, {"name": "las_manos"},
        ),),
    ),
    HTTPRouteContract("platform-chat.v1", "POST", "/api/chat", HTTPOutputClassification.ADAPTED),
    HTTPRouteContract("platform-auth-logout.v1", "POST", "/api/auth/logout", HTTPOutputClassification.STATIC_SAFE),
    HTTPRouteContract("platform-events.v1", "GET", "/api/events", HTTPOutputClassification.ADAPTED),
    HTTPRouteContract(
        "platform-image-generate.v1", "POST", "/api/image/generate", HTTPOutputClassification.GOVERNED_TOOL_DATA,
        origins=(("/url", "TOOL", "image-provider"), ("/revised_prompt", "ASSISTANT/MODEL", "image-provider")),
    ),
    HTTPRouteContract(
        "platform-facets.v1", "GET", "/api/facets", HTTPOutputClassification.GOVERNED_TOOL_DATA,
        origins=(("", "SYSTEM", "platform:facet-runtime"),),
        runtime_claims=tuple(RuntimeClaimContract(
            "FACET_RUNTIME_STATUS", f"/facets/{name}/status",
            {"name": f"/facets/{name}/name", "status": f"/facets/{name}/status"}, {},
        ) for name in _FACET_KEYS),
    ),
    HTTPRouteContract(
        "platform-pipeline.v1", "GET", "/api/pipelines/{pipeline_id}", HTTPOutputClassification.GOVERNED_TOOL_DATA,
        runtime_claims=(RuntimeClaimContract(
            "PIPELINE_STATUS", "/status", {"pipeline_id": "/pipeline_id", "status": "/status"}, {},
        ),),
    ),
    HTTPRouteContract(
        "platform.get.api.pipelines.pipeline_id.results.v1", "GET", "/api/pipelines/{pipeline_id}/results", HTTPOutputClassification.GOVERNED_TOOL_DATA,
        number_binding_contract_id="platform-http.pipeline-results.v1",
        origins=(("", "TOOL", "jacobs:pipeline-result"),),
    ),
)


def contracts_for_routes(routes: Iterable[object]) -> tuple[HTTPRouteContract, ...]:
    """Freeze the effective server route graph into the startup contract table.

    FastAPI has already applied router prefixes when this runs.  Request data
    cannot add a route, method, classification, origin, or numeric contract.
    The three exceptional routes above stay explicit; every other existing
    application route is a typed-untrusted DTO boundary.
    """
    from fastapi.routing import APIRoute

    explicit = route_contract_index(PLATFORM_HTTP_ROUTE_CONTRACTS)
    result = list(PLATFORM_HTTP_ROUTE_CONTRACTS)
    seen = set(explicit)
    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or ():
            key = (method.upper(), route.path)
            if key in seen:
                continue
            route_slug = route.path.strip("/").replace("/", ".").replace("{", "").replace("}", "") or "root"
            status_code = route.status_code or 200
            result.append(HTTPRouteContract(
                route_id=f"platform.{method.lower()}.{route_slug}.v1", method=method,
                path=route.path,
                classification=(HTTPOutputClassification.STATIC_SAFE if status_code == 204
                                else HTTPOutputClassification.GOVERNED_TOOL_DATA),
                status_code=status_code,
            ))
            seen.add(key)
    return tuple(result)
