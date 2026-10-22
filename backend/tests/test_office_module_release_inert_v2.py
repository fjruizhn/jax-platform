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
    return {"domain":"axioma.office.module-release-receipt/v1","module_package_digest":"sha256:"+"11"*32,"project_id":7,"tenant_id":8,"creative_approval_id":9,"rights_verdict_id":10,"critic_principal":"critic:primary","office_release_signer":"office-release:primary"}

def test_v2_separates_package_client_and_authorities_while_staying_pending():
    source = "\n".join(module.DDL)
    assert "module_package_digest" in source and "client_artifact_set_digest" not in source
    assert "evidence_set_sha256" in source and "mfa_proof_event_id BIGINT UNSIGNED NULL" in source
    assert source.count("PENDING_CONTRACT") >= 6
    assert "PUBLISHED" not in source
    assert module.FORBIDDEN_RUNTIME_TABLES == frozenset((*module.TABLES, module.STAMP_TABLE))

def test_v2_uses_a_single_composite_scope_module_chain():
    edges = module._EXPECTED_FK_EDGES
    assert edges[("office_module_artifact_snapshot_v2", "fk_office_module_snapshot_v2_candidate_scope")] == (
        ("candidate_id", "project_id", "tenant_id", "module_id", "module_version"),
        "office_module_candidate",
        ("candidate_id", "origin_project_id", "origin_tenant_id", "module_id", "module_version"),
    )
    for table, fk in (
        ("office_module_publication_request_v2", "fk_office_module_request_v2_snapshot"),
        ("office_module_creative_approval_v2", "fk_office_module_creative_approval_v2_request"),
        ("office_module_rights_verdict_v2", "fk_office_module_rights_verdict_v2_request"),
        ("office_module_release_operation_v2", "fk_office_module_release_operation_v2_request"),
        ("office_module_release_receipt_v2", "fk_office_module_release_receipt_v2_operation"),
        ("office_module_effective_release_head_v2", "fk_office_module_effective_head_v2_receipt"),
    ):
        local, _, remote = edges[(table, fk)]
        assert local[-4:] == ("project_id", "tenant_id", "module_id", "module_version")
        assert remote[-4:] == ("project_id", "tenant_id", "module_id", "module_version")

def test_receipt_binds_distinct_authorities():
    receipt = module.validate_release_receipt(valid(), module_package_digest="sha256:"+"11"*32, project_id=7, tenant_id=8, human_approval_id=9, critic_rights_id=10, human_principal="human:fernando")
    assert receipt.office_release_signer == "office-release:primary"

@pytest.mark.parametrize("key,value", [("module_package_digest","sha256:"+"00"*32),("office_release_signer","human:fernando"),("office_release_signer","office-release:"),("critic_principal","human:fernando"),("project_id",True),("domain","module-package/v1")])
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
                await cur.execute("CREATE TABLE office_module_candidate (candidate_id BIGINT UNSIGNED PRIMARY KEY,module_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,module_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,origin_project_id INT NOT NULL,origin_tenant_id INT NOT NULL,UNIQUE KEY uq_office_module_candidate_release_scope (candidate_id,origin_project_id,origin_tenant_id,module_id,module_version)) ENGINE=InnoDB")
                await cur.execute("CREATE TABLE office_module_candidate_revision (candidate_revision_id BIGINT UNSIGNED NOT NULL,candidate_id BIGINT UNSIGNED NOT NULL,manifest_sha256 BINARY(32) NOT NULL,claimed_artifact_set_digest BINARY(32) NOT NULL,PRIMARY KEY(candidate_revision_id,candidate_id,manifest_sha256,claimed_artifact_set_digest)) ENGINE=InnoDB")
                await cur.execute("INSERT INTO office_module_candidate VALUES (1,'studio','1.0.0',11,12)"); await cur.execute("INSERT INTO office_module_candidate_revision VALUES (2,1,%s,%s)",(bytes.fromhex("33"*32),bytes.fromhex("22"*32)))
                await module.apply_inert_release_v2_migrations(cur); await module.apply_inert_release_v2_migrations(cur)
                await cur.execute("INSERT INTO office_module_artifact_snapshot_v2 (candidate_revision_id,candidate_id,manifest_sha256,module_package_digest,project_id,tenant_id,module_id,module_version) VALUES (2,1,%s,%s,11,12,'studio','1.0.0')",(bytes.fromhex("33"*32),bytes.fromhex("22"*32)))
                snapshot = cur.lastrowid
                with pytest.raises(IntegrityError):
                    await cur.execute("INSERT INTO office_module_artifact_snapshot_v2 (candidate_revision_id,candidate_id,manifest_sha256,module_package_digest,project_id,tenant_id,module_id,module_version) VALUES (2,1,%s,%s,21,22,'studio','1.0.0')",(bytes.fromhex("33"*32),bytes.fromhex("22"*32)))
                with pytest.raises(IntegrityError):
                    await cur.execute("INSERT INTO office_module_artifact_snapshot_v2 (candidate_revision_id,candidate_id,manifest_sha256,module_package_digest,project_id,tenant_id,module_id,module_version) VALUES (2,1,%s,%s,11,12,'studio','2.0.0')",(bytes.fromhex("33"*32),bytes.fromhex("22"*32)))
                await cur.execute("INSERT INTO office_module_publication_request_v2 (snapshot_id,project_id,tenant_id,module_id,module_version,requested_by_user_id,mfa_proof_event_id,state) VALUES (%s,11,12,'studio','1.0.0',7,NULL,'PENDING_CONTRACT')",(snapshot,))
                request = cur.lastrowid
                with pytest.raises(IntegrityError):
                    await cur.execute("INSERT INTO office_module_publication_request_v2 (snapshot_id,project_id,tenant_id,module_id,module_version,requested_by_user_id,state) VALUES (%s,11,12,'studio','2.0.0',7,'PENDING_CONTRACT')",(snapshot,))
                await cur.execute("INSERT INTO office_module_creative_approval_v2 (publication_request_id,project_id,tenant_id,module_id,module_version,human_user_id,decision) VALUES (%s,11,12,'studio','1.0.0',7,'PENDING_CONTRACT')", (request,))
                approval = cur.lastrowid
                await cur.execute("INSERT INTO office_module_rights_verdict_v2 (publication_request_id,project_id,tenant_id,module_id,module_version,critic_principal,evidence_set_sha256,verification_state,decision) VALUES (%s,11,12,'studio','1.0.0','critic:one',%s,'PENDING_CONTRACT','PENDING_CONTRACT')", (request, bytes.fromhex("44" * 32)))
                verdict = cur.lastrowid
                await cur.execute("INSERT INTO office_module_release_operation_v2 (publication_request_id,project_id,tenant_id,module_id,module_version,creative_approval_id,rights_verdict_id,office_release_signer,state) VALUES (%s,11,12,'studio','1.0.0',%s,%s,'office-release:one','PENDING_CONTRACT')", (request, approval, verdict))
                operation = cur.lastrowid
                await cur.execute("INSERT INTO office_module_release_receipt_v2 (release_operation_id,project_id,tenant_id,module_id,module_version,state) VALUES (%s,11,12,'studio','1.0.0','PENDING_CONTRACT')", (operation,))
                receipt = cur.lastrowid
                await cur.execute("INSERT INTO office_module_effective_release_head_v2 (project_id,tenant_id,module_id,module_version,release_receipt_id,status) VALUES (11,12,'studio','1.0.0',%s,'PENDING_CONTRACT')", (receipt,))
                with pytest.raises(IntegrityError):
                    await cur.execute("INSERT INTO office_module_effective_release_head_v2 (project_id,tenant_id,module_id,module_version,release_receipt_id,status) VALUES (11,12,'studio','2.0.0',%s,'PENDING_CONTRACT')", (receipt,))
                await cur.execute("INSERT INTO jax_project_scope VALUES (21,22)")
                await cur.execute("INSERT INTO office_module_candidate VALUES (3,'studio-other','1.0.0',21,22)")
                await cur.execute("INSERT INTO office_module_candidate_revision VALUES (4,3,%s,%s)", (bytes.fromhex("55" * 32), bytes.fromhex("66" * 32)))
                await cur.execute("INSERT INTO office_module_artifact_snapshot_v2 (candidate_revision_id,candidate_id,manifest_sha256,module_package_digest,project_id,tenant_id,module_id,module_version) VALUES (4,3,%s,%s,21,22,'studio-other','1.0.0')", (bytes.fromhex("55" * 32), bytes.fromhex("66" * 32)))
                other_snapshot = cur.lastrowid
                await cur.execute("INSERT INTO office_module_publication_request_v2 (snapshot_id,project_id,tenant_id,module_id,module_version,requested_by_user_id,state) VALUES (%s,21,22,'studio-other','1.0.0',7,'PENDING_CONTRACT')", (other_snapshot,))
                other_request = cur.lastrowid
                await cur.execute("INSERT INTO office_module_creative_approval_v2 (publication_request_id,project_id,tenant_id,module_id,module_version,human_user_id,decision) VALUES (%s,21,22,'studio-other','1.0.0',7,'PENDING_CONTRACT')", (other_request,))
                other_approval = cur.lastrowid
                await cur.execute("INSERT INTO office_module_rights_verdict_v2 (publication_request_id,project_id,tenant_id,module_id,module_version,critic_principal,evidence_set_sha256,verification_state,decision) VALUES (%s,21,22,'studio-other','1.0.0','critic:two',%s,'PENDING_CONTRACT','PENDING_CONTRACT')", (other_request, bytes.fromhex("77" * 32)))
                other_verdict = cur.lastrowid
                with pytest.raises(IntegrityError):
                    await cur.execute("INSERT INTO office_module_release_operation_v2 (publication_request_id,project_id,tenant_id,module_id,module_version,creative_approval_id,rights_verdict_id,office_release_signer,state) VALUES (%s,11,12,'studio','1.0.0',%s,%s,'office-release:one','PENDING_CONTRACT')", (request, approval, other_verdict))
                with pytest.raises(IntegrityError):
                    await cur.execute("INSERT INTO office_module_release_operation_v2 (publication_request_id,project_id,tenant_id,module_id,module_version,creative_approval_id,rights_verdict_id,office_release_signer,state) VALUES (%s,11,12,'studio','1.0.0',%s,%s,'office-release:one','PENDING_CONTRACT')", (request, other_approval, verdict))
                with pytest.raises(OperationalError, match="append-only"):
                    await cur.execute("UPDATE office_module_publication_request_v2 SET state='PENDING_CONTRACT' WHERE publication_request_id=1")
                await module.assert_inert_release_v2_metadata(cur)
        finally:
            async with conn.cursor() as cur: await cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
            conn.close()
    client.portal.call(scenario)
