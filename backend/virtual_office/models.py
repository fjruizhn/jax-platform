from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OfficeDecision:
    """A resolved database decision; callers cannot mutate or reuse authority."""

    allowed: bool
    code: str
    actor_user_id: int
    operation: str
    project_id: int | None
    tenant_id: int | None = None
    global_role: str | None = None
