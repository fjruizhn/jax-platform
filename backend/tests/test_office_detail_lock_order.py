"""Lock-prefix regressions for project detail and invitation management."""
from __future__ import annotations

import asyncio
import uuid

import aiomysql
import pytest

from api.virtual_office.projects import _project_detail_in_cursor
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.test_office_invitation_adversarial import _CONFIG, _MFA, _project_for
from tests.test_office_projects_api import _office_root_with_current_proof
from virtual_office.invitations import (
    InvitationError,
    _issuer_snapshot,
    issue_invitation_in_cursor, list_active_invitations_in_cursor,
    lock_office_actor_project_tenants_in_cursor,
)
from virtual_office import invitations as invitation_service


class _TraceCursor:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []
        self.rows = iter(((2,), (1,), (1,), (2,)))

    async def execute(self, statement, params=()):
        self.statements.append((statement, params))

    async def fetchone(self):
        return next(self.rows)


def test_shared_tenant_prefix_locks_ascending_before_any_actor_or_scope_lock():
    trace = _TraceCursor()
    asyncio.run(lock_office_actor_project_tenants_in_cursor(
        trace, actor_user_id=7, project_id=12,
    ))
    updates = [(statement, params) for statement, params in trace.statements if "FOR UPDATE" in statement]
    assert updates == [
        ("SELECT tenant_id FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (1,)),
        ("SELECT tenant_id FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (2,)),
    ]
    # The two discovery reads establish 2 and 1; sorted locking makes the
    # actual parameter order deterministic even for cross-tenant work.
    assert trace.statements[:2] == [
        ("SELECT tenant_id FROM jax_users WHERE user_id=%s", (7,)),
        ("SELECT tenant_id FROM jax_project_scope WHERE project_id=%s", (12,)),
    ]


def test_issuer_reauthorizes_after_tenant_prefix_instead_of_trusting_discovery(monkeypatch):
    trace = _TraceCursor()
    calls: list[str] = []

    class _Denied:
        allowed = False

    async def definitive_denial(*_args, **_kwargs):
        calls.append("authorize")
        return _Denied()

    monkeypatch.setattr(invitation_service, "authorize_office_in_cursor", definitive_denial)
    with pytest.raises(InvitationError) as denied:
        asyncio.run(_issuer_snapshot(
            trace, actor_user_id=7, project_id=12,
            proof_event_id=9, mfa_config=object(),
        ))
    assert denied.value.status_code == 403
    assert calls == ["authorize"]
    assert [params for statement, params in trace.statements if "FOR UPDATE" in statement] == [(1,), (2,)]


def test_detail_list_and_issue_share_lock_prefix_without_transaction_contention(client):
    """Exercise the three paths concurrently against the real MariaDB lock manager."""
    actor, _token_version, _mfa_version, proof = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            project_id = await _project_for(
                cur, actor=actor, tenant_id=1,
                label="detail-list-lock-order",
            )

        async def detail():
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await _project_detail_in_cursor(
                    cur, actor_user_id=actor, project_id=project_id,
                    proof_event_id=proof, mfa_config=_MFA,
                )

        async def listing():
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await list_active_invitations_in_cursor(
                    cur, actor_user_id=actor, project_id=project_id,
                    proof_event_id=proof, mfa_config=_MFA, limit=25, cursor=None,
                )

        async def issue():
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                return await issue_invitation_in_cursor(
                    cur, actor_user_id=actor, project_id=project_id,
                    email=f"lock-order-{uuid.uuid4().hex}@example.test", audience="INTERNAL",
                    proof_event_id=proof, mfa_config=_MFA,
                    invitation_config=_CONFIG, idempotency_key=str(uuid.uuid4()),
                )

        return await asyncio.wait_for(asyncio.gather(
            detail(), listing(), issue(), return_exceptions=True,
        ), timeout=10)

    outcomes = client.portal.call(scenario)
    assert not any(
        isinstance(value, aiomysql.OperationalError) and value.args[0] in {1205, 1213}
        for value in outcomes
    )
    assert not any(isinstance(value, Exception) for value in outcomes)
    assert outcomes[0]["capabilities"]["manage_invitations"] is True
