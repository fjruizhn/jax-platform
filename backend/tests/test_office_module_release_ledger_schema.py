"""Candidate storage never represents review or release authority."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import uuid

import aiomysql
import pytest
from pymysql.err import IntegrityError, OperationalError


_MODULE_PATH = Path(__file__).parents[1] / "virtual_office" / "module_release_ledger.py"
_SPEC = importlib.util.spec_from_file_location("office_module_candidate_storage", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
candidate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(candidate)


def _ddl(table: str) -> str:
    return next(statement for statement in candidate.MODULE_CANDIDATE_DDL if f"CREATE TABLE IF NOT EXISTS {table} " in statement)


def test_candidate_storage_has_no_release_authority_surface() -> None:
    source = "\n".join(candidate.MODULE_CANDIDATE_DDL)
    for forbidden in ("PUBLISHED", "APPROVED", "PASS", "effective_", "authorization_event", "mfa_proof", "rights_verdict", "creative_approval"):
        assert forbidden not in source
    assert set(candidate._APPEND_ONLY) == set(candidate._TABLE_COLUMNS)


def test_candidate_identity_manifest_claim_and_generic_evidence_are_bound() -> None:
    identity = _ddl("office_module_candidate")
    revision = _ddl("office_module_candidate_revision")
    evidence = _ddl("office_module_candidate_evidence")
    assert "UNIQUE KEY uq_office_module_candidate_identity (module_id,module_version)" in identity
    assert "manifest_sha256=UNHEX(SHA2(manifest_bytes,256))" in revision
    assert "claimed_artifact_set_digest BINARY(32)" in revision
    assert "claimed_signing_key_id" in revision
    assert "evidence_sha256=UNHEX(SHA2(evidence_bytes,256))" in evidence
    assert "FOREIGN KEY (candidate_revision_id,candidate_id)" in evidence


def test_predecessor_is_only_removed_when_empty() -> None:
    source = Path(candidate.__file__).read_text()
    assert "refusing destructive candidate-storage migration" in source
    assert candidate._LEGACY_TABLES[0] == "office_module_published_event"


def test_runtime_acl_cannot_gain_candidate_update_or_delete() -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from db import runtime_privileges
    assert set(candidate._APPEND_ONLY) <= runtime_privileges._FORBIDDEN_TABLES
    assert not (set(candidate._APPEND_ONLY) & set(runtime_privileges.runtime_table_privilege_map()))


def test_mariadb_candidate_storage_is_append_only_and_hash_bound(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_candidate_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4")
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await candidate.apply_module_candidate_storage_migrations(cur)
                await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("INSERT INTO office_module_candidate (module_id,module_version) VALUES ('studio','1.0.0')")
                candidate_id = cur.lastrowid
                manifest = b'{"untrusted":true}'
                artifact = bytes.fromhex("11" * 32)
                await cur.execute("INSERT INTO office_module_candidate_revision (candidate_id,revision_no,manifest_sha256,manifest_bytes,claimed_artifact_set_digest,claimed_signing_key_id) VALUES (%s,1,UNHEX(SHA2(%s,256)),%s,%s,'untrusted-key')", (candidate_id, manifest, manifest, artifact))
                revision_id = cur.lastrowid
                evidence = b"opaque evidence"
                await cur.execute("INSERT INTO office_module_candidate_evidence (candidate_revision_id,candidate_id,evidence_kind,evidence_sha256,evidence_bytes) VALUES (%s,%s,'CUSTOM',UNHEX(SHA2(%s,256)),%s)", (revision_id, candidate_id, evidence, evidence))
                with pytest.raises((IntegrityError, OperationalError)):
                    await cur.execute("INSERT INTO office_module_candidate_revision (candidate_id,revision_no,manifest_sha256,manifest_bytes,claimed_artifact_set_digest,claimed_signing_key_id) VALUES (%s,2,%s,%s,%s,'untrusted-key')", (candidate_id, bytes(32), manifest, artifact))
                with pytest.raises(OperationalError, match="append-only"):
                    await cur.execute("UPDATE office_module_candidate SET module_version='2.0.0' WHERE candidate_id=%s", (candidate_id,))
                with pytest.raises(OperationalError, match="append-only"):
                    await cur.execute("DELETE FROM office_module_candidate_revision WHERE candidate_revision_id=%s", (revision_id,))
                await candidate.assert_module_candidate_storage_metadata(cur)
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)


def test_mariadb_empty_predecessor_is_removed_and_nonempty_predecessor_fails(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_candidate_legacy_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4")
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await cur.execute("CREATE TABLE office_module_release (release_id BIGINT PRIMARY KEY) ENGINE=InnoDB")
                await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_module_release'")
                assert await cur.fetchone() is None
                await cur.execute("CREATE TABLE office_module_release (release_id BIGINT PRIMARY KEY) ENGINE=InnoDB")
                await cur.execute("INSERT INTO office_module_release VALUES (1)")
                with pytest.raises(RuntimeError, match="predecessor contains rows"):
                    await candidate.apply_module_candidate_storage_migrations(cur)
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)


def test_mariadb_metadata_rejects_noop_append_only_trigger(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_candidate_drift_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4")
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("DROP TRIGGER no_update_office_module_candidate")
                await cur.execute("CREATE TRIGGER no_update_office_module_candidate BEFORE UPDATE ON office_module_candidate FOR EACH ROW SET NEW.module_id=NEW.module_id")
                with pytest.raises(RuntimeError, match="triggers.*invalid"):
                    await candidate.assert_module_candidate_storage_metadata(cur)
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)


def test_mariadb_metadata_rejects_weakened_hash_check(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_candidate_check_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4")
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("ALTER TABLE office_module_candidate_revision DROP CONSTRAINT chk_office_module_candidate_revision_payload")
                await cur.execute("ALTER TABLE office_module_candidate_revision ADD CONSTRAINT chk_office_module_candidate_revision_payload CHECK (revision_no>=0)")
                with pytest.raises(RuntimeError, match="CHECK expressions are invalid"):
                    await candidate.assert_module_candidate_storage_metadata(cur)
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)
