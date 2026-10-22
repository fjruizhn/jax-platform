"""The first publication schema cut is durable but cannot publish."""
from __future__ import annotations

import os
import uuid

import aiomysql
import pytest
from pymysql.err import IntegrityError, OperationalError

from db_connect_config import db_connect_timeout_seconds
from virtual_office import migrations
from virtual_office import module_release_ledger as candidate


def test_staging_has_three_distinct_authorities_and_no_effective_release() -> None:
    source = "\n".join(migrations.OFFICE_MODULE_PUBLICATION_STAGING_DDL)
    assert "human_user_id" in source
    assert "critic_invocation_ref" in source
    assert "canonical_receipt_signature" in source
    assert "status='PENDING_CONTRACT'" in source
    assert "decision='PENDING_CONTRACT'" in source
    assert "verification_state='PENDING_CONTRACT'" in source
    assert "PUBLISHED" not in source


def test_runtime_acl_forbids_every_append_only_publication_event() -> None:
    from db import runtime_privileges
    events = set(migrations._MODULE_PUBLICATION_STAGING_EVENT_TABLES)
    assert events <= runtime_privileges._FORBIDDEN_TABLES
    assert not (events & set(runtime_privileges.runtime_table_privilege_map()))


def test_mariadb_staging_is_bound_append_only_and_pending_contract(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_publication_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(
            host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
            user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source,
            autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds(),
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await cur.execute("CREATE TABLE jax_users (user_id INT NOT NULL PRIMARY KEY) ENGINE=InnoDB")
                await cur.execute("CREATE TABLE office_mfa_security_event (event_id BIGINT UNSIGNED NOT NULL PRIMARY KEY) ENGINE=InnoDB")
                await cur.execute("INSERT INTO jax_users VALUES (7)")
                await cur.execute("INSERT INTO office_mfa_security_event VALUES (9)")
                await candidate.apply_module_candidate_storage_migrations(cur)
                await migrations.apply_module_publication_staging_migrations(cur)
                await migrations.apply_module_publication_staging_migrations(cur)
                await cur.execute("INSERT INTO office_module_candidate (module_id,module_version) VALUES ('studio','1')")
                candidate_id = cur.lastrowid
                manifest, digest = b'{}', bytes.fromhex('11' * 32)
                await cur.execute("INSERT INTO office_module_candidate_revision (candidate_id,revision_no,manifest_sha256,manifest_bytes,claimed_artifact_set_digest,claimed_signing_key_id) VALUES (%s,1,UNHEX(SHA2(%s,256)),%s,%s,'pending-key')", (candidate_id, manifest, manifest, digest))
                revision_id = cur.lastrowid
                await cur.execute("INSERT INTO office_module_creative_review_event (candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest,human_user_id,mfa_proof_event_id,decision) VALUES (%s,%s,UNHEX(SHA2(%s,256)),%s,7,9,'PENDING_CONTRACT')", (revision_id, candidate_id, manifest, digest))
                creative_id = cur.lastrowid
                with pytest.raises((IntegrityError, OperationalError)):
                    await cur.execute("INSERT INTO office_module_creative_review_event (candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest,human_user_id,mfa_proof_event_id,decision) VALUES (%s,%s,UNHEX(SHA2(%s,256)),%s,7,9,'APPROVED')", (revision_id, candidate_id, manifest, digest))
                await cur.execute("INSERT INTO office_module_rights_review_event (candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest,critic_invocation_ref,verification_state,decision) VALUES (%s,%s,UNHEX(SHA2(%s,256)),%s,'invocation:pending','PENDING_CONTRACT','PENDING_CONTRACT')", (revision_id, candidate_id, manifest, digest))
                rights_id = cur.lastrowid
                await cur.execute("INSERT INTO office_module_release_event (candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest,creative_review_event_id,rights_review_event_id,decision_state) VALUES (%s,%s,UNHEX(SHA2(%s,256)),%s,%s,%s,'PENDING_CONTRACT')", (revision_id, candidate_id, manifest, digest, creative_id, rights_id))
                release_id = cur.lastrowid
                await cur.execute("INSERT INTO office_module_release (candidate_id,head_version,release_event_id,candidate_revision_id,manifest_sha256,artifact_set_digest,status) VALUES (%s,1,%s,%s,UNHEX(SHA2(%s,256)),%s,'PENDING_CONTRACT')", (candidate_id, release_id, revision_id, manifest, digest))
                with pytest.raises((IntegrityError, OperationalError)):
                    await cur.execute("UPDATE office_module_release_event SET decision_state='PUBLISHED' WHERE release_event_id=%s", (release_id,))
                with pytest.raises((IntegrityError, OperationalError)):
                    await cur.execute("INSERT INTO office_module_release (candidate_id,head_version,release_event_id,candidate_revision_id,manifest_sha256,artifact_set_digest,status) VALUES (%s,2,%s,%s,UNHEX(SHA2(%s,256)),%s,'PUBLISHED')", (candidate_id, release_id, revision_id, manifest, digest))
                with pytest.raises((IntegrityError, OperationalError)):
                    await cur.execute("INSERT INTO office_module_rights_review_event (candidate_revision_id,candidate_id,manifest_sha256,artifact_set_digest,critic_invocation_ref,verification_state,decision) VALUES (%s,%s,UNHEX(SHA2(%s,256)),%s,'untrusted','VERIFIED','PASS')", (revision_id, candidate_id, manifest, digest))
                await cur.execute("EXPLAIN SELECT * FROM office_module_release_event WHERE candidate_id=%s AND candidate_revision_id=%s ORDER BY occurred_at", (candidate_id, revision_id))
                plan = await cur.fetchall()
                assert plan and plan[0][5] == "idx_office_module_release_event_candidate"
                await migrations.assert_module_publication_staging_metadata(cur)
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)


def test_mariadb_rejects_malformed_existing_release_head_before_candidate_replay(client) -> None:
    async def scenario() -> None:
        source = os.environ["JAX_DB_NAME"]
        schema = f"office_module_publication_bad_{uuid.uuid4().hex[:16]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db=source, autocommit=True, charset="utf8mb4", connect_timeout=db_connect_timeout_seconds())
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4")
                await cur.execute(f"USE `{schema}`")
                await cur.execute("CREATE TABLE office_module_release (candidate_id BIGINT UNSIGNED NOT NULL,head_version BIGINT UNSIGNED NOT NULL,release_event_id BIGINT UNSIGNED NOT NULL,status VARCHAR(24) NOT NULL,PRIMARY KEY(candidate_id)) ENGINE=InnoDB")
                with pytest.raises(RuntimeError, match="explicit data-preserving migration"):
                    await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("DROP TABLE office_module_release")
                await cur.execute("CREATE TABLE jax_users (user_id INT NOT NULL PRIMARY KEY) ENGINE=InnoDB")
                await cur.execute("CREATE TABLE office_mfa_security_event (event_id BIGINT UNSIGNED NOT NULL PRIMARY KEY) ENGINE=InnoDB")
                await candidate.apply_module_candidate_storage_migrations(cur)
                await cur.execute("CREATE TABLE office_module_release (candidate_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,head_version BIGINT UNSIGNED NOT NULL,release_event_id BIGINT UNSIGNED NOT NULL,candidate_revision_id BIGINT UNSIGNED NOT NULL,manifest_sha256 BINARY(32) NOT NULL,artifact_set_digest BINARY(32) NOT NULL,status VARCHAR(24) NOT NULL) ENGINE=InnoDB")
                with pytest.raises(RuntimeError, match="columns|indexes|foreign keys|checks"):
                    await migrations.apply_module_publication_staging_migrations(cur)
        finally:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)
