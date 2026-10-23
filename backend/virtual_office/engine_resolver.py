"""Fail-closed resolution for the Office engine catalogue."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re

_CLASSIFICATION = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SUPPORTED_TRANSPORTS = frozenset({"http_openai_compat"})
_MAX_TOKEN_PARAMS = frozenset({"max_tokens", "max_completion_tokens"})


class OfficeEngineUnavailable(RuntimeError):
    """The requested Office engine has no safe currently-attested binding."""


@dataclass(frozen=True)
class ResolvedOfficeEngine:
    engine_key: str
    display_name_key: str
    model_ref: int
    provider_id: str
    provider_auth_type: str
    provider_api_key_transport: str
    model_id: str
    transport: str
    allowed_data_classifications: frozenset[str]
    max_tokens_param: str
    max_output_tokens: int
    credential_id: int
    credential_fingerprint: str
    engine_binding_fingerprint: str
    version: int


def _classifications(raw: object) -> frozenset[str]:
    if not isinstance(raw, str):
        raise OfficeEngineUnavailable("engine classifications are malformed")
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise OfficeEngineUnavailable("engine classifications are malformed") from error
    if (not isinstance(decoded, list) or not decoded
            or any(not isinstance(value, str) or not _CLASSIFICATION.fullmatch(value) for value in decoded)):
        raise OfficeEngineUnavailable("engine classifications are malformed")
    values = frozenset(decoded)
    if len(values) != len(decoded):
        raise OfficeEngineUnavailable("engine classifications are malformed")
    return values


def engine_binding_fingerprint(*, engine_key: str, model_ref: int, transport: str,
                               allowed_data_classifications: frozenset[str],
                               provider_fingerprint: str, model_fingerprint: str,
                               credential_id: int, credential_fingerprint: str, engine_enabled: int,
                               activation_epoch: int, version: int) -> str:
    fields = {"credential_id": credential_id, "credential_fingerprint": credential_fingerprint, "engine_enabled": engine_enabled,
              "activation_epoch": activation_epoch, "engine_key": engine_key,
              "allowed_data_classifications": sorted(allowed_data_classifications),
              "model_fingerprint": model_fingerprint, "model_ref": model_ref,
              "provider_fingerprint": provider_fingerprint, "transport": transport, "version": version}
    if (not isinstance(model_ref, int) or isinstance(model_ref, bool) or model_ref < 1
            or not isinstance(credential_id, int) or isinstance(credential_id, bool) or credential_id < 1
            or not isinstance(engine_enabled, int) or isinstance(engine_enabled, bool) or engine_enabled not in (0, 1)
            or not isinstance(activation_epoch, int) or isinstance(activation_epoch, bool) or activation_epoch < 0
            or not isinstance(version, int) or isinstance(version, bool) or version < 1
            or not isinstance(allowed_data_classifications, frozenset) or not allowed_data_classifications
            or any(not isinstance(value, str) or not _CLASSIFICATION.fullmatch(value) for value in allowed_data_classifications)
            or any(not isinstance(value, str) or not _HEX.fullmatch(value) for key, value in fields.items() if key.endswith("fingerprint"))):
        raise OfficeEngineUnavailable("engine binding fingerprint is malformed")
    return sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def resolve_office_engine_row(row: dict[str, object] | None, *, data_classification: str) -> ResolvedOfficeEngine:
    if row is None or not isinstance(data_classification, str) or not _CLASSIFICATION.fullmatch(data_classification):
        raise OfficeEngineUnavailable("engine unavailable")
    try:
        classifications = _classifications(row["allowed_data_classifications"])
        engine_key, display_name_key, model_ref = row["engine_key"], row["display_name_key"], row["model_ref"]
        provider_id, model_id, transport, version = row["provider_id"], row["model_id"], row["transport"], row["version"]
        provider_auth_type, provider_api_key_transport = row["provider_auth_type"], row["provider_api_key_transport"]
        max_tokens_param, max_output_tokens = row["max_tokens_param"], row["max_output_tokens"]
        engine_enabled, activation_epoch = row["engine_enabled"], row["activation_epoch"]
        stored_fingerprint = row["binding_fingerprint"]
        credential_id, credential_fingerprint = row["credential_id"], row["credential_fingerprint"]
        available = (row["engine_enabled"] == 1 and row["provider_status"] == "active"
                     and row["provider_auth_type"] == "api_key" and row["model_status"] == "available"
                     and row["credential_state"] == "active")
        calculated = engine_binding_fingerprint(engine_key=engine_key, model_ref=model_ref, transport=transport,
            allowed_data_classifications=classifications, provider_fingerprint=row["provider_fingerprint"],
            model_fingerprint=row["model_fingerprint"], credential_id=credential_id, credential_fingerprint=credential_fingerprint,
            engine_enabled=engine_enabled, activation_epoch=activation_epoch, version=version)
    except (KeyError, OfficeEngineUnavailable) as error:
        raise OfficeEngineUnavailable("engine catalogue row is incomplete") from error
    if (not available or transport not in _SUPPORTED_TRANSPORTS or data_classification not in classifications
            or not isinstance(engine_key, str) or not engine_key or not isinstance(display_name_key, str) or not display_name_key
            or isinstance(model_ref, bool) or not isinstance(model_ref, int) or model_ref < 1
            or not isinstance(provider_id, str) or not provider_id or not isinstance(provider_auth_type, str) or provider_auth_type != "api_key"
            or not isinstance(provider_api_key_transport, str) or not provider_api_key_transport
            or not isinstance(model_id, str) or not model_id
            or not isinstance(max_tokens_param, str) or max_tokens_param not in _MAX_TOKEN_PARAMS
            or not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool) or max_output_tokens < 1
            or not isinstance(engine_enabled, int) or isinstance(engine_enabled, bool) or engine_enabled not in (0, 1)
            or not isinstance(activation_epoch, int) or isinstance(activation_epoch, bool) or activation_epoch < 0
            or isinstance(version, bool) or not isinstance(version, int) or version < 1
            or not isinstance(stored_fingerprint, str) or not _HEX.fullmatch(stored_fingerprint) or stored_fingerprint != calculated):
        raise OfficeEngineUnavailable("engine unavailable")
    return ResolvedOfficeEngine(engine_key, display_name_key, model_ref, provider_id, provider_auth_type, provider_api_key_transport,
        model_id, transport, classifications, max_tokens_param, max_output_tokens, credential_id, credential_fingerprint,
        stored_fingerprint, version)


async def resolve_office_engine_in_cursor(cur, *, engine_key: str, data_classification: str,
                                          for_update: bool = False) -> ResolvedOfficeEngine:
    """Resolve the exact pinned credential; acceptance callers lock it too."""
    await cur.execute(
        "SELECT e.engine_key,e.display_name_key,e.model_ref,e.transport,e.allowed_data_classifications,"
        "e.binding_fingerprint,e.version,e.enabled AS engine_enabled,e.activation_epoch,p.id AS provider_id,"
        "p.status AS provider_status,p.auth_type AS provider_auth_type,p.api_key_transport AS provider_api_key_transport,m.model_id,m.status AS model_status,"
        "m.max_tokens_param,m.max_output_tokens,e.credential_id,c.state AS credential_state,"
        "SHA2(CAST(JSON_ARRAY('office-engine-provider-v1',p.id,p.base_url,p.auth_type,p.api_key_transport,p.status,p.is_local) AS CHAR),256) AS provider_fingerprint,"
        "SHA2(CAST(JSON_ARRAY('office-engine-model-v1',m.id,m.provider_id,m.model_id,m.status,m.is_alias,m.context_window,m.supports_tool_use,m.supports_structured_output,m.max_tokens_param,m.max_output_tokens,m.input_modalities) AS CHAR),256) AS model_fingerprint,"
        "SHA2(CAST(JSON_ARRAY('office-engine-credential-v1',c.id,c.provider_id,c.state,c.activated_at,c.revoked_at,SHA2(c.encrypted_value,256)) AS CHAR),256) AS credential_fingerprint "
        "FROM office_engine e JOIN model m ON m.id=e.model_ref JOIN provider p ON p.id=m.provider_id JOIN credential c ON c.id=e.credential_id AND c.provider_id=p.id WHERE e.engine_key=%s"
        + (" FOR UPDATE" if for_update else ""), (engine_key,))
    row = await cur.fetchone()
    if row is not None and not isinstance(row, dict):
        columns = ("engine_key", "display_name_key", "model_ref", "transport", "allowed_data_classifications", "binding_fingerprint", "version", "engine_enabled", "activation_epoch", "provider_id", "provider_status", "provider_auth_type", "provider_api_key_transport", "model_id", "model_status", "max_tokens_param", "max_output_tokens", "credential_id", "credential_state", "provider_fingerprint", "model_fingerprint", "credential_fingerprint")
        row = dict(zip(columns, row, strict=True))
    return resolve_office_engine_row(row, data_classification=data_classification)


async def load_pinned_office_credential_in_cursor(cur, *, engine: ResolvedOfficeEngine,
                                                   for_update: bool = False) -> str:
    """Return only the credential pinned by an already-attested engine."""
    await cur.execute(
        "SELECT encrypted_value,SHA2(CAST(JSON_ARRAY('office-engine-credential-v1',id,provider_id,state,activated_at,revoked_at,SHA2(encrypted_value,256)) AS CHAR),256) AS credential_fingerprint "
        "FROM credential WHERE id=%s AND provider_id=%s AND state='active'" + (" FOR UPDATE" if for_update else ""),
        (engine.credential_id, engine.provider_id),
    )
    row = await cur.fetchone()
    if isinstance(row, dict):
        ciphertext, fingerprint = row.get("encrypted_value"), row.get("credential_fingerprint")
    elif isinstance(row, (tuple, list)) and len(row) == 2:
        ciphertext, fingerprint = row
    else:
        raise OfficeEngineUnavailable("pinned engine credential is unavailable")
    if not isinstance(ciphertext, str) or not isinstance(fingerprint, str) or fingerprint != engine.credential_fingerprint:
        raise OfficeEngineUnavailable("pinned engine credential changed")
    return ciphertext
