from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import sys
import uuid
import aiomysql
import pytest
from pymysql.err import OperationalError
from db_connect_config import db_connect_timeout_seconds

spec = importlib.util.spec_from_file_location("release_inert_v2", Path(__file__).parents[1] / "virtual_office" / "module_release_inert_v2.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)

def valid():
    return {"domain":"axioma.office.module-release-receipt/v1","module_package_digest":"sha256:"+"11"*32,"project_id":7,"tenant_id":8,"creative_approval_id":9,"rights_verdict_id":10,"office_release_signer":"office-release:primary"}

def test_v2_separates_package_client_and_authorities_while_staying_pending():
    source = "\n".join(module.DDL)
    assert "module_package_digest" in source and "client_artifact_set_digest" not in source
    assert "evidence_set_sha256" in source and "mfa_proof_event_id BIGINT UNSIGNED NULL" in source
    assert source.count("PENDING_CONTRACT") >= 6
    assert "PUBLISHED" not in source
    assert module.FORBIDDEN_RUNTIME_TABLES == frozenset(module.TABLES)

def test_receipt_binds_distinct_authorities():
    receipt = module.validate_release_receipt(valid(), module_package_digest="sha256:"+"11"*32, project_id=7, tenant_id=8, human_approval_id=9, critic_rights_id=10, human_principal="human:fernando")
    assert receipt.office_release_signer == "office-release:primary"

@pytest.mark.parametrize("key,value", [("module_package_digest","sha256:"+"00"*32),("office_release_signer","human:fernando"),("domain","module-package/v1")])
def test_receipt_rejects_misbinding(key, value):
    item = valid(); item[key] = value
    with pytest.raises(module.ReleaseReceiptRejected): module.validate_release_receipt(item, module_package_digest="sha256:"+"11"*32, project_id=7, tenant_id=8, human_approval_id=9, critic_rights_id=10, human_principal="human:fernando")

def test_mariadb_v2_is_resumable_append_only_and_mfa_optional(client):
    async def scenario():
        source = os.environ["JAX_DB_NAME"]; schema = f"office_release_v2_{uuid.uuid4().hex[:12]}"
        conn = await aiomysql.connect(host=os.environ["JAX_DB_HOST"],port=int(os.environ["JAX_DB_PORT"]),user=os.environ["JAX_DB_USER"],password=os.environ["JAX_DB_PASSWORD"],db=source,autocommit=True,charset="utf8mb4",connect_timeout=db_connect_timeout_seconds())
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4"); await cur.execute(f"USE `{schema}`")
                await cur.execute("CREATE TABLE jax_users (user_id INT PRIMARY KEY) ENGINE=InnoDB"); await cur.execute("INSERT INTO jax_users VALUES (7)")
                await cur.execute("CREATE TABLE office_mfa_security_event (event_id BIGINT UNSIGNED PRIMARY KEY) ENGINE=InnoDB")
                await cur.execute("CREATE TABLE jax_project_scope (project_id INT NOT NULL,tenant_id INT NOT NULL,PRIMARY KEY(project_id,tenant_id)) ENGINE=InnoDB"); await cur.execute("INSERT INTO jax_project_scope VALUES (11,12)")
                await cur.execute("CREATE TABLE office_module_candidate (candidate_id BIGINT UNSIGNED PRIMARY KEY) ENGINE=InnoDB")
                await cur.execute("CREATE TABLE office_module_candidate_revision (candidate_revision_id BIGINT UNSIGNED NOT NULL,candidate_id BIGINT UNSIGNED NOT NULL,manifest_sha256 BINARY(32) NOT NULL,claimed_artifact_set_digest BINARY(32) NOT NULL,PRIMARY KEY(candidate_revision_id,candidate_id,manifest_sha256,claimed_artifact_set_digest)) ENGINE=InnoDB")
                await cur.execute("INSERT INTO office_module_candidate VALUES (1)"); await cur.execute("INSERT INTO office_module_candidate_revision VALUES (2,1,%s,%s)",(bytes.fromhex("33"*32),bytes.fromhex("22"*32)))
                await module.apply_inert_release_v2_migrations(cur); await module.apply_inert_release_v2_migrations(cur)
                await cur.execute("INSERT INTO office_module_artifact_snapshot_v2 (candidate_revision_id,candidate_id,manifest_sha256,module_package_digest,project_id,tenant_id) VALUES (2,1,%s,%s,11,12)",(bytes.fromhex("33"*32),bytes.fromhex("22"*32)))
                snapshot = cur.lastrowid
                await cur.execute("INSERT INTO office_module_publication_request_v2 (snapshot_id,project_id,tenant_id,requested_by_user_id,mfa_proof_event_id,state) VALUES (%s,11,12,7,NULL,'PENDING_CONTRACT')",(snapshot,))
                with pytest.raises(OperationalError, match="append-only"):
                    await cur.execute("UPDATE office_module_publication_request_v2 SET state='PENDING_CONTRACT' WHERE publication_request_id=1")
                await module.assert_inert_release_v2_metadata(cur)
        finally:
            async with conn.cursor() as cur: await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)
