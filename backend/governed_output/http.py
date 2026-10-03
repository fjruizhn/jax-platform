"""FastAPI endpoint boundary for exact F2-C/F2-D HTTP JSON output.

The endpoint and its dependant call are replaced before FastAPI's response
serializer.  The only ASGI body sent is the prepared F2-D wire payload.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from auth.models import AuthUser
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .adapter import PlatformGovernedExternalOutputAdapter, PreparedStructuredOutput
from .channels import PlatformDeliveryKind, channel_binding
from .composition import _COMPOSITION_COMPONENT, _environment, runtime_output_composer
from .core import load_structured_core
from .registry import (
    HTTPOutputClassification,
    HTTPRouteContract,
    PLATFORM_HTTP_ROUTE_CONTRACTS,
    route_contract_index,
)
from .transport import StructuredOutputOutboxRepository, commit_structured_wire


_FALLBACK = {"detail": {"code": "OUTPUT_LIFECYCLE_UNAVAILABLE"}}
_EXPOSED_HEADERS = (
    "X-Axioma-Governed-Output", "X-Axioma-Structured-Renderer", "X-Axioma-Structured-Schema",
    "X-Axioma-Domain-Spec", "X-Axioma-Response-Id", "X-Axioma-Body-SHA256",
    "X-Axioma-Provenance-Profile", "X-Axioma-Provenance-Profile-Digest",
    "X-Axioma-Origin-Manifest-SHA256",
)


class GovernedHTTPUnavailable(RuntimeError):
    pass


def _principal(arguments: tuple[object, ...], keywords: dict[str, object]) -> AuthUser | None:
    values = (*arguments, *keywords.values())
    users = [value for value in values if isinstance(value, AuthUser)]
    if len(users) > 1 and any(value != users[0] for value in users[1:]):
        raise GovernedHTTPUnavailable("route received conflicting authenticated principals")
    return users[0] if users else None


def _request(arguments: tuple[object, ...], keywords: dict[str, object]) -> Request | None:
    return next((value for value in (*arguments, *keywords.values()) if isinstance(value, Request)), None)


def _scope(*, user: AuthUser | None, request: Request | None):
    """Mint scope from DI principal, or fixed server request metadata.

    Authentication routes have no authenticated principal by design.  Their
    scope names that absence; it never accepts a client header as identity.
    """
    core = load_structured_core()
    if user is not None:
        tenant_id, subject_id, audience = str(user.tenant_id), str(user.user_id), f"user:{user.user_id}"
    else:
        tenant_id, subject_id, audience = "server-request", "user-unavailable", "unauthenticated-request"
    return core.response.ResponseScope(
        environment=_environment(), tenant_id=tenant_id, project_id=None,
        subject_id=subject_id, actor_id="service:jax-platform", audience=audience,
        component_id=_COMPOSITION_COMPONENT, request_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
    )


def _tool_data(value: object) -> object:
    if isinstance(value, Response) and value.media_type == "application/json":
        try:
            return json.loads(value.body)
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise GovernedHTTPUnavailable("native JSON response has no JSON DTO") from exc
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, tuple, list)):
        return value
    raise GovernedHTTPUnavailable("HTTP endpoint result is not a native structured DTO")


def _is_native_json_response(value: object) -> bool:
    return isinstance(value, Response) and value.media_type == "application/json"


def _layout(*, contract: HTTPRouteContract, composed: object, core: object):
    binding = channel_binding(PlatformDeliveryKind.HTTP_JSON)
    channel = getattr(core.external_output.ExternalOutputChannelId, binding.core_channel_name)
    structured = core.structured_output
    layout = structured.StructuredLayout(
        layout_id=contract.route_id + ".layout.1", layout_version="1", channel_id=channel.value,
        root_block_index=0, slots=composed.slots,
        origins=tuple(structured.OriginBinding(pointer, structured.OutputOrigin(origin), source)
                      for pointer, origin, source in contract.origins),
        presentation_maps={"engine-status-bool-v1": {"alive": True, "down": False}},
        fallback=_FALLBACK, number_bindings=composed.number_bindings,
        number_patterns=composed.number_patterns,
        number_binding_manifest=composed.number_binding_manifest,
    )
    return layout, structured.StructuredLayoutRegistry({layout.layout_id: layout}), channel


async def prepare_http_output(*, contract: HTTPRouteContract, value: object,
                              user: AuthUser | None, request: Request | None) -> PreparedStructuredOutput:
    """Compose DTO data into canonical bytes before FastAPI can serialize it."""
    core = load_structured_core()
    scope = _scope(user=user, request=request)
    data = _tool_data(value)
    runtime = core.runtime_composition
    requests = tuple(runtime.RuntimeClaimRequest(
        claim.predicate,
        {name: (_json_pointer_value(data, pointer) if pointer else constant)
         for name, pointer in claim.argument_pointers.items()}
        | dict(claim.server_constants),
        claim.json_pointer, presentation_map_id=claim.presentation_map_id,
    ) for claim in contract.runtime_claims)
    composed = await runtime_output_composer().compose(
        scope=scope, response_id=str(uuid.uuid4()), producer=_COMPOSITION_COMPONENT,
        tool_data=data, requests=requests,
        number_binding_contract_id=contract.number_binding_contract_id,
    )
    layout, registry, channel = _layout(contract=contract, composed=composed, core=core)
    submission = core.external_output.GovernedExternalOutputSubmission(
        composed.envelope, composed.envelope.response_scope, core.structured_output.OutputOrigin.TOOL,
        channel, output_reference="http:" + contract.route_id,
    )
    key_material = f"{contract.route_id}:{scope.request_id}:{composed.envelope.response_id}".encode("utf-8")
    return PlatformGovernedExternalOutputAdapter(core).prepare(
        submission, layout=layout, context=composed.context, layout_registry=registry,
        delivery=PlatformDeliveryKind.HTTP_JSON,
        idempotency_key=hashlib.sha256(key_material).hexdigest(), http_status=contract.status_code,
    )


def _json_pointer_value(value: object, pointer: str) -> object:
    current = value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise GovernedHTTPUnavailable("route claim pointer is not server-owned")
    for part in pointer[1:].split("/"):
        if not isinstance(current, dict) or part not in current:
            raise GovernedHTTPUnavailable("route DTO cannot satisfy accredited claim")
        current = current[part]
    if not isinstance(current, str) or not current:
        raise GovernedHTTPUnavailable("route claim argument must be canonical text")
    return current


class GovernedHTTPResponse(Response):
    """An ASGI response whose terminal body is committed only after send."""

    media_type = "application/json"

    def __init__(self, prepared: PreparedStructuredOutput, authorization: object, repository: object,
                 headers: list[tuple[bytes, bytes]] = ()):
        self._authorization = authorization
        self._repository = repository
        super().__init__(content=prepared.canonical_bytes, status_code=prepared.transport_unit.metadata.http_status,
                         media_type=self.media_type)
        # Cookies and server-selected non-content headers are response
        # transport metadata, never candidate fields.  Replace only headers
        # whose value is derived from the immutable F2-D bytes.
        self.raw_headers = [item for item in self.raw_headers if item[0] not in {b"content-length", b"content-type"}]
        self.raw_headers.extend((name, value) for name, value in headers
                                if name not in {b"content-length", b"content-type", b"cache-control"}
                                and not name.lower().startswith(b"x-axioma-"))
        unit = prepared.transport_unit
        rendered = unit.rendered
        governed_headers = (
            (b"x-axioma-governed-output", b"f2-e.output.1"),
            (b"x-axioma-structured-renderer", rendered.renderer_api_version.encode("ascii")),
            (b"x-axioma-structured-schema", rendered.structured_schema_version.encode("ascii")),
            (b"x-axioma-domain-spec", rendered.domain_spec_version.encode("ascii")),
            (b"x-axioma-response-id", rendered.response_id.encode("ascii")),
            (b"x-axioma-body-sha256", unit.wire_payload_digest.encode("ascii")),
            (b"x-axioma-provenance-profile", unit.layout.layout_id.encode("ascii")),
            (b"x-axioma-provenance-profile-digest", rendered.layout_digest.encode("ascii")),
            (b"x-axioma-origin-manifest-sha256", rendered.origin_manifest_digest.encode("ascii")),
        )
        self.raw_headers.extend([
            (b"content-length", str(len(self.body)).encode("latin-1")),
            (b"content-type", b"application/json"),
            (b"cache-control", b"no-transform"),
        ])
        self.raw_headers.extend(governed_headers)

    async def __call__(self, scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})

        async def send_wire(payload: bytes) -> None:
            if payload != self.body:
                raise GovernedHTTPUnavailable("F2-D payload differs from HTTP body")
            await send({"type": "http.response.body", "body": payload, "more_body": False})

        await commit_structured_wire(
            authorization=self._authorization, repository=self._repository, send_wire=send_wire,
        )
        if self.background is not None:
            await self.background()


def _fallback() -> JSONResponse:
    return JSONResponse(status_code=503, content=_FALLBACK)


def install_governed_http_boundary(
    app: FastAPI, *, contracts: tuple[HTTPRouteContract, ...] | None = None,
    repository_factory: Callable[[], StructuredOutputOutboxRepository] = StructuredOutputOutboxRepository,
) -> None:
    """Install only the closed table; all other dynamic HTTP routes fail closed."""
    table = route_contract_index(PLATFORM_HTTP_ROUTE_CONTRACTS if contracts is None else contracts)
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or ():
            contract = table.get((method.upper(), route.path))
            if contract is None:
                contract = HTTPRouteContract(
                    route_id="unregistered." + hashlib.sha256((method + ":" + route.path).encode()).hexdigest()[:16],
                    method=method, path=route.path, classification=HTTPOutputClassification.GOVERNED_TOOL_DATA,
                )
                registered = False
            else:
                registered = True
            if contract.classification is not HTTPOutputClassification.GOVERNED_TOOL_DATA and registered:
                continue
            original = route.dependant.call

            async def governed_call(*args: Any, __original=original, __contract=contract,
                                    __registered=registered, **kwargs: Any):
                try:
                    result = __original(*args, **kwargs)
                    if inspect.isawaitable(result):
                        result = await result
                except HTTPException as exc:
                    # Status is a server-controlled protocol fact.  Dynamic
                    # detail is not a static exception contract and must not
                    # cross this boundary raw.
                    return JSONResponse(status_code=exc.status_code, content=_FALLBACK)
                except Exception:
                    # A dynamic handler error may contain provider or request
                    # detail.  It has no independent static-output contract.
                    return _fallback()
                if not __registered or (isinstance(result, Response) and not _is_native_json_response(result)):
                    return _fallback()
                try:
                    prepared = await prepare_http_output(
                        contract=__contract, value=result,
                        user=_principal(args, kwargs), request=_request(args, kwargs),
                    )
                    repository = repository_factory()
                    authorization = await repository.prepare(prepared.transport_unit)
                    headers = result.raw_headers if _is_native_json_response(result) else ()
                    return GovernedHTTPResponse(prepared, authorization, repository, headers)
                except Exception:
                    return _fallback()

            route.dependant.call = governed_call
            route.endpoint = governed_call
