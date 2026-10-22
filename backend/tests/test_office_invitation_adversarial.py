"""Adversarial concurrency and deadline regressions for Office invitations."""
from __future__ import annotations

import asyncio
from functools import partial
import datetime as dt
import uuid

import aiomysql
import pytest

from db.seed import _hash
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.identidades import sql
from tests.test_office_projects_api import _office_root_with_current_proof
from virtual_office import invitations as invitation_service
from virtual_office.invitation_config import InvitationConfig as _InvitationConfig
from virtual_office.invitations import (
    InvitationError,
    _decode_outbox_payload,
    _expire_if_needed,
    accept_invitation_in_cursor,
    issue_invitation_in_cursor,
    revoke_invitation_in_cursor,
)
from virtual_office.mfa import MFAConfig

InvitationConfig = partial(_InvitationConfig, public_base_url="https://virtualoffice.example.test", email_locale="es")


_MFA = MFAConfig(120, 3, 300, 300, 3)
_CONFIG = InvitationConfig(60, 30, 50, 50)


async def _project_for(cur, *, actor: int, tenant_id: int, label: str) -> int:
    await cur.execute(
        "INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')",
        (f"{label}-{uuid.uuid4().hex}",),
    )
    project_id = cur.lastrowid
    await cur.execute(
        "INSERT INTO jax_project_scope "
        "(project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
        "VALUES (%s,%s,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'test',UTC_TIMESTAMP(6))",
        (project_id, tenant_id),
    )
    await cur.execute(
        "INSERT INTO office_project_grant "
        "(project_id,user_id,grant_role,audience,status,granted_by) "
        "VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)",
        (project_id, actor, actor),
    )
    return int(project_id)


def test_cross_tenant_admins_invite_one_another_without_deadlock_or_binding(client, monkeypatch):
    """The issuer snapshot barrier must not expose an inverse tenant/user lock order."""
    first, _tv1, _mv1, first_proof = client.portal.call(_office_root_with_current_proof)
    second, _tv2, _mv2, second_proof = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        tenant2 = await sql(
            "INSERT INTO jax_tenants (name,plan,status) VALUES (%s,'test','active')",
            ("cross-invite-tenant-" + uuid.uuid4().hex,),
        )
        await sql("UPDATE jax_users SET tenant_id=%s WHERE user_id=%s", (tenant2, second))
        identities = await sql(
            "SELECT user_id,email,tenant_id FROM jax_users WHERE user_id IN (%s,%s)",
            (first, second), True,
        )
        by_id = {int(row[0]): row for row in identities}
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            left = await _project_for(cur, actor=first, tenant_id=1, label="cross-invite-left")
            right = await _project_for(cur, actor=second, tenant_id=int(tenant2), label="cross-invite-right")

        original = invitation_service._issuer_snapshot
        arrived = 0
        lock = asyncio.Lock()
        ready = asyncio.Event()

        async def barrier_after_snapshot(cur, **kwargs):
            nonlocal arrived
            result = await original(cur, **kwargs)
            async with lock:
                arrived += 1
                if arrived == 2:
                    ready.set()
            await ready.wait()
            return result

        monkeypatch.setattr(invitation_service, "_issuer_snapshot", barrier_after_snapshot)

        async def issue(actor, proof, project_id, email):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await issue_invitation_in_cursor(
                    cur, actor_user_id=actor, project_id=project_id, email=email,
                    audience="INTERNAL", proof_event_id=proof, mfa_config=_MFA,
                    invitation_config=_CONFIG, idempotency_key=str(uuid.uuid4()),
                )

        outcomes = await asyncio.wait_for(asyncio.gather(
            issue(first, first_proof, left, by_id[second][1]),
            issue(second, second_proof, right, by_id[first][1]),
            return_exceptions=True,
        ), timeout=10)
        successful = [outcome.invitation_id for outcome in outcomes if not isinstance(outcome, Exception)]
        targets = await sql(
            "SELECT target_user_id FROM office_invitation WHERE invitation_id IN (%s,%s) ORDER BY invitation_id",
            tuple(successful), True,
        ) if len(successful) == 2 else ()
        return outcomes, targets

    outcomes, targets = client.portal.call(scenario)
    assert not any(isinstance(value, aiomysql.OperationalError) and value.args[0] in {1205, 1213}
                   for value in outcomes)
    assert len([value for value in outcomes if not isinstance(value, Exception)]) == 2
    assert targets == ((None,), (None,))


def test_global_revoke_idempotency_key_has_one_receipt_and_stable_conflict(client):
    first, _tv1, _mv1, first_proof = client.portal.call(_office_root_with_current_proof)
    second, _tv2, _mv2, second_proof = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        work = []
        for actor, proof, label in ((first, first_proof, "left"), (second, second_proof, "right")):
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                project_id = await _project_for(cur, actor=actor, tenant_id=1, label="revoke-key-" + label)
                invitation = await issue_invitation_in_cursor(
                    cur, actor_user_id=actor, project_id=project_id,
                    email=f"revoke-key-{label}-{uuid.uuid4().hex}@example.test", audience="INTERNAL",
                    proof_event_id=proof, mfa_config=_MFA, invitation_config=_CONFIG,
                    idempotency_key=str(uuid.uuid4()),
                )
            work.append((actor, proof, project_id, invitation.invitation_id))
        key = str(uuid.uuid4())
        gate = asyncio.Event()
        arrivals = 0
        lock = asyncio.Lock()

        async def revoke(item):
            nonlocal arrivals
            async with lock:
                arrivals += 1
                if arrivals == 2:
                    gate.set()
            await gate.wait()
            actor, proof, project_id, invitation_id = item
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await revoke_invitation_in_cursor(
                    cur, actor_user_id=actor, project_id=project_id, invitation_id=invitation_id,
                    expected_version=1, proof_event_id=proof, mfa_config=_MFA, idempotency_key=key,
                )

        outcomes = await asyncio.gather(*(revoke(item) for item in work), return_exceptions=True)
        statuses = await sql(
            "SELECT status FROM office_invitation WHERE invitation_id IN (%s,%s) ORDER BY invitation_id",
            (work[0][3], work[1][3]), True,
        )
        receipts = await sql(
            "SELECT COUNT(*) FROM office_invitation_mutation_receipt WHERE idempotency_key=%s", (key,), True)
        return outcomes, statuses, receipts

    outcomes, statuses, receipts = client.portal.call(scenario)
    assert [value for value in outcomes if not isinstance(value, Exception)] == [(2, False)]
    failures = [value for value in outcomes if isinstance(value, Exception)]
    assert len(failures) == 1 and isinstance(failures[0], InvitationError)
    assert failures[0].code == "office_invitation_idempotency_conflict"
    assert sorted(row[0] for row in statuses) == ["ISSUED", "REVOKED"]
    assert receipts == ((1,),)


def test_acceptance_deadline_is_inclusive_and_rolls_back_identity_and_grant(client, monkeypatch):
    issuer, _tv, _mv, proof = client.portal.call(_office_root_with_current_proof)
    email = "deadline-" + uuid.uuid4().hex + "@example.test"

    async def scenario():
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            project_id = await _project_for(cur, actor=issuer, tenant_id=1, label="deadline")
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id, email=email, audience="INTERNAL",
                proof_event_id=proof, mfa_config=_MFA,
                invitation_config=InvitationConfig(1, 30, 50, 50), idempotency_key=str(uuid.uuid4()),
            )
        payload = (await sql("SELECT payload_ciphertext FROM office_invitation_outbox WHERE invitation_id=%s",
                             (invitation.invitation_id,), True))[0][0]
        bearer = _decode_outbox_payload(payload)[1]
        expiry = (await sql(
            "SELECT s.expires_at FROM office_invitation i JOIN office_invitation_issuance s "
            "ON s.issuance_id=i.issuance_id WHERE i.invitation_id=%s",
            (invitation.invitation_id,), True))[0][0]
        expiry = expiry.replace(tzinfo=dt.timezone.utc)
        # Make the database deadline true.  The first application clock read
        # below then simulates the last microsecond before it, while the SQL
        # guard observes the real deadline and must fail closed.
        await asyncio.sleep(1.1)
        original_now = invitation_service._db_now
        calls = 0

        async def first_before_then_real(cur):
            nonlocal calls
            calls += 1
            if calls == 1:
                return expiry - dt.timedelta(microseconds=1)
            return await original_now(cur)

        monkeypatch.setattr(invitation_service, "_db_now", first_before_then_real)
        with pytest.raises(InvitationError) as error:
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await accept_invitation_in_cursor(
                    cur, accepting_user_id=None, accepting_email=None,
                    new_password_hash=_hash("deadline-password"), bearer_token=bearer)
        state = await sql("SELECT status,accepted_user_id FROM office_invitation WHERE invitation_id=%s",
                          (invitation.invitation_id,), True)
        identities = await sql("SELECT COUNT(*) FROM jax_users WHERE email=%s", (email,), True)
        grants = await sql(
            "SELECT COUNT(*) FROM office_project_grant WHERE project_id=%s AND user_id IN "
            "(SELECT user_id FROM jax_users WHERE email=%s)", (project_id, email), True)
        return error.value, state, identities, grants

    error, state, identities, grants = client.portal.call(scenario)
    assert error.code == "office_invitation_expired" and error.status_code == 410
    assert state == (("ISSUED", None),)
    assert identities == ((0,),) and grants == ((0,),)


def test_expire_if_needed_expires_exactly_at_deadline(client):
    issuer, _tv, _mv, proof = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            project_id = await _project_for(cur, actor=issuer, tenant_id=1, label="expiry-equality")
            invitation = await issue_invitation_in_cursor(
                cur, actor_user_id=issuer, project_id=project_id,
                email="expiry-equality-" + uuid.uuid4().hex + "@example.test", audience="INTERNAL",
                    proof_event_id=proof, mfa_config=_MFA, invitation_config=InvitationConfig(1, 30, 50, 50),
                idempotency_key=str(uuid.uuid4()))
            await cur.execute(
                "SELECT e.event_id,s.correlation_id FROM office_invitation_event e "
                "JOIN office_invitation i ON i.invitation_id=e.invitation_id "
                "JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id "
                "WHERE e.invitation_id=%s AND e.event_kind='ISSUED' FOR UPDATE",
                (invitation.invitation_id,))
            issued_event, correlation_id = await cur.fetchone()
            await cur.execute(
                "SELECT s.expires_at FROM office_invitation i JOIN office_invitation_issuance s "
                "ON s.issuance_id=i.issuance_id WHERE i.invitation_id=%s", (invitation.invitation_id,))
            expires_at = (await cur.fetchone())[0].replace(tzinfo=dt.timezone.utc)
            await asyncio.sleep(1.1)
            original_now = invitation_service._db_now

            async def exactly_expiry(_cur):
                return expires_at

            # The storage guard still reads UTC_TIMESTAMP(6); it is already
            # past the same deadline, so this isolates the helper's inclusive
            # comparison from clock precision.
            invitation_service._db_now = exactly_expiry
            expired = await _expire_if_needed(
                cur, invitation_id=invitation.invitation_id, status="ISSUED", version=1,
                issued_event_id=int(issued_event), correlation_id=correlation_id,
                expires_at=expires_at)
            invitation_service._db_now = original_now
            await cur.execute("SELECT status FROM office_invitation WHERE invitation_id=%s", (invitation.invitation_id,))
            return expired, await cur.fetchone()
    assert client.portal.call(scenario) == (True, ("EXPIRED",))
