"""Versioned least-privilege table ACL for the web database principal.

The manifest lives beside the provisioner so both the root-only provisioning
path and the runtime guard consume the same reviewed, deterministic contract.
Schema-level SELECT/INSERT are intentional; UPDATE and DELETE are granted
only to the tables listed here.  Migration-only and append-only tables must
never be added.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re


MANIFEST_VERSION = "runtime-table-privileges-v1"
_ALLOWED_PRIVILEGES = frozenset(("UPDATE", "DELETE"))
_FORBIDDEN_TABLES = frozenset((
    "office_authorization_event",
    "office_bootstrap_state",
    "office_invitation_event",
    "office_invitation_issuance",
    "office_mfa_security_event",
    "office_membership_mutation_receipt",
    "office_profile_revocation_event",
    "office_schema_revision",
    "office_module_candidate",
    "office_module_candidate_revision",
    "office_module_candidate_evidence",
    "office_module_creative_review_event",
    "office_module_rights_review_event",
    "office_module_release_event",
))
_TABLE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def manifest_path() -> Path:
    return Path(__file__).resolve().parents[2] / "ops" / "migration" / "runtime-table-privileges-v1.txt"


def load_runtime_table_privileges() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Parse the checked-in manifest and reject ambiguity before serving."""
    try:
        raw = manifest_path().read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError("runtime table privilege manifest is unavailable") from error
    rows: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(raw.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) != 2 or not _TABLE.fullmatch(parts[0]):
            raise RuntimeError(f"runtime table privilege manifest is malformed at line {line_number}")
        table, raw_privileges = parts
        privileges = tuple(raw_privileges.split(","))
        if (not privileges or len(set(privileges)) != len(privileges)
                or any(privilege not in _ALLOWED_PRIVILEGES for privilege in privileges)):
            raise RuntimeError(f"runtime table privilege manifest has invalid privileges at line {line_number}")
        if table in seen or table in _FORBIDDEN_TABLES:
            raise RuntimeError(f"runtime table privilege manifest has forbidden or duplicate table at line {line_number}")
        if rows and table <= rows[-1][0]:
            raise RuntimeError("runtime table privilege manifest is not bytewise ordered")
        seen.add(table)
        rows.append((table, privileges))
    if not rows:
        raise RuntimeError("runtime table privilege manifest is empty")
    return tuple(rows)


def runtime_table_privilege_digest() -> str:
    """Bind the runtime revision to the exact normalized grant body."""
    normalized = "\n".join(f"{table} {','.join(privileges)}" for table, privileges in load_runtime_table_privileges())
    return sha256(normalized.encode("ascii")).hexdigest()


def runtime_table_privilege_map() -> dict[str, frozenset[str]]:
    return {table: frozenset(privileges) for table, privileges in load_runtime_table_privileges()}
