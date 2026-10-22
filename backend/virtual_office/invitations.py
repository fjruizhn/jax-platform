"""Transactional invitation issuance, revocation, and acceptance.

This is deliberately a service, not an ingress router.  It receives only
server-derived identities and current session proof references.  The raw
bearer is hashed immediately and never enters SQL, an exception, or a log.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass

from crypto_secrets import clave_de_cifrado_utilizable, encrypt_secret
from validacion import direccion_unica_valida

from .audit import record_authorization_event
from .authority import authorize_office_in_cursor
from .capabilities import OfficeOperation
from .invitation_config import InvitationConfig
from .membership_receipts import invitation_acceptance_digest, write_invitation_acceptance_receipt
from .mfa import MFADenied, MFAConfig, reload_current_self_mfa_proof_in_cursor


class InvitationError(RuntimeError):
    """A stable, token-safe domain error for the HTTP boundary."""
    def __init__(self, code: str, *, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class IssuedInvitation:
    invitation_id: str
    expires_at: dt.datetime


def _normalize_email(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not value.isascii():
        raise InvitationError("office_invitation_email_invalid", status_code=422)
    normalized = value.lower()
    if not direccion_unica_valida(normalized):
        raise InvitationError("office_invitation_email_invalid", status_code=422)
    return normalized


def _token_hash(token: str) -> bytes:
    if not isinstance(token, str) or len(token) < 43 or len(token) > 256 or not token.isascii():
        raise InvitationError("office_invitation_invalid", status_code=403)
    return hashlib.sha256(token.encode("ascii")).digest()


def _proof_digest(*, token_hash: bytes, email: str, tenant_id: int, project_id: int,
                  recipient_group_id: int | None, target_user_id: int, account_class: str,
                  actor_user_id: int, profile_version: int, grant_version: int,
                  proof_event_id: int, mfa_version: int, issued_at: dt.datetime,
                  expires_at: dt.datetime, authorization_event_id: int) -> bytes:
    fields = {
        "account_class": account_class, "actor_user_id": actor_user_id,
        "authorization_event_id": authorization_event_id, "email": email,
        "expires_at": issued_at.isoformat() if False else expires_at.isoformat(),
        "grant_role": "COLLABORATOR", "grant_version": grant_version,
        "issued_at": issued_at.isoformat(), "mfa_version": mfa_version,
        "profile_version": profile_version, "project_id": project_id,
        "proof_event_id": proof_event_id, "recipient_group_id": recipient_group_id,
        "tenant_id": tenant_id, "token_hash": token_hash.hex(),
        "target_user_id": target_user_id,
    }
    return hashlib.sha256(json.dumps(fields, separators=(",", ":"), sort_keys=True).encode("utf-8")).digest()


def _outbox_payload(*, invitation_id: str, email: str, token: str, expires_at: dt.datetime) -> bytes:
    """Encrypt the sole transient bearer copy; it deliberately contains no URL."""
    if not clave_de_cifrado_utilizable():
        raise InvitationError("office_invitation_transport_unavailable", status_code=503)
    plaintext = json.dumps({
        "domain": "office-invitation-outbox/v1", "email": email,
        "expires_at": expires_at.isoformat(), "invitation_id": invitation_id,
        "token": token,
    }, separators=(",", ":"), sort_keys=True)
    try:
        return encrypt_secret(plaintext).encode("ascii")
    except (RuntimeError, ValueError, UnicodeError) as error:
        raise InvitationError("office_invitation_transport_unavailable", status_code=503) from error


async def _db_now(cur) -> dt.datetime:
    await cur.execute("SELECT UTC_TIMESTAMP(6)")
    value = (await cur.fetchone())[0]
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


async def _issuer_snapshot(cur, *, actor_user_id: int, project_id: int,
                           proof_event_id: int, mfa_config: MFAConfig):
    decision = await authorize_office_in_cursor(
        cur, actor_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, project_id,
        require_recent_mfa=True, mfa_proof_event_id=proof_event_id, mfa_config=mfa_config,
    )
    if not decision.allowed:
        raise InvitationError("office_invitation_issuer_denied", status_code=403)
    proof = await reload_current_self_mfa_proof_in_cursor(
        cur, actor_user_id=actor_user_id, proof_event_id=proof_event_id, config=mfa_config,
    )
    if isinstance(proof, MFADenied):
        raise InvitationError("office_invitation_issuer_denied", status_code=403)
    await cur.execute(
        "SELECT p.version,g.version FROM office_account_profile p "
        "JOIN office_project_grant g ON g.user_id=p.user_id "
        "WHERE p.user_id=%s AND p.status='ACTIVE' AND g.project_id=%s "
        "AND g.status='ACTIVE' AND g.audience='INTERNAL' AND g.grant_role='PROJECT_ADMIN' FOR UPDATE",
        (actor_user_id, project_id),
    )
    basis = await cur.fetchone()
    if basis is None:
        raise InvitationError("office_invitation_issuer_denied", status_code=403)
    return decision, proof, int(basis[0]), int(basis[1])


async def issue_invitation_in_cursor(
    cur, *, actor_user_id: int, project_id: int, target_user_id: int,
    email: str, target_account_class: str, recipient_group_id: int | None,
    proof_event_id: int | None, mfa_config: MFAConfig | None,
    invitation_config: InvitationConfig,
) -> IssuedInvitation:
    """Issue to an existing, compatible identity and queue encrypted delivery."""
    if type(actor_user_id) is not int or type(project_id) is not int or type(target_user_id) is not int:
        raise InvitationError("office_invitation_invalid", status_code=422)
    if mfa_config is None or type(proof_event_id) is not int or proof_event_id < 1:
        raise InvitationError("office_invitation_issuer_denied", status_code=403)
    normalized_email = _normalize_email(email)
    if target_account_class not in {"OFFICE_INTERNAL", "EXTERNAL_CLIENT"}:
        raise InvitationError("office_invitation_target_denied", status_code=409)
    if ((target_account_class == "OFFICE_INTERNAL" and recipient_group_id is not None)
            or (target_account_class == "EXTERNAL_CLIENT" and not isinstance(recipient_group_id, int))):
        raise InvitationError("office_invitation_target_denied", status_code=409)
    decision, proof, profile_version, grant_version = await _issuer_snapshot(
        cur, actor_user_id=actor_user_id, project_id=project_id,
        proof_event_id=proof_event_id, mfa_config=mfa_config,
    )
    await cur.execute(
        "SELECT u.tenant_id,u.email,u.account_class,u.status,t.status,p.status "
        "FROM jax_users u JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
        "LEFT JOIN office_account_profile p ON p.user_id=u.user_id WHERE u.user_id=%s FOR UPDATE",
        (target_user_id,),
    )
    target = await cur.fetchone()
    if (target is None or target[1] != normalized_email or target[2] != target_account_class
            or target[3] != "active" or target[4] != "active" or target[5] != "ACTIVE"):
        raise InvitationError("office_invitation_target_denied", status_code=409)
    tenant_id = int(target[0])
    await cur.execute(
        "SELECT tenant_id,status,namespace FROM jax_project_scope WHERE project_id=%s FOR UPDATE", (project_id,))
    scope = await cur.fetchone()
    if scope is None or int(scope[0]) != tenant_id or scope[1] != "ACTIVE" or scope[2] != "OFFICE_CONFIDENTIAL":
        raise InvitationError("office_invitation_target_denied", status_code=409)
    if target_account_class == "EXTERNAL_CLIENT":
        await cur.execute(
            "SELECT recipient_group_id FROM office_recipient_group WHERE recipient_group_id=%s AND project_id=%s "
            "AND group_kind='CLIENT' AND status='ACTIVE' FOR UPDATE", (recipient_group_id, project_id))
        if await cur.fetchone() is None:
            raise InvitationError("office_invitation_target_denied", status_code=409)
    now = await _db_now(cur)
    expires_at = now + dt.timedelta(seconds=invitation_config.lifetime_seconds)
    token = secrets.token_urlsafe(32)
    token_hash = _token_hash(token)
    invitation_id = str(uuid.uuid4())
    issuance_id = str(uuid.uuid4())
    outbox_id = str(uuid.uuid4())
    correlation_id = uuid.uuid4().bytes
    authorization_event_id = await record_authorization_event(cur, decision)
    proof_digest = _proof_digest(
        token_hash=token_hash, email=normalized_email, tenant_id=tenant_id, project_id=project_id,
        recipient_group_id=recipient_group_id, target_user_id=target_user_id,
        account_class=target_account_class, actor_user_id=actor_user_id,
        profile_version=profile_version, grant_version=grant_version,
        proof_event_id=proof.event_id, mfa_version=proof.mfa_version,
        issued_at=now, expires_at=expires_at, authorization_event_id=authorization_event_id,
    )
    payload = _outbox_payload(invitation_id=invitation_id, email=normalized_email, token=token, expires_at=expires_at)
    await cur.execute(
        "INSERT INTO office_invitation_issuance "
        "(issuance_id,token_hash,email_normalized,tenant_id,project_id,recipient_group_id,target_user_id,target_account_class,grant_role,audience,issued_by_user_id,issuer_profile_version,issuer_grant_version,issuer_proof_event_id,issuer_mfa_version,issuer_mfa_at,issuance_authorization_event_id,proof_digest,correlation_id,issued_at,expires_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'COLLABORATOR',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6),%s)",
        (issuance_id, token_hash, normalized_email, tenant_id, project_id, recipient_group_id, target_user_id,
         target_account_class, "INTERNAL" if target_account_class == "OFFICE_INTERNAL" else "CLIENT_PUBLISHED",
         actor_user_id, profile_version, grant_version, proof.event_id, proof.mfa_version,
         proof.occurred_at.replace(tzinfo=None), authorization_event_id, proof_digest, correlation_id, expires_at.replace(tzinfo=None)),
    )
    await cur.execute(
        "INSERT INTO office_invitation (invitation_id,issuance_id,project_id,email_normalized,target_user_id) "
        "VALUES (%s,%s,%s,%s,%s)", (invitation_id, issuance_id, project_id, normalized_email, target_user_id))
    await cur.execute(
        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,bearer_token_hash,observed_outbox_state,correlation_id,occurred_at) "
        "VALUES (%s,'ISSUED',NULL,%s,NULL,NULL,%s,UTC_TIMESTAMP(6))", (invitation_id, actor_user_id, correlation_id))
    await cur.execute(
        "INSERT INTO office_invitation_outbox (outbox_id,invitation_id,state,attempt_id,attempt_count,cancel_requested,payload_ciphertext,message_id,result_code,claimed_at,finished_at,created_at,updated_at) "
        "VALUES (%s,%s,'QUEUED',NULL,0,FALSE,%s,NULL,NULL,NULL,NULL,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
        (outbox_id, invitation_id, payload))
    return IssuedInvitation(invitation_id=invitation_id, expires_at=expires_at)
