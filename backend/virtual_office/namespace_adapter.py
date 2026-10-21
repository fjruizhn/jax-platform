"""Trusted JXP authorization adapters for JAX Office namespace mutations.

This module is deliberately not an API router.  It is the in-transaction
bridge used by JAX's typed namespace contract; Office project routes wait for
the MFA boundary in Task 3.
"""
from __future__ import annotations

from . import repository
from .audit import record_authorization_event
from .authority import authorize_office_in_cursor
from .capabilities import OfficeOperation


class OfficeNamespaceAuthorizationDenied(PermissionError):
    """The current, locked Office identity cannot perform this mutation."""


def _office_namespace(namespace) -> bool:
    return getattr(namespace, "value", namespace) == "OFFICE_CONFIDENTIAL"


class OfficeNamespaceAuthorizer:
    """JAX callback for creating Office scopes under JAX's transaction."""

    async def authorize_in_transaction(self, cursor, *, actor_user_id: int,
                                       target_tenant_id: int, namespace, operation: str) -> None:
        if operation != "CREATE_OFFICE_PROJECT" or not _office_namespace(namespace):
            raise OfficeNamespaceAuthorizationDenied("Office namespace operation denied")
        decision = await authorize_office_in_cursor(
            cursor, actor_user_id, OfficeOperation.CREATE_OFFICE_PROJECT)
        if not decision.allowed or decision.global_role != "OFFICE_SUPERUSER":
            raise OfficeNamespaceAuthorizationDenied("Office project creation requires an active Office superuser")
        target = await repository.fetch_tenant(cursor, target_tenant_id, for_update=True)
        if target is None or target["status"] != "active":
            raise OfficeNamespaceAuthorizationDenied("Office target tenant is not active")
        # The callback is invoked before JAX inserts scope/project.  The event
        # therefore records only canonical actor and requested tenant, inside
        # the same transaction that creates both JAX rows.
        decision = decision.__class__(True, "office_namespace_create", decision.actor_user_id,
                                      OfficeOperation.CREATE_OFFICE_PROJECT.value, None, None,
                                      target_tenant_id, decision.global_role, None, None, None)
        await record_authorization_event(cursor, decision)


class OfficeNamespaceMembershipAuthorizer:
    """Typed JAX membership callback; no request supplied callback is accepted.

    Task 3 supplies MFA and invitation-capability context before calling the
    core membership methods.  This adapter intentionally accepts only the
    already typed execution value from that contract and has no HTTP entry.
    """

    async def authorize_membership_in_transaction(
        self, cursor, *, actor_user_id: int, target_tenant_id: int, project_id: int,
        target_user_id: int, namespace, operation, member_kind, idempotency_key: str, execution,
    ) -> None:
        if not _office_namespace(namespace):
            raise OfficeNamespaceAuthorizationDenied("Office namespace operation denied")
        operation_value = getattr(operation, "value", operation)
        if operation_value not in {"GRANT_OFFICE_MEMBER", "REVOKE_OFFICE_MEMBER"}:
            raise OfficeNamespaceAuthorizationDenied("Office membership operation denied")
        # The .2 core carries typed invitation provenance, but Task 3 has not
        # yet supplied JXP's issuer/acceptance proof.  Do not treat a caller
        # supplied UUID as such proof.  Interactive administration remains
        # current-DB authority; invitation acceptance fails closed until its
        # trusted issuance context is implemented.
        if getattr(getattr(execution, "kind", None), "value", getattr(execution, "kind", None)) == "INVITATION_ACCEPTANCE":
            raise OfficeNamespaceAuthorizationDenied("Office invitation acceptance proof is unavailable")
        decision = await authorize_office_in_cursor(
            cursor, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id)
        if not decision.allowed:
            raise OfficeNamespaceAuthorizationDenied("Office membership administration denied")
        if decision.tenant_id != target_tenant_id:
            raise OfficeNamespaceAuthorizationDenied("Office membership target tenant denied")
        target = await repository.fetch_actor(cursor, target_user_id, for_update=True)
        if target is None or target["tenant_id"] != target_tenant_id:
            raise OfficeNamespaceAuthorizationDenied("Office membership target identity denied")
        target_tenant = await repository.fetch_tenant(cursor, target_tenant_id, for_update=True)
        # A disabled tenant may only be cleaned by the root role, and never
        # receive a new grant.  The JAX core handles exact target/class rules.
        if target_tenant is None or (target_tenant["status"] != "active"
                                     and not (operation_value == "REVOKE_OFFICE_MEMBER"
                                              and decision.global_role == "OFFICE_SUPERUSER")):
            raise OfficeNamespaceAuthorizationDenied("Office membership target tenant inactive")
        await record_authorization_event(cursor, decision)
