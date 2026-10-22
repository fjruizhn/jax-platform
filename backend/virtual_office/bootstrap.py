"""Explicit, self-authenticated initial Office-root bootstrap ceremony."""
from __future__ import annotations

from dataclasses import dataclass
import re

from auth.conexiones import _cortar_conexiones
from auth.models import AuthUser
from db.transaccion import AISLAMIENTO_ADMIN, transaccion

from .mfa import (
    MFADenied, load_mfa_config_from_environment,
    reload_current_self_mfa_proof_in_cursor,
    terminalize_recovery_receipts_after_token_cut_in_cursor,
)


CEREMONY_KEY = "INITIAL_OFFICE_SUPERUSER"
_BASIS = re.compile(r"[ -~]{1,128}\Z", re.ASCII)


class BootstrapDenied(RuntimeError):
    """The ceremony deliberately exposes no account or proof detail."""


@dataclass(frozen=True, slots=True)
class BootstrapReceipt:
    ceremony_key: str
    target_user_id: int
    identity_proof_event_id: int
    credential_audit_id: int
    replay: bool


def _positive_id(value: object) -> bool:
    return type(value) is int and value > 0


async def _locked_current_target(cur, target_user_id: int, expected_email: str) -> tuple[int, str] | None:
    """Lock and validate the user selected by numeric ID, never by email."""
    # The untrusted input becomes only a discovery hint.  Tenant is locked
    # first (the general 3C ordering), then the canonical user is re-read and
    # checked against that hint under its row lock.
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (target_user_id,))
    discovered = await cur.fetchone()
    if discovered is None:
        return None
    tenant_id = int(discovered[0])
    await cur.execute("SELECT tenant_id,status FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (tenant_id,))
    tenant = await cur.fetchone()
    if tenant is None or tenant[1] != "active":
        return None
    await cur.execute(
        "SELECT tenant_id,email,status,account_class FROM jax_users WHERE user_id=%s FOR UPDATE",
        (target_user_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    current_tenant_id, email, user_status, account_class = row
    if (int(current_tenant_id) != tenant_id or user_status != "active" or
            account_class not in {"INTERNAL", "OFFICE_INTERNAL"} or email != expected_email):
        return None
    return int(tenant_id), str(account_class)


async def credential_self_control_in_cursor(cur, *, user_id: int) -> int | None:
    """Return the real latest self-credential audit id after admin control.

    The audit table is historical (and intentionally lacks user FKs), so the
    target user lock held by the caller makes this decision stable with the
    canonical writers that append those events in the same user transaction.
    """
    await cur.execute(
        "SELECT id,actor_user_id,action,ts FROM user_admin_audit WHERE target_user_id=%s "
        "AND action IN ('create','update_email','password_set_by_admin',"
        "'password_reset_completed','password_changed_self') ORDER BY ts ASC,id ASC",
        (user_id,),
    )
    latest_admin: tuple[object, object] | None = None
    latest_self_id: int | None = None
    for audit_id, audit_actor_user_id, action, timestamp in await cur.fetchall():
        if action in {"create", "update_email", "password_set_by_admin"}:
            latest_admin = (timestamp, audit_id)
            latest_self_id = None
        elif action in {"password_reset_completed", "password_changed_self"}:
            # A reset-completion record means self control only when its
            # immutable audit actor is the credential subject.  A forged or
            # administrator-originated completion must never satisfy the
            # bootstrap ceremony; any later admin credential control resets
            # the chain again.
            if latest_admin is not None and audit_actor_user_id == user_id:
                latest_self_id = int(audit_id)
    return latest_self_id


async def bootstrap_initial_office_root(*, session_user: AuthUser, target_user_id: int,
                                       expected_email: str, basis_ref: str) -> BootstrapReceipt:
    """Create the one initial binding, or return its immutable receipt.

    The caller may only name the target and expected email.  Actor and current
    proof are derived from the verified middleware session; neither can arrive
    as independent authority arguments.
    """
    if (not _positive_id(target_user_id) or not isinstance(expected_email, str)
            or not expected_email or not isinstance(basis_ref, str) or not _BASIS.fullmatch(basis_ref)):
        raise BootstrapDenied("bootstrap_denied")
    try:
        actor_user_id = int(session_user.user_id)
    except (TypeError, ValueError) as exc:
        raise BootstrapDenied("bootstrap_denied") from exc
    proof_event_id = session_user.mfa_proof_event_id
    if actor_user_id != target_user_id or not _positive_id(proof_event_id):
        raise BootstrapDenied("bootstrap_denied")
    config = load_mfa_config_from_environment()
    if config is None:
        raise BootstrapDenied("bootstrap_denied")

    cut_connections = False
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        # user/profile before the ceremony state: this is the shared 3C
        # ownership order for a profile writer.
        if await _locked_current_target(cur, target_user_id, expected_email) is None:
            raise BootstrapDenied("bootstrap_denied")
        await cur.execute("SELECT user_id FROM office_account_profile WHERE user_id=%s FOR UPDATE", (target_user_id,))
        existing_profile = await cur.fetchone()
        proof = await reload_current_self_mfa_proof_in_cursor(
            cur, actor_user_id=actor_user_id, proof_event_id=int(proof_event_id), config=config,
        )
        if isinstance(proof, MFADenied):
            raise BootstrapDenied("bootstrap_denied")
        credential_audit_id = await credential_self_control_in_cursor(cur, user_id=target_user_id)
        if credential_audit_id is None:
            raise BootstrapDenied("bootstrap_denied")
        await cur.execute(
            "SELECT target_user_id,identity_actor_user_id,identity_proof_event_id,credential_audit_id,basis_ref "
            "FROM office_bootstrap_state WHERE ceremony_key=%s FOR UPDATE",
            (CEREMONY_KEY,),
        )
        state = await cur.fetchone()
        if state is not None:
            state_target, state_actor, state_proof, state_audit, state_basis = state
            if int(state_target) != target_user_id or int(state_actor) != actor_user_id or state_basis != basis_ref:
                raise BootstrapDenied("bootstrap_denied")
            # A replay is receipt retrieval under a fresh session.  It never
            # heals a revoked/changed profile and never rewrites provenance.
            return BootstrapReceipt(CEREMONY_KEY, int(state_target), int(state_proof), int(state_audit), True)
        if existing_profile is not None:
            raise BootstrapDenied("bootstrap_denied")
        await cur.execute(
            "INSERT INTO office_account_profile (user_id,global_role,status,version,created_at,updated_at) "
            "VALUES (%s,'OFFICE_SUPERUSER','ACTIVE',1,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
            (target_user_id,),
        )
        await cur.execute("DELETE FROM password_reset_tokens WHERE user_id=%s AND used=FALSE", (target_user_id,))
        await cur.execute("UPDATE jax_users SET token_version=token_version+1 WHERE user_id=%s", (target_user_id,))
        await terminalize_recovery_receipts_after_token_cut_in_cursor(cur, user_id=target_user_id)
        await cur.execute(
            "INSERT INTO office_bootstrap_state "
            "(ceremony_key,target_user_id,identity_actor_user_id,identity_proof_event_id,credential_audit_id,basis_ref,completed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))",
            (CEREMONY_KEY, target_user_id, actor_user_id, proof.event_id, credential_audit_id, basis_ref),
        )
        cut_connections = True
        receipt = BootstrapReceipt(CEREMONY_KEY, target_user_id, proof.event_id, credential_audit_id, False)
    if cut_connections:
        await _cortar_conexiones(target_user_id)
    return receipt
