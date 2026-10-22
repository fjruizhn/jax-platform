"""Real MariaDB authority contract for the invitation delivery routines.

The schema-admin fixture seeds an issued invitation. Calls under test run as
the deliberately restricted ``jaxinvite_*`` account, proving the SQL boundary.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import uuid
from pathlib import Path

import aiomysql
import pytest

from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.identidades import sql
from tests.test_office_projects_api import _office_root_with_current_proof
from virtual_office.invitations import issue_invitation_in_cursor
from virtual_office.invitation_config import InvitationConfig
from virtual_office.mfa import MFAConfig


pytestmark = pytest.mark.skipif(os.getenv("JAX_DB_USER") != "root", reason="requires disposable root MariaDB")
_MFA_CONFIG = MFAConfig(120, 3, 300, 300, 3)
_INVITATION_CONFIG = InvitationConfig(3600, 60, 10, 10, "https://virtualoffice.example.test")
_ROUTINES = ("office_invitation_claim_next_v1", "office_invitation_expire_due_v1", "office_invitation_settle_delivery_v1")


def _root_mysql(payload: str) -> None:
    """Execute trusted setup SQL by stdin without exposing the root password."""
    subprocess.run(
        ["mysql", "--protocol=TCP", "-h", os.environ["JAX_DB_HOST"], "-P", os.environ["JAX_DB_PORT"], "-u", "root"],
        input=payload.encode(), env={**os.environ, "MYSQL_PWD": os.environ["JAX_DB_PASSWORD"]},
        check=True, capture_output=True,
    )


def _install_routines(worker: str) -> str:
    db = os.environ["JAX_DB_NAME"]
    sql_source = Path(__file__).parents[2] / "ops/virtualoffice/office-invitation-delivery-v1.sql"
    check = subprocess.run(
        ["mysql", "--protocol=TCP", "-N", "-B", "-h", os.environ["JAX_DB_HOST"], "-P", os.environ["JAX_DB_PORT"], "-u", "root", "-e", f"SELECT (SELECT COUNT(*) FROM mysql.user WHERE User='jaxoffice_invitation_owner' AND Host='localhost'), (SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='{db}' AND ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME IN ('office_invitation_claim_next_v1','office_invitation_expire_due_v1','office_invitation_settle_delivery_v1'))"],
        env={**os.environ, "MYSQL_PWD": os.environ["JAX_DB_PASSWORD"]}, check=True, capture_output=True, text=True,
    )
    assert check.stdout.strip() == "0\t0", "delivery routine fixture requires an unowned authority"
    host_check = subprocess.run(
        ["mysql", "--protocol=TCP", "-N", "-B", "-h", os.environ["JAX_DB_HOST"], "-P", os.environ["JAX_DB_PORT"], "-u", "root", "-e", "SELECT SUBSTRING_INDEX(USER(), '@', -1)"],
        env={**os.environ, "MYSQL_PWD": os.environ["JAX_DB_PASSWORD"]}, check=True, capture_output=True, text=True,
    )
    worker_host = host_check.stdout.strip()
    assert re.fullmatch(r"[A-Za-z0-9._:-]+", worker_host)
    grants = f"""
CREATE USER `jaxoffice_invitation_owner`@`localhost` ACCOUNT LOCK;
GRANT SELECT ON `{db}`.`office_invitation` TO `jaxoffice_invitation_owner`@`localhost`;
GRANT SELECT ON `{db}`.`office_invitation_issuance` TO `jaxoffice_invitation_owner`@`localhost`;
GRANT SELECT ON `{db}`.`office_invitation_event` TO `jaxoffice_invitation_owner`@`localhost`;
GRANT SELECT ON `{db}`.`office_invitation_outbox` TO `jaxoffice_invitation_owner`@`localhost`;
GRANT INSERT ON `{db}`.`office_invitation_event` TO `jaxoffice_invitation_owner`@`localhost`;
GRANT UPDATE (status,version) ON `{db}`.`office_invitation` TO `jaxoffice_invitation_owner`@`localhost`;
GRANT UPDATE (state,attempt_id,attempt_count,payload_ciphertext,message_id,result_code,claimed_at,finished_at,updated_at) ON `{db}`.`office_invitation_outbox` TO `jaxoffice_invitation_owner`@`localhost`;
CREATE USER `{worker}`@`{worker_host}` IDENTIFIED BY 'test-only-worker-password';
GRANT SELECT ON `{db}`.`axioma_config` TO `{worker}`@`{worker_host}`;
GRANT SELECT (outbox_id,invitation_id,state,attempt_id,claimed_at,created_at) ON `{db}`.`office_invitation_outbox` TO `{worker}`@`{worker_host}`;
"""
    worker_executes = "\n".join(f"GRANT EXECUTE ON PROCEDURE `{db}`.`{routine}` TO `{worker}`@`{worker_host}`;" for routine in _ROUTINES)
    owner_executes = "\n".join(f"GRANT EXECUTE ON PROCEDURE `{db}`.`{routine}` TO `jaxoffice_invitation_owner`@`localhost`;" for routine in _ROUTINES)
    _root_mysql(f"USE `{db}`;\n{grants}\n{sql_source.read_text()}\n{owner_executes}\n{worker_executes}\n")
    return worker_host


def _cleanup_routines(worker: str, worker_host: str) -> None:
    db = os.environ["JAX_DB_NAME"]
    cleanup = "\n".join(f"DROP PROCEDURE IF EXISTS `{routine}`;" for routine in _ROUTINES)
    cleanup += f"\nDROP USER IF EXISTS `{worker}`@`{worker_host}`;"
    cleanup += "\nDROP USER IF EXISTS `jaxoffice_invitation_owner`@`localhost`;"
    _root_mysql(f"USE `{db}`;\n{cleanup}\n")


async def _worker_connection(worker: str):
    return await aiomysql.connect(host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]), user=worker, password="test-only-worker-password", db=os.environ["JAX_DB_NAME"], autocommit=True)


async def _claim(worker: str) -> tuple[str, str | None, str | None]:
    connection = await _worker_connection(worker)
    try:
        async with connection.cursor() as cur:
            await cur.execute("CALL office_invitation_claim_next_v1(@d,@o,@a,@i,@p)")
            while await cur.nextset():
                pass
            await cur.execute("SELECT @d,@o,@a")
            disposition, outbox_id, attempt_id = await cur.fetchone()
            return str(disposition), outbox_id, attempt_id
    finally:
        connection.close()


async def _settle(worker: str, outbox_id: str, attempt_id: str) -> str:
    connection = await _worker_connection(worker)
    try:
        async with connection.cursor() as cur:
            await cur.execute("CALL office_invitation_settle_delivery_v1(%s,%s,'SMTP_ACCEPTED',%s,@d)", (outbox_id, attempt_id, "<routine-test@example.test>"))
            while await cur.nextset():
                pass
            await cur.execute("SELECT @d")
            return str((await cur.fetchone())[0])
    finally:
        connection.close()


async def _expire_due(worker: str) -> int:
    connection = await _worker_connection(worker)
    try:
        async with connection.cursor() as cur:
            await cur.execute("CALL office_invitation_expire_due_v1(1,@expired)")
            while await cur.nextset():
                pass
            await cur.execute("SELECT @expired")
            return int((await cur.fetchone())[0])
    finally:
        connection.close()


async def _seed_issued_invitation(issuer: int, proof_event_id: int, invitation_config: InvitationConfig = _INVITATION_CONFIG) -> str:
    project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("delivery-routine-" + uuid.uuid4().hex,))
    await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))", (project_id,))
    await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, issuer, issuer))
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        invitation = await issue_invitation_in_cursor(cur, actor_user_id=issuer, project_id=project_id, email=f"routine-{uuid.uuid4().hex}@example.test", audience="INTERNAL", proof_event_id=proof_event_id, mfa_config=_MFA_CONFIG, invitation_config=invitation_config, idempotency_key=str(uuid.uuid4()))
    return invitation.invitation_id


def test_restricted_delivery_routines_claim_settle_and_fence_replays(client):
    """One queue item gains one lease; only the routine can complete it."""
    worker = "jaxinvite_" + uuid.uuid4().hex[:16]
    worker_host = _install_routines(worker)
    try:
        issuer, _tv, _mv, proof_event_id = client.portal.call(_office_root_with_current_proof)
        invitation_id = client.portal.call(_seed_issued_invitation, issuer, proof_event_id)
        claims = client.portal.call(lambda: asyncio.gather(_claim(worker), _claim(worker)))
        claimed = [claim for claim in claims if claim[0] == "CLAIMED"]
        assert len(claimed) == 1
        assert {claim[0] for claim in claims} <= {"CLAIMED", "EMPTY", "RETRY"}
        _disposition, outbox_id, attempt_id = claimed[0]
        assert outbox_id and attempt_id
        assert client.portal.call(_settle, worker, outbox_id, attempt_id) == "APPLIED"
        assert client.portal.call(_settle, worker, outbox_id, attempt_id) == "REPLAYED"
        assert client.portal.call(sql, "SELECT state,payload_ciphertext,result_code FROM office_invitation_outbox WHERE invitation_id=%s", (invitation_id,), True) == (("SMTP_ACCEPTED", None, "SMTP_ACCEPTED"),)
        assert client.portal.call(sql, "SELECT event_kind FROM office_invitation_event WHERE invitation_id=%s ORDER BY event_id", (invitation_id,), True) == (("ISSUED",), ("DELIVERY_OBSERVED",))

        async def direct_lifecycle_write_is_denied():
            connection = await _worker_connection(worker)
            try:
                async with connection.cursor() as cur:
                    try:
                        await cur.execute("UPDATE office_invitation SET status='EXPIRED' WHERE invitation_id=%s", (invitation_id,))
                    except aiomysql.OperationalError as error:
                        return error.args[0]
                    return None
            finally:
                connection.close()

        assert client.portal.call(direct_lifecycle_write_is_denied) == 1142

        expiry_config = InvitationConfig(1, 60, 10, 10, "https://virtualoffice.example.test")
        expired_invitation = client.portal.call(_seed_issued_invitation, issuer, proof_event_id, expiry_config)
        client.portal.call(asyncio.sleep, 1.1)
        for _ in range(10):
            assert client.portal.call(_expire_due, worker) in (0, 1)
            if client.portal.call(sql, "SELECT status FROM office_invitation WHERE invitation_id=%s", (expired_invitation,), True) == (("EXPIRED",),):
                break
        else:
            pytest.fail("bounded expiry sweep did not reach its own invitation")
        assert client.portal.call(
            sql,
            "SELECT event_kind FROM office_invitation_event WHERE invitation_id=%s ORDER BY event_id",
            (expired_invitation,),
            True,
        ) == (("ISSUED",), ("EXPIRED",))
    finally:
        _cleanup_routines(worker, worker_host)
