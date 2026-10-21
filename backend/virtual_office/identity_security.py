"""Target-side identity barrier for Office-bound accounts.

This is deliberately separate from the classic administrator role check.  A
classic superadmin can administer a classic account, but may not take over an
Office-bound one through password, email, reset-link, role, reactivation,
unlock, or baja operations.  Task 3 supplies the Office-root + recent-MFA
proof; until then the only safe result is denial.
"""
from __future__ import annotations

from enum import StrEnum

from fastapi import HTTPException, status


class OfficeIdentityMutation(StrEnum):
    PASSWORD = "PASSWORD"
    RESET_LINK = "RESET_LINK"
    EMAIL = "EMAIL"
    CLASSIC_ROLE = "CLASSIC_ROLE"
    REACTIVATE = "REACTIVATE"
    UNLOCK = "UNLOCK"
    BAJA = "BAJA"


_OFFICE_ACCOUNT_CLASSES = frozenset({"OFFICE_INTERNAL", "EXTERNAL_CLIENT"})


async def guard_office_identity_mutation_in_cursor(cur, *, actor_user_id: int,
                                                    target_user_id: int,
                                                    mutation: OfficeIdentityMutation) -> None:
    """Fail closed for a target permanently bound to Office.

    Caller already holds tenant -> actor -> target locks.  The profile lookup
    takes its row lock too, so a concurrent first Office binding cannot race a
    classic credential mutation.  ``actor_user_id`` and the typed mutation are
    accepted now so the Task 3 proof verifier can extend this exact boundary
    without moving authorization into individual handlers.
    """
    if not isinstance(mutation, OfficeIdentityMutation):
        raise ValueError("mutation must be OfficeIdentityMutation")
    await cur.execute(
        "SELECT u.account_class, p.user_id FROM jax_users u "
        "LEFT JOIN office_account_profile p ON p.user_id=u.user_id "
        "WHERE u.user_id=%s FOR UPDATE",
        (target_user_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="usuario_no_encontrado")
    account_class, profile_user_id = row
    if account_class not in {"INTERNAL", "OFFICE_INTERNAL", "EXTERNAL_CLIENT"}:
        # Corrupt classification is never a reason to permit a credential
        # mutation.  The session path uses the same known-enum boundary.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="office_identity_guard_required")
    if account_class in _OFFICE_ACCOUNT_CLASSES or profile_user_id is not None:
        # No usable recent-MFA proof exists before Task 3.  Do not substitute
        # generic Office administration or a classic role for that proof.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="office_identity_guard_required")
