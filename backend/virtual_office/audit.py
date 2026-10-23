from __future__ import annotations

from .models import OfficeDecision


async def record_authorization_event(cur, decision: OfficeDecision, *, correlation_id: str | None = None) -> int:
    """Append a decision using only canonical IDs resolved by authority.

    An unknown actor has no canonical authorization subject and therefore is
    deliberately not written as an AUTHORIZATION event. Future ingress may
    record such attempts in a separate untrusted-attempt stream.
    """
    if decision.code == "office_actor_missing":
        raise ValueError("unknown Office actor cannot be recorded as AUTHORIZATION")
    await cur.execute(
        "INSERT INTO office_authorization_event "
        "(actor_user_id,actor_principal,event_kind,fence_key,operation,requested_project_id,project_id,tenant_id,result,reason_code,correlation_id,occurred_at) "
        "VALUES (%s,NULL,'AUTHORIZATION',NULL,%s,%s,%s,%s,%s,%s,%s,NOW(6))",
        (decision.actor_user_id, decision.operation, decision.requested_project_id, decision.project_id, decision.tenant_id,
         "ALLOWED" if decision.allowed else "DENIED", decision.code, correlation_id),
    )
    return int(cur.lastrowid)
