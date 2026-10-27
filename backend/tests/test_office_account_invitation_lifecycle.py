"""MariaDB acceptance for human Office accounts, independent of projects."""
from __future__ import annotations

import uuid

from db.seed import _hash
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.identidades import sql
from tests.test_office_people_api import _root_with_proof
from virtual_office.account_invitations import (
    _decode_payload, accept_account_invitation_in_cursor,
    issue_account_invitation_in_cursor, revoke_account_invitation_in_cursor,
)
from virtual_office.invitation_config import InvitationConfig


def test_human_invitation_creates_person_without_project_access(client):
    root, _tv, _mv, _proof = client.portal.call(_root_with_proof)
    email = f"office-person-{uuid.uuid4().hex}@example.test"
    config = InvitationConfig(120, 30, 10, 10, "https://virtualoffice.example.test", 1)
    password_hash = _hash("correct-horse-battery-office-9271")

    async def scenario():
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            issued = await issue_account_invitation_in_cursor(
                cur, actor_user_id=root, email=email,
                idempotency_key=str(uuid.uuid4()), config=config)
        payload = (await sql(
            "SELECT payload_ciphertext FROM office_account_invitation_outbox WHERE invitation_id=%s",
            (issued.invitation_id,), True))[0][0]
        _recipient, bearer, _expiry = _decode_payload(payload)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            replayed = await accept_account_invitation_in_cursor(
                cur, accepting_user_id=None, accepting_email=None,
                new_password_hash=password_hash, bearer_token=bearer)
        account = (await sql(
            "SELECT u.user_id,u.account_class,p.global_role,p.status "
            "FROM jax_users u JOIN office_account_profile p ON p.user_id=u.user_id "
            "WHERE u.email=%s", (email,), True))[0]
        memberships = (await sql(
            "SELECT COUNT(*) FROM jax_project_membership WHERE user_id=%s", (account[0],), True))[0][0]
        grants = (await sql(
            "SELECT COUNT(*) FROM office_project_grant WHERE user_id=%s", (account[0],), True))[0][0]
        return replayed, account, memberships, grants

    replayed, account, memberships, grants = client.portal.call(scenario)
    assert replayed is False
    assert account[1:] == ("OFFICE_INTERNAL", "COLLABORATOR", "ACTIVE")
    assert memberships == grants == 0


def test_human_invitation_revocation_preserves_no_account(client):
    root, _tv, _mv, _proof = client.portal.call(_root_with_proof)
    email = f"office-revoked-{uuid.uuid4().hex}@example.test"
    config = InvitationConfig(120, 30, 10, 10, "https://virtualoffice.example.test", 1)

    async def scenario():
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            issued = await issue_account_invitation_in_cursor(
                cur, actor_user_id=root, email=email,
                idempotency_key=str(uuid.uuid4()), config=config)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            version, replayed = await revoke_account_invitation_in_cursor(
                cur, actor_user_id=root, invitation_id=issued.invitation_id,
                expected_version=1, idempotency_key=str(uuid.uuid4()))
        invitation = (await sql(
            "SELECT status,version FROM office_account_invitation WHERE invitation_id=%s",
            (issued.invitation_id,), True))[0]
        account_count = (await sql(
            "SELECT COUNT(*) FROM jax_users WHERE email=%s", (email,), True))[0][0]
        return version, replayed, invitation, account_count

    version, replayed, invitation, account_count = client.portal.call(scenario)
    assert (version, replayed, invitation, account_count) == (2, False, ("REVOKED", 2), 0)
