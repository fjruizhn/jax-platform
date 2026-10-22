"""Transactional invitation issuance, revocation, and acceptance.

This is deliberately a service, not an ingress router.  It receives only
server-derived identities and current session proof references.  The raw
bearer is hashed immediately and never enters SQL, an exception, or a log.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass

from crypto_secrets import clave_de_cifrado_utilizable, decrypt_db_secret, encrypt_secret
from validacion import direccion_unica_valida

from .audit import record_authorization_event
from .authority import authorize_office_in_cursor
from .capabilities import OfficeOperation
from .invitation_config import InvitationConfig
from .membership_receipts import invitation_acceptance_digest, write_invitation_acceptance_receipt
from .mfa import MFADenied, MFAConfig, load_mfa_config_from_environment, reload_current_self_mfa_proof_in_cursor


async def _create_invited_identity_in_cursor(cur, *, tenant_id: int, email: str,
                                             account_class: str, password_hash: str) -> int:
    """Create only the bearer-bound identity, never a general signup user."""
    import user_audit
    await cur.execute("SELECT tenant_id,status FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (tenant_id,))
    tenant = await cur.fetchone()
    if tenant is None or tenant[1] != "active":
        raise InvitationError("office_invitation_invalid", status_code=403)
    await cur.execute("SELECT user_id FROM jax_users WHERE BINARY email=BINARY %s FOR UPDATE", (email,))
    if await cur.fetchone() is not None:
        raise InvitationError("office_invitation_existing_session_required", status_code=409)
    try:
        await cur.execute(
            "INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) "
            "VALUES (%s,%s,%s,'operator','active',%s)",
            (tenant_id, email, password_hash, account_class))
    except Exception as error:
        # A duplicate-email race must not turn a password supplied for a new
        # identity into a write on the identity that won elsewhere.
        if getattr(error, "args", ()) and error.args[0] == 1062:
            raise InvitationError("office_invitation_existing_session_required", status_code=409) from error
        raise
    user_id = int(cur.lastrowid)
    await user_audit.registrar(cur, user_id, user_id, "create", {"email": email, "role": "operator"})
    await user_audit.registrar(cur, user_id, user_id, "password_reset_completed", None)
    credential_audit_id = int(cur.lastrowid)
    await cur.execute(
        "INSERT INTO office_account_profile "
        "(user_id,global_role,status,version,bound_by_user_id,credential_audit_id,created_at,updated_at) "
        "VALUES (%s,'COLLABORATOR','ACTIVE',1,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
        (user_id, user_id, credential_audit_id))
    return user_id


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
                  recipient_group_id: int | None, target_user_id: int | None, account_class: str,
                  actor_user_id: int, profile_version: int, grant_version: int,
                  proof_event_id: int, mfa_version: int, issued_at: dt.datetime,
                  expires_at: dt.datetime, authorization_event_id: int) -> bytes:
    fields = {
        "account_class": account_class, "actor_user_id": actor_user_id,
        "authorization_event_id": authorization_event_id, "email": email,
        "expires_at": expires_at.isoformat(),
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
    cur, *, actor_user_id: int, project_id: int, email: str, audience: str,
    proof_event_id: int | None, mfa_config: MFAConfig | None,
    invitation_config: InvitationConfig,
) -> IssuedInvitation:
    """Derive the target and audience server-side, then queue delivery."""
    if type(actor_user_id) is not int or type(project_id) is not int:
        raise InvitationError("office_invitation_invalid", status_code=422)
    if mfa_config is None or type(proof_event_id) is not int or proof_event_id < 1:
        raise InvitationError("office_invitation_issuer_denied", status_code=403)
    normalized_email = _normalize_email(email)
    if audience not in {"INTERNAL", "CLIENT_PUBLISHED"}:
        raise InvitationError("office_invitation_target_denied", status_code=409)
    target_account_class = "OFFICE_INTERNAL" if audience == "INTERNAL" else "EXTERNAL_CLIENT"
    decision, proof, profile_version, grant_version = await _issuer_snapshot(
        cur, actor_user_id=actor_user_id, project_id=project_id,
        proof_event_id=proof_event_id, mfa_config=mfa_config,
    )
    await cur.execute(
        "SELECT tenant_id,status,authorization_namespace FROM jax_project_scope WHERE project_id=%s FOR UPDATE", (project_id,))
    scope = await cur.fetchone()
    if scope is None or scope[1] != "ACTIVE" or scope[2] != "OFFICE_CONFIDENTIAL":
        raise InvitationError("office_invitation_target_denied", status_code=409)
    tenant_id = int(scope[0])
    await cur.execute(
        "SELECT user_id,tenant_id,account_class,status FROM jax_users WHERE BINARY email=BINARY %s FOR UPDATE",
        (normalized_email,))
    target = await cur.fetchone()
    target_user_id = None if target is None else int(target[0])
    if target is not None and (int(target[1]) != tenant_id or target[2] != target_account_class or target[3] != "active"):
        raise InvitationError("office_invitation_target_denied", status_code=409)
    recipient_group_id = None
    if audience == "CLIENT_PUBLISHED":
        await cur.execute(
            "SELECT recipient_group_id FROM office_recipient_group WHERE project_id=%s "
            "AND group_kind='CLIENT' AND status='ACTIVE' FOR UPDATE", (project_id,))
        group = await cur.fetchone()
        if group is None:
            raise InvitationError("office_invitation_target_denied", status_code=409)
        recipient_group_id = int(group[0])
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
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'COLLABORATOR',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (issuance_id, token_hash, normalized_email, tenant_id, project_id, recipient_group_id, target_user_id,
         target_account_class, audience,
         actor_user_id, profile_version, grant_version, proof.event_id, proof.mfa_version,
         proof.occurred_at.replace(tzinfo=None), authorization_event_id, proof_digest, correlation_id,
         now.replace(tzinfo=None), expires_at.replace(tzinfo=None)),
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


async def revoke_invitation_in_cursor(
    cur, *, actor_user_id: int, project_id: int, invitation_id: str, expected_version: int,
    proof_event_id: int | None,
    mfa_config: MFAConfig | None,
) -> bool:
    """Revoke an unconsumed invitation and let the DB guard cancel delivery."""
    if mfa_config is None or type(proof_event_id) is not int or type(expected_version) is not int or expected_version < 1:
        raise InvitationError("office_invitation_issuer_denied", status_code=403)
    await cur.execute(
        "SELECT i.status,i.version,i.project_id,s.correlation_id FROM office_invitation i "
        "JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id "
        "WHERE i.invitation_id=%s FOR UPDATE", (invitation_id,))
    invitation = await cur.fetchone()
    if invitation is None or int(invitation[2]) != project_id:
        raise InvitationError("office_invitation_not_found", status_code=404)
    if invitation[0] == "REVOKED":
        return True
    if invitation[0] != "ISSUED":
        raise InvitationError("office_invitation_terminal")
    if int(invitation[1]) != expected_version:
        raise InvitationError("office_invitation_version_conflict")
    await _issuer_snapshot(cur, actor_user_id=actor_user_id, project_id=int(invitation[2]),
                           proof_event_id=proof_event_id, mfa_config=mfa_config)
    await cur.execute(
        "SELECT event_id FROM office_invitation_event WHERE invitation_id=%s AND event_kind='ISSUED' FOR UPDATE",
        (invitation_id,))
    issued = await cur.fetchone()
    if issued is None:
        raise InvitationError("office_invitation_integrity", status_code=503)
    await cur.execute(
        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,bearer_token_hash,observed_outbox_state,correlation_id,occurred_at) "
        "VALUES (%s,'REVOKED',%s,%s,NULL,NULL,%s,UTC_TIMESTAMP(6))",
        (invitation_id, int(issued[0]), actor_user_id, invitation[3]))
    await cur.execute(
        "UPDATE office_invitation SET status='REVOKED',version=version+1,revoked_by_user_id=%s,revoked_at=UTC_TIMESTAMP(6) "
        "WHERE invitation_id=%s AND status='ISSUED' AND version=%s",
        (actor_user_id, invitation_id, expected_version))
    if cur.rowcount != 1:
        raise InvitationError("office_invitation_retryable", status_code=503)
    return False


class _InvitationAcceptanceAuthorizer:
    """Trusted JXP proof resolver used only by JAX's typed capability call."""
    def __init__(self, *, invitation_id: str, issuer_user_id: int, target_user_id: int,
                 target_tenant_id: int, project_id: int, mfa_version: int,
                 proof_event_id: int, mfa_config: MFAConfig):
        self.invitation_id = invitation_id
        self.issuer_user_id = issuer_user_id
        self.target_user_id = target_user_id
        self.target_tenant_id = target_tenant_id
        self.project_id = project_id
        self.mfa_version = mfa_version
        self.proof_event_id = proof_event_id
        self.mfa_config = mfa_config
        self.authorization_event_id: int | None = None

    async def authorize_membership_in_transaction(self, cursor, *, actor_user_id: int,
                                                  target_tenant_id: int, project_id: int,
                                                  target_user_id: int, namespace, operation,
                                                  member_kind, idempotency_key: str, execution) -> None:
        from jax.memory.project_authority import AuthorizationDenied, NamespaceMembershipExecutionKind
        if (getattr(namespace, "value", namespace) != "OFFICE_CONFIDENTIAL"
                or getattr(operation, "value", operation) != "GRANT_OFFICE_MEMBER"
                or getattr(getattr(execution, "kind", None), "value", None)
                != NamespaceMembershipExecutionKind.INVITATION_ACCEPTANCE.value
                or getattr(execution, "invitation_id", None) != self.invitation_id
                or actor_user_id != self.issuer_user_id or target_user_id != self.target_user_id
                or target_tenant_id != self.target_tenant_id or project_id != self.project_id):
            raise AuthorizationDenied("Office invitation capability binding denied")
        await cursor.execute(
            "SELECT u.mfa_version,p.status FROM jax_users u LEFT JOIN office_account_profile p ON p.user_id=u.user_id "
            "WHERE u.user_id=%s FOR UPDATE", (self.issuer_user_id,))
        issuer = await cursor.fetchone()
        if issuer is None or int(issuer[0]) != self.mfa_version or issuer[1] != "ACTIVE":
            raise AuthorizationDenied("Office invitation issuer proof is no longer current")
        decision = await authorize_office_in_cursor(
            cursor, self.issuer_user_id, OfficeOperation.ADMINISTER_PROJECT_MEMBERS, self.project_id,
            require_recent_mfa=True, mfa_proof_event_id=self.proof_event_id, mfa_config=self.mfa_config,
        )
        if not decision.allowed:
            raise AuthorizationDenied("Office invitation issuer authority is no longer current")
        self.authorization_event_id = await record_authorization_event(cursor, decision)


async def _expire_if_needed(cur, *, invitation_id: str, status: str, version: int,
                            issued_event_id: int, correlation_id: bytes, expires_at: dt.datetime) -> bool:
    now = await _db_now(cur)
    if status != "ISSUED" or now <= (expires_at.replace(tzinfo=dt.timezone.utc) if expires_at.tzinfo is None else expires_at):
        return False
    await cur.execute(
        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,bearer_token_hash,observed_outbox_state,correlation_id,occurred_at) "
        "VALUES (%s,'EXPIRED',%s,NULL,NULL,NULL,%s,UTC_TIMESTAMP(6))",
        (invitation_id, issued_event_id, correlation_id))
    await cur.execute(
        "UPDATE office_invitation SET status='EXPIRED',version=version+1 WHERE invitation_id=%s AND status='ISSUED' AND version=%s",
        (invitation_id, version))
    if cur.rowcount != 1:
        raise InvitationError("office_invitation_retryable", status_code=503)
    return True


async def accept_invitation_in_cursor(
    cur, *, accepting_user_id: int | None, accepting_email: str | None,
    new_password_hash: str | None, bearer_token: str,
) -> bool | None:
    """Consume one bearer through JAX and the Office companion atomically."""
    token_hash = _token_hash(bearer_token)
    await cur.execute(
        "SELECT i.invitation_id,i.status,i.version,i.project_id,i.target_user_id,s.issued_by_user_id,s.tenant_id,s.email_normalized,"
        "s.target_account_class,s.grant_role,s.audience,s.recipient_group_id,s.issuer_proof_event_id,"
        "s.issuer_profile_version,s.issuer_grant_version,s.issuer_mfa_version,s.issuer_mfa_at,"
        "s.issuance_authorization_event_id,s.proof_digest,s.token_hash,s.issued_at,s.expires_at,s.correlation_id "
        "FROM office_invitation i JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id "
        "WHERE s.token_hash=%s FOR UPDATE", (token_hash,))
    row = await cur.fetchone()
    if row is None:
        raise InvitationError("office_invitation_invalid", status_code=403)
    (invitation_id, status, version, project_id, target_user_id, issuer_user_id, tenant_id, issued_email,
     account_class, grant_role, audience, group_id, proof_event_id,
     issuer_profile_version, issuer_grant_version, issuer_mfa_version, issuer_mfa_at,
     issuance_auth_id, stored_proof_digest, stored_hash, issued_at, expires_at, correlation_id) = row
    if stored_hash != token_hash:
        raise InvitationError("office_invitation_invalid", status_code=403)
    # A completed invitation can only be observed by its authenticated owner.
    # A bearer alone must never become a password-reset capability after use.
    if status == "ACCEPTED":
        await cur.execute("SELECT accepted_user_id FROM office_invitation WHERE invitation_id=%s FOR UPDATE", (invitation_id,))
        accepted = await cur.fetchone()
        if accepting_user_id is not None and accepted is not None and accepted[0] == accepting_user_id:
            return True
        raise InvitationError("office_invitation_existing_session_required", status_code=409)
    if status != "ISSUED":
        raise InvitationError("office_invitation_invalid", status_code=403)
    await cur.execute("SELECT event_id FROM office_invitation_event WHERE invitation_id=%s AND event_kind='ISSUED' FOR UPDATE", (invitation_id,))
    issued = await cur.fetchone()
    if issued is None:
        raise InvitationError("office_invitation_integrity", status_code=503)
    if await _expire_if_needed(cur, invitation_id=invitation_id, status=status, version=int(version),
                               issued_event_id=int(issued[0]), correlation_id=correlation_id, expires_at=expires_at):
        return None
    if accepting_user_id is None:
        if target_user_id is not None or new_password_hash is None:
            raise InvitationError("office_invitation_existing_session_required", status_code=409)
        accepting_user_id = await _create_invited_identity_in_cursor(
            cur, tenant_id=int(tenant_id), email=issued_email,
            account_class=account_class, password_hash=new_password_hash)
    else:
        if new_password_hash is not None:
            raise InvitationError("office_invitation_invalid", status_code=403)
        if target_user_id is not None and int(target_user_id) != accepting_user_id:
            raise InvitationError("office_invitation_invalid", status_code=403)
        normalized = _normalize_email(accepting_email or "")
        await cur.execute(
            "SELECT email,tenant_id,account_class,status FROM jax_users WHERE user_id=%s FOR UPDATE", (accepting_user_id,))
        accepting = await cur.fetchone()
        if (accepting is None or accepting[0] != normalized or int(accepting[1]) != int(tenant_id)
                or accepting[2] != account_class or accepting[3] != "active"):
            raise InvitationError("office_invitation_invalid", status_code=403)
        from .bootstrap import credential_self_control_in_cursor
        credential_audit_id = await credential_self_control_in_cursor(cur, user_id=accepting_user_id)
        if credential_audit_id is None:
            raise InvitationError("office_invitation_invalid", status_code=403)
        await cur.execute("SELECT status FROM office_account_profile WHERE user_id=%s FOR UPDATE", (accepting_user_id,))
        profile = await cur.fetchone()
        if profile is None:
            await cur.execute(
                "INSERT INTO office_account_profile "
                "(user_id,global_role,status,version,bound_by_user_id,credential_audit_id,created_at,updated_at) "
                "VALUES (%s,'COLLABORATOR','ACTIVE',1,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
                (accepting_user_id, accepting_user_id, credential_audit_id))
        elif profile[0] != "ACTIVE":
            raise InvitationError("office_invitation_invalid", status_code=403)
    issued_at_utc = issued_at.replace(tzinfo=dt.timezone.utc) if issued_at.tzinfo is None else issued_at.astimezone(dt.timezone.utc)
    expires_at_utc = expires_at.replace(tzinfo=dt.timezone.utc) if expires_at.tzinfo is None else expires_at.astimezone(dt.timezone.utc)
    if not hmac.compare_digest(
        _proof_digest(token_hash=token_hash, email=issued_email, tenant_id=int(tenant_id), project_id=int(project_id),
                      recipient_group_id=group_id, target_user_id=(None if target_user_id is None else int(target_user_id)), account_class=account_class,
                      actor_user_id=int(issuer_user_id), profile_version=int(issuer_profile_version),
                      grant_version=int(issuer_grant_version), proof_event_id=int(proof_event_id),
                      mfa_version=int(issuer_mfa_version), issued_at=issued_at_utc,
                      expires_at=expires_at_utc, authorization_event_id=int(issuance_auth_id)),
        stored_proof_digest,
    ):
        raise InvitationError("office_invitation_integrity", status_code=503)
    # Proof event is immutable issuance evidence, then the callback below
    # reauthorizes the issuer from current Office state without minting a new
    # user session or treating a request boolean as authority.
    await cur.execute(
        "SELECT subject_user_id,event_kind,purpose,mfa_version,result,occurred_at FROM office_mfa_security_event "
        "WHERE event_id=%s FOR UPDATE", (proof_event_id,))
    proof = await cur.fetchone()
    if (proof is None or int(proof[0]) != int(issuer_user_id) or proof[1] != "MFA_PROOF"
            or proof[2] not in {"LOGIN", "STEP_UP"} or proof[4] != "VERIFIED" or int(proof[3]) != int(issuer_mfa_version)
            or proof[5] != issuer_mfa_at):
        raise InvitationError("office_invitation_invalid", status_code=403)
    await cur.execute(
        "SELECT state FROM office_invitation_outbox WHERE invitation_id=%s FOR UPDATE", (invitation_id,))
    outbox = await cur.fetchone()
    if outbox is None or outbox[0] not in {"QUEUED", "CLAIMED", "SMTP_ACCEPTED", "UNKNOWN", "CANCELLED"}:
        raise InvitationError("office_invitation_integrity", status_code=503)
    await cur.execute(
        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,bearer_token_hash,observed_outbox_state,correlation_id,occurred_at) "
        "VALUES (%s,'INVITATION_BEARER_PRESENTED',%s,NULL,%s,NULL,%s,UTC_TIMESTAMP(6))",
        (invitation_id, int(issued[0]), token_hash, correlation_id))
    bearer_event_id = int(cur.lastrowid)
    from db.connection import get_pool
    from jax.memory.b9_mariadb import MariaDBB9Store
    from jax.memory.project_authority import (NamespacedMemberKind, NamespaceMembershipExecution,
                                              NamespaceMembershipExecutionKind, ProjectAuthorityAdmin,
                                              ProjectNamespace)
    acceptance_mfa_config = load_mfa_config_from_environment()
    if acceptance_mfa_config is None:
        raise InvitationError("office_invitation_unavailable", status_code=503)
    authorizer = _InvitationAcceptanceAuthorizer(
        invitation_id=invitation_id, issuer_user_id=int(issuer_user_id), target_user_id=accepting_user_id,
        target_tenant_id=int(tenant_id), project_id=int(project_id), mfa_version=int(issuer_mfa_version),
        proof_event_id=int(proof_event_id), mfa_config=acceptance_mfa_config)
    result = await ProjectAuthorityAdmin(MariaDBB9Store(await get_pool())).grant_namespaced_member_in_transaction(
        cur, actor_user_id=int(issuer_user_id), target_tenant_id=int(tenant_id), project_id=int(project_id),
        target_user_id=accepting_user_id, namespace=ProjectNamespace.OFFICE_CONFIDENTIAL,
        member_kind=(NamespacedMemberKind.INTERNAL if account_class == "OFFICE_INTERNAL" else NamespacedMemberKind.EXTERNAL_CLIENT),
        idempotency_key=str(uuid.uuid5(uuid.NAMESPACE_URL, f"office-invitation-acceptance/v1/{invitation_id}")), authorizer=authorizer,
        execution=NamespaceMembershipExecution(NamespaceMembershipExecutionKind.INVITATION_ACCEPTANCE, invitation_id),
    )
    if result.replayed or authorizer.authorization_event_id is None:
        raise InvitationError("office_invitation_retryable", status_code=503)
    await cur.execute(
        "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
        "VALUES (%s,%s,%s,%s,'ACTIVE',%s)",
        (project_id, accepting_user_id, grant_role, audience, issuer_user_id))
    external_relation = None
    if audience == "CLIENT_PUBLISHED":
        await cur.execute(
            "INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) VALUES (%s,%s,%s,'ACTIVE')",
            (project_id, accepting_user_id, group_id))
        await cur.execute(
            "SELECT relation_id,version,recipient_group_id FROM office_external_relation WHERE project_id=%s AND user_id=%s FOR UPDATE",
            (project_id, accepting_user_id))
        external_relation = tuple(map(int, await cur.fetchone()))
    digest = invitation_acceptance_digest(
        invitation_id=invitation_id, issuer_user_id=int(issuer_user_id), target_tenant_id=int(tenant_id),
        project_id=int(project_id), target_user_id=accepting_user_id, grant_role=grant_role, audience=audience)
    await write_invitation_acceptance_receipt(
        cur, idempotency_key=str(uuid.uuid5(uuid.NAMESPACE_URL, f"office-invitation-acceptance/v1/{invitation_id}")), digest=digest, invitation_id=invitation_id,
        issuer_user_id=int(issuer_user_id), target_tenant_id=int(tenant_id), project_id=int(project_id),
        target_user_id=accepting_user_id, grant_role=grant_role, audience=audience, result=result,
        office_grant_version=1, authorization_event_id=authorizer.authorization_event_id,
        external_relation=external_relation)
    await cur.execute(
        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,bearer_token_hash,observed_outbox_state,correlation_id,occurred_at) "
        "VALUES (%s,'ACCEPTED',%s,%s,NULL,%s,%s,UTC_TIMESTAMP(6))",
        (invitation_id, bearer_event_id, accepting_user_id, outbox[0], correlation_id))
    await cur.execute(
        "UPDATE office_invitation SET status='ACCEPTED',version=version+1,accepted_user_id=%s,accepted_at=UTC_TIMESTAMP(6) "
        "WHERE invitation_id=%s AND status='ISSUED' AND version=%s",
        (accepting_user_id, invitation_id, int(version)))
    if cur.rowcount != 1:
        raise InvitationError("office_invitation_retryable", status_code=503)
    return False


@dataclass(frozen=True)
class ClaimedInvitationDelivery:
    outbox_id: str
    attempt_id: str
    invitation_id: str
    recipient: str
    bearer_token: str
    expires_at: str


def _decode_outbox_payload(ciphertext: bytes | str) -> tuple[str, str, str]:
    """Decrypt and validate one claimed payload without exposing its bearer."""
    try:
        raw = ciphertext.decode("ascii") if isinstance(ciphertext, bytes) else ciphertext
        payload = json.loads(decrypt_db_secret(raw))
        if (payload.get("domain") != "office-invitation-outbox/v1"
                or not isinstance(payload.get("email"), str)
                or not isinstance(payload.get("token"), str)
                or not isinstance(payload.get("expires_at"), str)):
            raise ValueError("invalid invitation payload")
        # Validation is intentionally separate from the returned value, so a
        # raw bearer never appears in a diagnostic expression or exception.
        _token_hash(payload["token"])
        return _normalize_email(payload["email"]), payload["token"], payload["expires_at"]
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise InvitationError("office_invitation_transport_unavailable", status_code=503) from error


async def claim_next_invitation_delivery_in_cursor(cur) -> ClaimedInvitationDelivery | None:
    """Fence one queued mail before any worker touches SMTP."""
    await cur.execute(
        "SELECT outbox_id,invitation_id,payload_ciphertext FROM office_invitation_outbox "
        "WHERE state='QUEUED' ORDER BY created_at,outbox_id LIMIT 1 FOR UPDATE SKIP LOCKED")
    row = await cur.fetchone()
    if row is None:
        return None
    outbox_id, invitation_id, ciphertext = row
    await cur.execute(
        "SELECT i.status,i.version,s.expires_at,s.correlation_id FROM office_invitation i JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id "
        "WHERE i.invitation_id=%s FOR UPDATE", (invitation_id,))
    invitation = await cur.fetchone()
    if invitation is None or invitation[0] != "ISSUED":
        raise InvitationError("office_invitation_integrity", status_code=503)
    now = await _db_now(cur)
    expiry = invitation[2].replace(tzinfo=dt.timezone.utc) if invitation[2].tzinfo is None else invitation[2].astimezone(dt.timezone.utc)
    if now > expiry:
        await cur.execute(
            "SELECT event_id FROM office_invitation_event WHERE invitation_id=%s AND event_kind='ISSUED' FOR UPDATE",
            (invitation_id,))
        issued = await cur.fetchone()
        if issued is None:
            raise InvitationError("office_invitation_integrity", status_code=503)
        await cur.execute(
            "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,bearer_token_hash,observed_outbox_state,correlation_id,occurred_at) "
            "VALUES (%s,'EXPIRED',%s,NULL,NULL,NULL,%s,UTC_TIMESTAMP(6))",
            (invitation_id, int(issued[0]), invitation[3]))
        await cur.execute(
            "UPDATE office_invitation SET status='EXPIRED',version=version+1 "
            "WHERE invitation_id=%s AND status='ISSUED' AND version=%s",
            (invitation_id, int(invitation[1])))
        if cur.rowcount != 1:
            raise InvitationError("office_invitation_retryable", status_code=503)
        return None
    recipient, token, expires_at = _decode_outbox_payload(ciphertext)
    attempt_id = str(uuid.uuid4())
    await cur.execute(
        "UPDATE office_invitation_outbox SET state='CLAIMED',attempt_id=%s,attempt_count=1,claimed_at=UTC_TIMESTAMP(6),"
        "updated_at=IF(UTC_TIMESTAMP(6)>updated_at,UTC_TIMESTAMP(6),DATE_ADD(updated_at,INTERVAL 1 MICROSECOND)) "
        "WHERE outbox_id=%s AND state='QUEUED'", (attempt_id, outbox_id))
    if cur.rowcount != 1:
        raise InvitationError("office_invitation_retryable", status_code=503)
    return ClaimedInvitationDelivery(str(outbox_id), attempt_id, str(invitation_id), recipient, token, expires_at)


async def finalize_invitation_delivery_in_cursor(
    cur, *, outbox_id: str, attempt_id: str, relay_accepted: bool,
    message_id: str | None = None, result_code: str | None = None,
) -> bool:
    """Persist the only two post-claim truths: accepted relay or UNKNOWN."""
    await cur.execute(
        "SELECT invitation_id,state,attempt_id,cancel_requested FROM office_invitation_outbox WHERE outbox_id=%s FOR UPDATE",
        (outbox_id,))
    row = await cur.fetchone()
    if row is None or row[1] != "CLAIMED" or row[2] != attempt_id:
        return False
    invitation_id = str(row[0])
    # A cancellation fences another send, but cannot rewrite what a relay
    # already accepted after this attempt was claimed.
    state = "SMTP_ACCEPTED" if relay_accepted else "UNKNOWN"
    if relay_accepted and (not isinstance(message_id, str) or not message_id or len(message_id) > 320 or not message_id.isascii()):
        raise InvitationError("office_invitation_integrity", status_code=503)
    code = ("SMTP_ACCEPTED" if relay_accepted else (result_code or "SMTP_UNKNOWN"))
    if not isinstance(code, str) or not code.isascii() or not 1 <= len(code) <= 64:
        raise InvitationError("office_invitation_integrity", status_code=503)
    await cur.execute(
        "UPDATE office_invitation_outbox SET state=%s,payload_ciphertext=NULL,message_id=%s,result_code=%s,"
        "finished_at=UTC_TIMESTAMP(6),updated_at=IF(UTC_TIMESTAMP(6)>updated_at,UTC_TIMESTAMP(6),DATE_ADD(updated_at,INTERVAL 1 MICROSECOND)) "
        "WHERE outbox_id=%s AND state='CLAIMED' AND attempt_id=%s",
        (state, message_id if relay_accepted else None, code, outbox_id, attempt_id))
    if cur.rowcount != 1:
        return False
    await cur.execute("SELECT event_id,correlation_id FROM office_invitation_event WHERE invitation_id=%s AND event_kind='ISSUED' FOR UPDATE", (invitation_id,))
    issued = await cur.fetchone()
    if issued is None:
        raise InvitationError("office_invitation_integrity", status_code=503)
    await cur.execute(
        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,bearer_token_hash,observed_outbox_state,correlation_id,occurred_at) "
        "VALUES (%s,'DELIVERY_OBSERVED',%s,NULL,NULL,%s,%s,UTC_TIMESTAMP(6))",
        (invitation_id, int(issued[0]), state, issued[1]))
    return True


async def settle_stale_invitation_claims_in_cursor(cur, *, config: InvitationConfig) -> int:
    """Turn abandoned fenced attempts into durable UNKNOWN, never QUEUED."""
    await cur.execute(
        "SELECT outbox_id,attempt_id FROM office_invitation_outbox WHERE state='CLAIMED' "
        "AND claimed_at < DATE_SUB(UTC_TIMESTAMP(6), INTERVAL %s SECOND) "
        "ORDER BY claimed_at,outbox_id FOR UPDATE SKIP LOCKED", (config.claim_timeout_seconds,))
    stale = await cur.fetchall()
    settled = 0
    for outbox_id, attempt_id in stale:
        if await finalize_invitation_delivery_in_cursor(
            cur, outbox_id=str(outbox_id), attempt_id=str(attempt_id),
            relay_accepted=False, result_code="SMTP_STALE"):
            settled += 1
    return settled


async def deliver_one_invitation() -> bool:
    """Run one delivery outside database locks and settle its fenced result.

    This is intentionally not scheduled by application startup.  A reviewed
    worker may call it; until then issuing stays durable but no SMTP action is
    performed merely because a web process imported this module.
    """
    from db.transaccion import AISLAMIENTO_ADMIN, transaccion
    import smtp_config

    from .invitation_config import load_invitation_config_from_environment
    config = load_invitation_config_from_environment()
    if config is None:
        raise InvitationError("office_invitation_transport_unavailable", status_code=503)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await settle_stale_invitation_claims_in_cursor(cur, config=config)
        delivery = await claim_next_invitation_delivery_in_cursor(cur)
    if delivery is None:
        return False
    accepted = False
    message_id: str | None = None
    result_code = "SMTP_UNKNOWN"
    try:
        settings = await smtp_config.cargar_settings()
        message = smtp_config.construir_mensaje(
            settings, delivery.recipient, "Axioma — Invitación a Oficina Virtual",
            "Use este código en la pantalla de aceptación de la Oficina Virtual:\n\n"
            f"{delivery.bearer_token}\n\nVence: {delivery.expires_at}",
            "<p>Use este código en la pantalla de aceptación de la Oficina Virtual:</p>"
            f"<p><code>{delivery.bearer_token}</code></p><p>Vence: {delivery.expires_at}</p>",
        )
        message_id = str(message["Message-ID"])
        await asyncio.to_thread(smtp_config.enviar, settings, message)
        accepted = True
    except BaseException:
        # A network exception, cancelled task, or malformed mail config is
        # delivery ambiguity after the claim.  Never put the payload back in
        # QUEUED and risk a second bearer send.
        result_code = "SMTP_UNKNOWN"
        try:
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await finalize_invitation_delivery_in_cursor(
                    cur, outbox_id=delivery.outbox_id, attempt_id=delivery.attempt_id,
                    relay_accepted=False, result_code=result_code)
        except Exception:
            # Leave a fenced CLAIMED row for the operator rather than claim a
            # false state after a second persistence failure.
            pass
        raise
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await finalize_invitation_delivery_in_cursor(
            cur, outbox_id=delivery.outbox_id, attempt_id=delivery.attempt_id,
            relay_accepted=accepted, message_id=message_id, result_code=result_code)
    return True
