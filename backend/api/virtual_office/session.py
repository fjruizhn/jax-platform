"""Server-resolved entry contract for the Virtual Office frontend."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.authority import authorize_office_in_cursor
from virtual_office.capabilities import OfficeOperation
from virtual_office.mfa import (
    MFADenied,
    load_mfa_config_from_environment,
    reload_current_self_mfa_proof_in_cursor,
    reload_current_self_mfa_verified_in_cursor,
)
from virtual_office import repository


router = APIRouter(prefix="/api/virtual-office")


async def _mfa_state(cur, *, actor_user_id: int, proof_event_id: int | None, config) -> dict[str, bool]:
    """Reload the bearer reference; it is never authority by itself."""
    if type(proof_event_id) is not int or proof_event_id < 1:
        return {"verified": False, "recent": False}
    verified = await reload_current_self_mfa_verified_in_cursor(
        cur, actor_user_id=actor_user_id, proof_event_id=proof_event_id,
    )
    if isinstance(verified, MFADenied):
        return {"verified": False, "recent": False}
    recent = await reload_current_self_mfa_proof_in_cursor(
        cur, actor_user_id=actor_user_id, proof_event_id=proof_event_id, config=config,
    )
    return {"verified": True, "recent": not isinstance(recent, MFADenied)}


async def _lock_current_actor(cur, *, actor_user_id: int, bearer_tenant_id: int,
                              bearer_token_version: int):
    """Lock tenant then user and reject a bearer cut during dependency handoff."""
    # The first read is only a lock-order hint.  Its value is checked again
    # after the canonical tenant and actor locks are held.
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (actor_user_id,))
    discovered = await cur.fetchone()
    if discovered is None:
        raise HTTPException(status_code=401, detail="sesion_invalida")
    discovered_tenant_id = int(discovered[0])
    await cur.execute("SELECT tenant_id FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (discovered_tenant_id,))
    if await cur.fetchone() is None:
        raise HTTPException(status_code=401, detail="sesion_invalida")
    actor = await repository.fetch_actor(cur, actor_user_id, for_update=True)
    await cur.execute("SELECT tenant_id,token_version FROM jax_users WHERE user_id=%s FOR UPDATE", (actor_user_id,))
    current = await cur.fetchone()
    if (actor is None or current is None or int(actor["tenant_id"]) != discovered_tenant_id
            or int(current[0]) != discovered_tenant_id or int(current[0]) != bearer_tenant_id
            or int(current[1]) != bearer_token_version):
        raise HTTPException(status_code=401, detail="sesion_invalida")
    return actor


@router.get("/session")
async def office_session(user: AuthUser = Depends(get_current_user)):
    """Return a deliberately small, database-derived Office entry contract.

    Project grants are intentionally absent: project discovery stays behind its
    own bounded resolver endpoint.  Every privileged bit is independently
    re-authorized with a current MFA proof in this transaction.
    """
    try:
        actor_user_id = int(user.user_id)
        bearer_tenant_id = int(user.tenant_id)
        bearer_token_version = int(user.token_version)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=401, detail="sesion_invalida") from error
    config = load_mfa_config_from_environment()
    if config is None:
        # A partial capability document would make a configuration incident
        # indistinguishable from a role decision to the frontend.
        raise HTTPException(status_code=503, detail="office_session_unavailable")
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            actor = await _lock_current_actor(
                cur, actor_user_id=actor_user_id, bearer_tenant_id=bearer_tenant_id,
                bearer_token_version=bearer_token_version,
            )
            if actor is None or actor["user_status"] != "active" or actor["tenant_status"] != "active":
                raise HTTPException(status_code=403, detail="office_access_denied")
            account_class = actor["account_class"]
            global_role = actor["global_role"]
            if (account_class not in {"INTERNAL", "OFFICE_INTERNAL", "EXTERNAL_CLIENT"}
                    or actor["profile_status"] != "ACTIVE"
                    or global_role not in {"COLLABORATOR", "GENERAL_ADMIN", "OFFICE_SUPERUSER"}
                    or (account_class == "EXTERNAL_CLIENT" and global_role != "COLLABORATOR")):
                raise HTTPException(status_code=403, detail="office_access_denied")
            mfa = await _mfa_state(
                cur, actor_user_id=actor_user_id, proof_event_id=user.mfa_proof_event_id, config=config,
            )
            capabilities = {
                "create_project": False,
                "administer_office": False,
                "administer_identity_security": False,
            }
            if account_class != "EXTERNAL_CLIENT":
                operations = {
                    "create_project": OfficeOperation.CREATE_OFFICE_PROJECT,
                    "administer_office": OfficeOperation.ADMINISTER_OFFICE,
                    "administer_identity_security": OfficeOperation.ADMINISTER_OFFICE_IDENTITY_SECURITY,
                }
                for name, operation in operations.items():
                    decision = await authorize_office_in_cursor(
                        cur, actor_user_id, operation, require_recent_mfa=True,
                        mfa_proof_event_id=user.mfa_proof_event_id, mfa_config=config,
                    )
                    capabilities[name] = bool(decision.allowed)
            return {
                "identity": {
                    "user_id": actor_user_id,
                    "tenant_id": int(actor["tenant_id"]),
                    "account_class": account_class,
                    "global_role": global_role,
                },
                "mfa": mfa,
                "capabilities": capabilities,
            }
    except HTTPException:
        raise
    except Exception as error:
        # Do not emit a document with some bits calculated from stale or
        # incomplete database reads.
        raise HTTPException(status_code=503, detail="office_session_unavailable") from error
