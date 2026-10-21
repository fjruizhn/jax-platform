from __future__ import annotations

from .models import OfficeDecision


async def record_authorization_event(cur, decision: OfficeDecision, *, correlation_id: str | None = None) -> None:
    """Append one non-secret authorization outcome in the caller transaction."""
    await cur.execute(
        "INSERT INTO office_authorization_event "
        "(actor_user_id,operation,project_id,tenant_id,result,reason_code,correlation_id,occurred_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(6))",
        (decision.actor_user_id, decision.operation, decision.project_id, decision.tenant_id,
         "ALLOWED" if decision.allowed else "DENIED", decision.code, correlation_id),
    )
