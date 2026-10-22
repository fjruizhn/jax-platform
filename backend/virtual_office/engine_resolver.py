"""Office-owned engine catalogue resolution.

This module deliberately resolves only the effective catalogue identity.  It
never reads a credential or provider URL: a future, separately reviewed
Office transport may resolve the managed credential at dispatch time.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re


_CLASSIFICATION = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_SUPPORTED_TRANSPORTS = frozenset({"http_openai_compat"})


class OfficeEngineUnavailable(RuntimeError):
    """The requested Office engine has no safe, currently usable binding."""


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
    if not isinstance(decoded, list) or not decoded:
        raise OfficeEngineUnavailable("engine classifications are malformed")
    values = frozenset(decoded)
    if (len(values) != len(decoded)
            or any(not isinstance(value, str) or not _CLASSIFICATION.fullmatch(value) for value in values)):
        raise OfficeEngineUnavailable("engine classifications are malformed")
    return values


def resolve_office_engine_row(row: dict[str, object] | None, *, data_classification: str) -> ResolvedOfficeEngine:
    """Validate one SQL catalogue row without accepting a fallback binding."""
    if row is None or not isinstance(data_classification, str) or not _CLASSIFICATION.fullmatch(data_classification):
        raise OfficeEngineUnavailable("engine unavailable")
    try:
        classifications = _classifications(row["allowed_data_classifications"])
        engine_key = row["engine_key"]
        display_name_key = row["display_name_key"]
        model_ref = row["model_ref"]
        provider_id = row["provider_id"]
        model_id = row["model_id"]
        transport = row["transport"]
        version = row["version"]
        available = (
            row["engine_enabled"] == 1
            and row["provider_status"] == "active"
            and row["provider_auth_type"] == "api_key"
            and row["model_status"] == "available"
            and row["has_active_credential"] == 1
        )
    except KeyError as error:
        raise OfficeEngineUnavailable("engine catalogue row is incomplete") from error
    if (not available or transport not in _SUPPORTED_TRANSPORTS
            or data_classification not in classifications
            or not isinstance(engine_key, str) or not engine_key
            or not isinstance(display_name_key, str) or not display_name_key
            or isinstance(model_ref, bool) or not isinstance(model_ref, int) or model_ref < 1
            or not isinstance(provider_id, str) or not provider_id
            or not isinstance(model_id, str) or not model_id
            or isinstance(version, bool) or not isinstance(version, int) or version < 1):
        raise OfficeEngineUnavailable("engine unavailable")
    return ResolvedOfficeEngine(
        engine_key=engine_key, display_name_key=display_name_key, model_ref=model_ref,
        provider_id=provider_id, model_id=model_id, transport=transport,
        allowed_data_classifications=classifications, version=version,
    )


async def resolve_office_engine_in_cursor(cur, *, engine_key: str,
                                           data_classification: str) -> ResolvedOfficeEngine:
    """Read one registered engine and prove that a managed credential exists.

    The credential is an ``EXISTS`` predicate only.  Keeping its ciphertext out
    of this resolver prevents a catalogue/listing caller from becoming a
    dispatch credential reader.
    """
    await cur.execute(
        "SELECT e.engine_key,e.display_name_key,e.model_ref,e.transport,"
        "e.allowed_data_classifications,e.version,e.enabled AS engine_enabled,"
        "p.id AS provider_id,p.status AS provider_status,p.auth_type AS provider_auth_type,"
        "m.model_id,m.status AS model_status,"
        "EXISTS(SELECT 1 FROM credential c WHERE c.provider_id=p.id AND c.state='active') "
        "AS has_active_credential "
        "FROM office_engine e JOIN model m ON m.id=e.model_ref "
        "JOIN provider p ON p.id=m.provider_id WHERE e.engine_key=%s",
        (engine_key,),
    )
    row = await cur.fetchone()
    if row is not None and not isinstance(row, dict):
        columns = (
            "engine_key", "display_name_key", "model_ref", "transport",
            "allowed_data_classifications", "version", "engine_enabled", "provider_id",
            "provider_status", "provider_auth_type", "model_id", "model_status",
            "has_active_credential",
        )
        row = dict(zip(columns, row, strict=True))
    return resolve_office_engine_row(row, data_classification=data_classification)
