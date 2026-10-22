"""Candidate storage never represents review or release authority."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import uuid

import aiomysql
import pytest
from pymysql.err import DataError, IntegrityError, OperationalError
from db_connect_config import db_connect_timeout_seconds


_MODULE_PATH = Path(__file__).parents[1] / "virtual_office" / "module_release_ledger.py"
_SPEC = importlib.util.spec_from_file_location("office_module_candidate_storage", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
candidate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(candidate)


def _ddl(table: str) -> str:
    return next(statement for statement in candidate.MODULE_CANDIDATE_DDL if f"CREATE TABLE IF NOT EXISTS {table} " in statement)


async def _create_project_scope(cur) -> None:
    await cur.execute(
        "CREATE TABLE jax_project_scope (project_id INT NOT NULL,tenant_id INT NOT NULL,"
        "PRIMARY KEY(project_id,tenant_id)) ENGINE=InnoDB"
    )
    await cur.execute("INSERT INTO jax_project_scope VALUES (11,12)")


def _predecessor_ddl() -> tuple[str, ...]:
    return tuple(
        statement.replace(
            "origin_project_id INT NOT NULL,origin_tenant_id INT NOT NULL,", ""
        ).replace(
            "KEY idx_office_module_candidate_origin (origin_project_id,origin_tenant_id,candidate_id),", ""
        ).replace(
            "CONSTRAINT fk_office_module_candidate_origin_scope FOREIGN KEY (origin_project_id,origin_tenant_id) REFERENCES jax_project_scope(project_id,tenant_id),", ""
        ).replace(
            "KEY idx_office_module_candidate_evidence_revision_candidate (candidate_revision_id,candidate_id),", ""
        )
        for statement in candidate.MODULE_CANDIDATE_DDL
    )


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
    assert "origin_project_id INT NOT NULL,origin_tenant_id INT NOT NULL" in identity
    assert "FOREIGN KEY (origin_project_id,origin_tenant_id) REFERENCES jax_project_scope(project_id,tenant_id)" in identity
    assert "manifest_sha256=UNHEX(SHA2(manifest_bytes,256))" in revision
    release_identity = "UNIQUE KEY uq_office_module_candidate_revision_release_identity (candidate_revision_id,candidate_id,manifest_sha256,claimed_artifact_set_digest)"
    assert release_identity in revision
    assert candidate._INDEXES[("office_module_candidate_revision", "uq_office_module_candidate_revision_release_identity")] == (
        True, ("candidate_revision_id", "candidate_id", "manifest_sha256", "claimed_artifact_set_digest"),
    )
    assert "claimed_artifact_set_digest BINARY(32)" in revision
    assert "claimed_signing_key_id" in revision
    assert "evidence_sha256=UNHEX(SHA2(evidence_bytes,256))" in evidence
    assert "FOREIGN KEY (candidate_revision_id,candidate_id)" in evidence
    assert "manifest_bytes MEDIUMBLOB" in revision
    assert "evidence_bytes MEDIUMBLOB" in evidence
    assert "OCTET_LENGTH(manifest_bytes)<=16777215" in revision
    assert "OCTET_LENGTH(evidence_bytes)<=16777215" in evidence


def test_legacy_release_tables_fail_closed_without_destructive_ddl() -> None:
    source = Path(candidate.__file__).read_text()
    assert "explicit data-preserving migration" in source
    assert "DROP TABLE" not in source
    assert "FOREIGN_KEY_CHECKS" not in source
    assert candidate._LEGACY_TABLES[0] == "office_module_published_event"


def test_runtime_acl_cannot_gain_candidate_update_or_delete() -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from db import runtime_privileges
    assert set(candidate._APPEND_ONLY) <= runtime_privileges._FORBIDDEN_TABLES
    assert not (set(candidate._APPEND_ONLY) & set(runtime_privileges.runtime_table_privilege_map()))
    from db.runtime_contract import _module_candidate_storage_metadata
    assert callable(_module_candidate_storage_metadata)


def test_mariadb_candidate_storage_is_append_only_and_hash_bound(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_candidate_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds())
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await _create_project_scope(cur)
                await candidate.apply_module_candidate_storage_migrations(cur)
                await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("INSERT INTO office_module_candidate (module_id,module_version,origin_project_id,origin_tenant_id) VALUES ('studio','1.0.0',11,12)")
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
                # A v15 table with the old implicit FK index is resumable only
                # when the FK is still exact.  The canonicalizer must never
                # turn a CASCADE drift into a v15-ready schema.
                await cur.execute(
                    "ALTER TABLE office_module_candidate_evidence "
                    "DROP FOREIGN KEY fk_office_module_candidate_evidence_revision,"
                    "DROP INDEX idx_office_module_candidate_evidence_revision_candidate,"
                    "ADD CONSTRAINT fk_office_module_candidate_evidence_revision "
                    "FOREIGN KEY (candidate_revision_id,candidate_id) "
                    "REFERENCES office_module_candidate_revision(candidate_revision_id,candidate_id) ON DELETE CASCADE"
                )
                with pytest.raises(RuntimeError, match="foreign key.*invalid"):
                    await candidate.apply_module_candidate_storage_migrations(cur)
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)


def test_mariadb_legacy_tables_are_preserved_and_rejected_empty_or_nonempty(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_candidate_legacy_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds())
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await cur.execute("CREATE TABLE office_module_release (release_id BIGINT PRIMARY KEY, unrelated VARCHAR(10) NULL) ENGINE=InnoDB")
                with pytest.raises(RuntimeError, match="explicit data-preserving migration"):
                    await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("SELECT COUNT(*) FROM office_module_release")
                assert (await cur.fetchone())[0] == 0
                await cur.execute("INSERT INTO office_module_release VALUES (1,'kept')")
                with pytest.raises(RuntimeError, match="explicit data-preserving migration"):
                    await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("SELECT release_id,unrelated FROM office_module_release")
                assert await cur.fetchone() == (1, "kept")
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)


def test_mariadb_empty_exact_predecessor_gains_immutable_origin_or_fails_closed(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_candidate_predecessor_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds())
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await _create_project_scope(cur)
                for statement in _predecessor_ddl():
                    await cur.execute(statement)
                for table in candidate._APPEND_ONLY:
                    for operation in ("UPDATE", "DELETE"):
                        await cur.execute(
                            f"CREATE TRIGGER no_{operation.lower()}_{table} BEFORE {operation} ON {table} "
                            f"FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'"
                        )
                await cur.execute("ALTER TABLE office_module_candidate DROP INDEX uq_office_module_candidate_identity, ADD UNIQUE KEY uq_office_module_candidate_identity (module_id(1),module_version(1))")
                with pytest.raises(RuntimeError, match="index.*invalid"):
                    await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("ALTER TABLE office_module_candidate DROP INDEX uq_office_module_candidate_identity, ADD UNIQUE KEY uq_office_module_candidate_identity (module_id,module_version)")
                await cur.execute("ALTER TABLE office_module_candidate ADD UNIQUE KEY rogue_predecessor_module (module_id)")
                with pytest.raises(RuntimeError, match="index set is invalid"):
                    await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("ALTER TABLE office_module_candidate DROP INDEX rogue_predecessor_module")
                await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("SHOW COLUMNS FROM office_module_candidate LIKE 'origin_project_id'")
                assert (await cur.fetchone())[1:3] == ("int(11)", "NO")
                await cur.execute("SHOW INDEX FROM office_module_candidate_evidence WHERE Key_name='idx_office_module_candidate_evidence_revision_candidate'")
                assert (await cur.fetchone())[4] == "candidate_revision_id"
                await cur.execute("SHOW INDEX FROM office_module_candidate_evidence WHERE Key_name='fk_office_module_candidate_evidence_revision'")
                assert await cur.fetchone() is None
                # The upgraded v15 schema resumes unchanged, proving the
                # predecessor-only absence of origin_scope is not reused.
                await candidate.apply_module_candidate_storage_migrations(cur)
                await candidate.assert_module_candidate_storage_metadata(cur)
                await cur.execute("INSERT INTO office_module_candidate (module_id,module_version,origin_project_id,origin_tenant_id) VALUES ('predecessor','1',11,12)")
                with pytest.raises(IntegrityError):
                    await cur.execute("INSERT INTO office_module_candidate (module_id,module_version,origin_project_id,origin_tenant_id) VALUES ('wrong-scope','1',99,12)")
                await cur.execute("ALTER TABLE office_module_candidate DROP INDEX uq_office_module_candidate_identity, ADD UNIQUE KEY uq_office_module_candidate_identity (module_id(1),module_version(1))")
                with pytest.raises(RuntimeError, match="index.*invalid"):
                    await candidate.assert_module_candidate_storage_metadata(cur)
                await cur.execute("ALTER TABLE office_module_candidate DROP INDEX uq_office_module_candidate_identity, ADD UNIQUE KEY uq_office_module_candidate_identity (module_id,module_version)")
                await cur.execute("ALTER TABLE office_module_candidate ADD UNIQUE KEY rogue_final_module (module_id)")
                with pytest.raises(RuntimeError, match="index set is invalid"):
                    await candidate.assert_module_candidate_storage_metadata(cur)
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)


@pytest.mark.parametrize("state", ("nonempty", "partial"))
def test_mariadb_predecessor_rows_or_partial_set_fail_without_origin_alter(client, state: str) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_candidate_reject_{state}_{uuid.uuid4().hex[:12]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds())
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await _create_project_scope(cur)
                statements = _predecessor_ddl()
                for statement in statements[:1] if state == "partial" else statements:
                    await cur.execute(statement)
                if state == "nonempty":
                    for table in candidate._APPEND_ONLY:
                        for operation in ("UPDATE", "DELETE"):
                            await cur.execute(
                                f"CREATE TRIGGER no_{operation.lower()}_{table} BEFORE {operation} ON {table} "
                                f"FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} append-only'"
                            )
                    await cur.execute("INSERT INTO office_module_candidate (module_id,module_version) VALUES ('historic','1')")
                    expected = "origin-project mapping"
                else:
                    expected = "partial table set"
                with pytest.raises(RuntimeError, match=expected):
                    await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("SHOW COLUMNS FROM office_module_candidate LIKE 'origin_project_id'")
                assert await cur.fetchone() is None
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)


def test_mariadb_metadata_rejects_noop_append_only_trigger(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_candidate_drift_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds())
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await _create_project_scope(cur)
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
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds())
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await _create_project_scope(cur)
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


def test_mariadb_metadata_rejects_check_clause_on_decoy_table(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_candidate_decoy_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds())
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await _create_project_scope(cur)
                await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("ALTER TABLE office_module_candidate DROP CONSTRAINT chk_office_module_candidate_identity")
                await cur.execute("CREATE TABLE office_module_candidate_decoy (value INT NOT NULL, CONSTRAINT chk_office_module_candidate_identity CHECK (value>0)) ENGINE=InnoDB")
                with pytest.raises(RuntimeError, match="checks are invalid"):
                    await candidate.assert_module_candidate_storage_metadata(cur)
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)


def test_mariadb_mediumblob_boundary_matches_declared_cap(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_candidate_bound_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds())
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await _create_project_scope(cur)
                await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("INSERT INTO office_module_candidate (module_id,module_version,origin_project_id,origin_tenant_id) VALUES ('bounded','1',11,12)")
                candidate_id = cur.lastrowid
                digest = bytes.fromhex("22" * 32)
                await cur.execute("INSERT INTO office_module_candidate_revision (candidate_id,revision_no,manifest_sha256,manifest_bytes,claimed_artifact_set_digest,claimed_signing_key_id) VALUES (%s,1,UNHEX(SHA2(REPEAT('x',16777215),256)),REPEAT('x',16777215),%s,'untrusted-key')", (candidate_id, digest))
                with pytest.raises((DataError, IntegrityError, OperationalError)):
                    await cur.execute("INSERT INTO office_module_candidate_revision (candidate_id,revision_no,manifest_sha256,manifest_bytes,claimed_artifact_set_digest,claimed_signing_key_id) VALUES (%s,2,UNHEX(SHA2(REPEAT('x',16777216),256)),REPEAT('x',16777216),%s,'untrusted-key')", (candidate_id, digest))
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)
