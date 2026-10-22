from __future__ import annotations

import importlib.util
import asyncio
import os
from pathlib import Path
import uuid

import aiomysql
import pytest
from pymysql.err import IntegrityError, OperationalError


_MODULE_PATH = Path(__file__).parents[1] / "virtual_office" / "module_release_ledger.py"
_SPEC = importlib.util.spec_from_file_location("office_module_release_ledger_schema", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
ledger = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ledger)


def _ddl(table: str) -> str:
    return next(statement for statement in ledger.MODULE_RELEASE_DDL if f"CREATE TABLE IF NOT EXISTS {table} " in statement)


def test_release_identity_is_global_and_has_creator_provenance() -> None:
    ddl = _ddl("office_module_release")
    assert "UNIQUE KEY uq_office_module_release_identity (module_id,module_version)" in ddl
    for column in ("created_by_user_id", "created_authorization_event_id", "created_mfa_proof_event_id"):
        assert column in ddl


def test_revision_binds_artifact_set_and_exact_signed_manifest_v2() -> None:
    ddl = _ddl("office_module_release_revision")
    for column in ("artifact_set_digest BINARY(32)", "manifest_digest BINARY(32)", "manifest_bytes LONGBLOB", "signing_key_id", "manifest_signature LONGBLOB"):
        assert column in ddl
    assert "manifest_schema=BINARY 'manifest/v2'" in ddl
    assert "manifest_digest=UNHEX(SHA2(manifest_bytes,256))" in ddl


def test_license_evidence_is_typed_and_bound_to_its_revision_artifact() -> None:
    ddl = _ddl("office_module_license_evidence")
    assert "evidence_type IN (BINARY 'LICENSE_TEXT',BINARY 'RIGHTS_GRANT',BINARY 'THIRD_PARTY_NOTICE')" in ddl
    assert "FOREIGN KEY (revision_id,release_id,artifact_set_digest)" in ddl
    assert "evidence_digest=UNHEX(SHA2(evidence_bytes,256))" in ddl


def test_creative_approval_requires_human_actor_authorization_and_mfa() -> None:
    ddl = _ddl("office_module_creative_approval")
    for column in ("actor_user_id INT NOT NULL", "authorization_event_id BIGINT UNSIGNED NOT NULL", "mfa_proof_event_id BIGINT UNSIGNED NOT NULL"):
        assert column in ddl
    assert "outcome IN (BINARY 'APPROVED',BINARY 'REJECTED')" in ddl


def test_rights_verdict_is_a_distinct_authenticated_jacobs_terminal_receipt() -> None:
    ddl = _ddl("office_module_rights_verdict")
    assert "outcome IN (BINARY 'PASS',BINARY 'FAIL',BINARY 'REVIEW_REQUIRED')" in ddl
    for column in ("jacobs_agent_id", "jacobs_receipt_digest", "jacobs_receipt_bytes", "jacobs_signing_key_id", "jacobs_receipt_signature"):
        assert column in ddl
    assert "FOREIGN KEY (license_evidence_id,license_revision_id,license_artifact_set_digest,license_evidence_type)" in ddl


def test_only_approved_and_pass_events_can_be_mutable_heads() -> None:
    creative = _ddl("office_module_creative_approval_head")
    rights = _ddl("office_module_rights_verdict_head")
    assert "outcome=BINARY 'APPROVED' AND row_version>0" in creative
    assert "outcome=BINARY 'PASS' AND row_version>0" in rights
    assert "FOREIGN KEY (approved_approval_id,revision_id,outcome)" in creative
    assert "FOREIGN KEY (pass_verdict_id,revision_id,outcome)" in rights


def test_effective_published_event_references_both_approved_heads() -> None:
    ddl = _ddl("office_module_published_event")
    assert "FOREIGN KEY (creative_head_revision_id,creative_approval_id,creative_head_outcome)" in ddl
    assert "FOREIGN KEY (rights_head_revision_id,rights_verdict_id,rights_head_outcome)" in ddl
    assert "creative_head_revision_id=revision_id AND rights_head_revision_id=revision_id" in ddl
    assert "creative_head_outcome=BINARY 'APPROVED' AND BINARY rights_head_outcome=BINARY 'PASS'" in ddl


def test_mutable_release_cannot_claim_published_without_effective_event() -> None:
    ddl = _ddl("office_module_release")
    assert "state=BINARY 'PUBLISHED' AND current_revision_id IS NOT NULL AND effective_published_event_id IS NOT NULL" in ddl
    assert "fk_office_module_release_effective_published_event" in "\n".join(ledger._LATE_FOREIGN_KEYS)


def test_history_tables_are_append_only_and_heads_are_not() -> None:
    assert set(ledger._APPEND_ONLY) == {
        "office_module_release_revision", "office_module_license_evidence", "office_module_creative_approval",
        "office_module_rights_verdict", "office_module_published_event",
    }
    assert "office_module_creative_approval_head" not in ledger._APPEND_ONLY
    assert "office_module_rights_verdict_head" not in ledger._APPEND_ONLY


def test_metadata_attestation_covers_every_table_and_check() -> None:
    assert set(ledger._TABLE_COLUMNS) == {
        "office_module_release", "office_module_release_revision", "office_module_license_evidence",
        "office_module_creative_approval", "office_module_rights_verdict", "office_module_creative_approval_head",
        "office_module_rights_verdict_head", "office_module_published_event",
    }
    assert len(ledger._CHECK_NAMES) == 9


def test_mariadb_schema_rejects_false_heads_and_history_mutation(client) -> None:
    """The physical gate must reject false readiness even without an API."""
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_gate_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(
            host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
            user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"],
            db=source, autocommit=True, charset="utf8mb4",
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await cur.execute("CREATE TABLE jax_users (user_id INT NOT NULL PRIMARY KEY) ENGINE=InnoDB")
                await cur.execute("CREATE TABLE office_authorization_event (event_id BIGINT UNSIGNED NOT NULL PRIMARY KEY) ENGINE=InnoDB")
                await cur.execute("CREATE TABLE office_mfa_security_event (event_id BIGINT UNSIGNED NOT NULL PRIMARY KEY) ENGINE=InnoDB")
                await cur.execute("INSERT INTO jax_users VALUES (1)")
                await cur.execute("INSERT INTO office_authorization_event VALUES (1)")
                await cur.execute("INSERT INTO office_mfa_security_event VALUES (1)")
                await ledger.apply_module_release_ledger_migrations(cur)
                await ledger.apply_module_release_ledger_migrations(cur)
                artifact_digest = bytes.fromhex("11" * 32)
                manifest = b'{"schema":"manifest/v2"}'
                await cur.execute(
                    "INSERT INTO office_module_release (module_id,module_version,created_by_user_id,created_authorization_event_id,created_mfa_proof_event_id) VALUES (%s,%s,1,1,1)",
                    ("studio", "1.0.0"),
                )
                release_id = cur.lastrowid
                await cur.execute(
                    "INSERT INTO office_module_release_revision (release_id,revision_no,artifact_set_digest,manifest_schema,manifest_digest,manifest_bytes,signing_key_id,manifest_signature,created_by_user_id) VALUES (%s,1,%s,'manifest/v2',UNHEX(SHA2(%s,256)),%s,'key-1',%s,1)",
                    (release_id, artifact_digest, manifest, manifest, b"signature"),
                )
                revision_id = cur.lastrowid
                await cur.execute(
                    "INSERT INTO office_module_license_evidence (revision_id,release_id,artifact_set_digest,evidence_type,evidence_digest,evidence_bytes,recorded_by_user_id) VALUES (%s,%s,%s,'LICENSE_TEXT',UNHEX(SHA2(%s,256)),%s,1)",
                    (revision_id, release_id, artifact_digest, b"license", b"license"),
                )
                evidence_id = cur.lastrowid
                await cur.execute(
                    "INSERT INTO office_module_creative_approval (revision_id,release_id,artifact_set_digest,outcome,actor_user_id,authorization_event_id,mfa_proof_event_id) VALUES (%s,%s,%s,'REJECTED',1,1,1)",
                    (revision_id, release_id, artifact_digest),
                )
                rejected_id = cur.lastrowid
                with pytest.raises(IntegrityError):
                    await cur.execute("INSERT INTO office_module_creative_approval_head (revision_id,approved_approval_id) VALUES (%s,%s)", (revision_id, rejected_id))
                await cur.execute(
                    "INSERT INTO office_module_rights_verdict (revision_id,release_id,artifact_set_digest,outcome,license_evidence_id,license_revision_id,license_artifact_set_digest,license_evidence_type,jacobs_agent_id,jacobs_receipt_digest,jacobs_receipt_bytes,jacobs_signing_key_id,jacobs_receipt_signature) VALUES (%s,%s,%s,'REVIEW_REQUIRED',%s,%s,%s,'LICENSE_TEXT','jacobs-1',UNHEX(SHA2(%s,256)),%s,'jacobs-key-1',%s)",
                    (revision_id, release_id, artifact_digest, evidence_id, revision_id, artifact_digest, b"receipt", b"receipt", b"signature"),
                )
                review_id = cur.lastrowid
                with pytest.raises(IntegrityError):
                    await cur.execute("INSERT INTO office_module_rights_verdict_head (revision_id,pass_verdict_id) VALUES (%s,%s)", (revision_id, review_id))
                with pytest.raises(OperationalError, match="append-only"):
                    await cur.execute("UPDATE office_module_creative_approval SET outcome='APPROVED' WHERE creative_approval_id=%s", (rejected_id,))
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()

    client.portal.call(scenario)
