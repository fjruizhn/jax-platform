"""Fail-closed resolution for the inert Office engine catalogue.

The catalogue identifies a model binding; it is deliberately not a provider
client and never yields a URL or credential.  A binding fingerprint is written
by the privileged migrator when it approves a catalogue row.  The resolver
recomputes it from the current model, provider and active credential identity
so a later catalogue mutation cannot silently select a different transport.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re


_CLASSIFICATION = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SUPPORTED_TRANSPORTS = frozenset({"http_openai_compat"})


class OfficeEngineUnavailable(RuntimeError):
    """The requested Office engine has no safe currently-attested binding."""


@dataclass(frozen=True)
class ResolvedOfficeEngine:
    engine_key: str
    display_name_key: str
    model_ref: int
    provider_id: str
    model_id: str
    transport: str
    allowed_data_classifications: frozenset[str]
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
                               provider_fingerprint: str, model_fingerprint: str,
                               credential_fingerprint: str, version: int) -> str:
    """Canonical digest of non-secret dynamic dependencies for one binding."""
    fields = {
        "credential_fingerprint": credential_fingerprint,
        "engine_key": engine_key,
        "model_fingerprint": model_fingerprint,
        "model_ref": model_ref,
        "provider_fingerprint": provider_fingerprint,
        "transport": transport,
        "version": version,
    }
    if (not isinstance(model_ref, int) or isinstance(model_ref, bool) or model_ref < 1
            or not isinstance(version, int) or isinstance(version, bool) or version < 1
            or any(not isinstance(value, str) or not _HEX.fullmatch(value)
                   for key, value in fields.items() if key.endswith("fingerprint"))):
        raise OfficeEngineUnavailable("engine binding fingerprint is malformed")
    return sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def resolve_office_engine_row(row: dict[str, object] | None, *, data_classification: str) -> ResolvedOfficeEngine:
    """Validate one complete catalogue row; no fallback model is selected."""
    if row is None or not isinstance(data_classification, str) or not _CLASSIFICATION.fullmatch(data_classification):
        raise OfficeEngineUnavailable("engine unavailable")
    try:
        classifications = _classifications(row["allowed_data_classifications"])
        engine_key, display_name_key, model_ref = row["engine_key"], row["display_name_key"], row["model_ref"]
        provider_id, model_id, transport, version = row["provider_id"], row["model_id"], row["transport"], row["version"]
        stored_fingerprint = row["binding_fingerprint"]
        available = (row["engine_enabled"] == 1 and row["provider_status"] == "active"
                     and row["provider_auth_type"] == "api_key" and row["model_status"] == "available"
                     and row["active_credential_count"] == 1)
        calculated = engine_binding_fingerprint(
            engine_key=engine_key, model_ref=model_ref, transport=transport,
            provider_fingerprint=row["provider_fingerprint"], model_fingerprint=row["model_fingerprint"],
            credential_fingerprint=row["credential_fingerprint"], version=version,
        )
    except (KeyError, OfficeEngineUnavailable) as error:
        raise OfficeEngineUnavailable("engine catalogue row is incomplete") from error
    if (not available or transport not in _SUPPORTED_TRANSPORTS or data_classification not in classifications
            or not isinstance(engine_key, str) or not engine_key or not isinstance(display_name_key, str) or not display_name_key
            or isinstance(model_ref, bool) or not isinstance(model_ref, int) or model_ref < 1
            or not isinstance(provider_id, str) or not provider_id or not isinstance(model_id, str) or not model_id
            or isinstance(version, bool) or not isinstance(version, int) or version < 1
            or not isinstance(stored_fingerprint, str) or not _HEX.fullmatch(stored_fingerprint)
            or stored_fingerprint != calculated):
        raise OfficeEngineUnavailable("engine unavailable")
    return ResolvedOfficeEngine(engine_key, display_name_key, model_ref, provider_id, model_id,
                                transport, classifications, version)


async def resolve_office_engine_in_cursor(cur, *, engine_key: str,
                                           data_classification: str) -> ResolvedOfficeEngine:
    """Resolve a binding without reading a credential or provider URL into Python."""
    await cur.execute(
        "SELECT e.engine_key,e.display_name_key,e.model_ref,e.transport,e.allowed_data_classifications,"
        "e.binding_fingerprint,e.version,e.enabled AS engine_enabled,p.id AS provider_id,"
        "p.status AS provider_status,p.auth_type AS provider_auth_type,m.model_id,m.status AS model_status,"
        "(SELECT COUNT(*) FROM credential c WHERE c.provider_id=p.id AND c.state='active') AS active_credential_count,"
        "SHA2(CONCAT_WS(CHAR(31),p.id,COALESCE(p.base_url,''),p.auth_type,p.status,p.is_local),256) AS provider_fingerprint,"
        "SHA2(CONCAT_WS(CHAR(31),m.id,m.provider_id,m.model_id,m.status,m.is_alias,COALESCE(m.context_window,''),"
        "m.supports_tool_use,m.supports_structured_output),256) AS model_fingerprint,"
        "(SELECT SHA2(CONCAT_WS(CHAR(31),c.id,c.provider_id,c.state,COALESCE(c.activated_at,''),"
        "COALESCE(c.revoked_at,''),SHA2(c.encrypted_value,256)),256) FROM credential c "
        "WHERE c.provider_id=p.id AND c.state='active' ORDER BY c.id LIMIT 1) AS credential_fingerprint "
        "FROM office_engine e JOIN model m ON m.id=e.model_ref JOIN provider p ON p.id=m.provider_id "
        "WHERE e.engine_key=%s", (engine_key,))
    row = await cur.fetchone()
    if row is not None and not isinstance(row, dict):
        columns = ("engine_key", "display_name_key", "model_ref", "transport", "allowed_data_classifications",
                   "binding_fingerprint", "version", "engine_enabled", "provider_id", "provider_status",
                   "provider_auth_type", "model_id", "model_status", "active_credential_count",
                   "provider_fingerprint", "model_fingerprint", "credential_fingerprint")
        row = dict(zip(columns, row, strict=True))
    return resolve_office_engine_row(row, data_classification=data_classification)
