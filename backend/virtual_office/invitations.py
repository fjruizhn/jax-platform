"""Transactional invitation issuance, revocation, and acceptance.

This is deliberately a service, not an ingress router.  It receives only
server-derived identities and current session proof references.  The raw
bearer is hashed immediately and never enters SQL, an exception, or a log.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

from crypto_secrets import clave_de_cifrado_utilizable, decrypt_db_secret, encrypt_secret
from pymysql.err import IntegrityError, OperationalError
from validacion import direccion_unica_valida

from .audit import record_authorization_event
from .authority import authorize_invitation_issuer_in_cursor, authorize_office_in_cursor
from .capabilities import OfficeOperation
from .invitation_config import InvitationConfig
from .invitation_email_i18n import render_invitation_email
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
    except (IntegrityError, OperationalError) as error:
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


class InvitationDeliveryMaintenanceFailure(RuntimeError):
    """A committed stale-claim repair that must alert before new delivery."""
    code = "stale_claims_settled"


@dataclass(frozen=True)
class IssuedInvitation:
    invitation_id: str
    expires_at: dt.datetime
    version: int = 1
    replayed: bool = False


@dataclass(frozen=True)
class ListedInvitation:
    invitation_id: str
    email: str
    audience: str
    status: str
    version: int
    expires_at: dt.datetime


_LIST_CURSOR_DOMAIN = "office-invitation-list/v1"
_LIST_CURSOR_MAX = 512


def encode_invitation_list_cursor(*, project_id: int, expires_at: dt.datetime,
                                  invitation_id: str) -> str:
    """Encode a position only; authorization is always re-resolved from DB."""
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
    payload = {
        "domain": _LIST_CURSOR_DOMAIN,
        "expires_at": expires_at.astimezone(dt.timezone.utc).isoformat(),
        "invitation_id": invitation_id,
        "project_id": project_id,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_invitation_list_cursor(cursor: str, *, project_id: int) -> tuple[dt.datetime, str]:
    """Fail closed on malformed or cross-project positions.

    The cursor is intentionally unsigned: changing it can only change a
    bounded position inside the same project/filter, after the database
    authority gate has succeeded.  It is never an authority credential.
    """
    if not isinstance(cursor, str) or not cursor or len(cursor) > _LIST_CURSOR_MAX:
        raise InvitationError("office_invitation_cursor_invalid", status_code=422)
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw.decode("ascii"))
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise InvitationError("office_invitation_cursor_invalid", status_code=422) from error
    if not isinstance(value, dict) or set(value) != {
        "domain", "expires_at", "invitation_id", "project_id"
    } or value["domain"] != _LIST_CURSOR_DOMAIN or value["project_id"] != project_id:
        raise InvitationError("office_invitation_cursor_invalid", status_code=422)
    invitation_id = value["invitation_id"]
    if not isinstance(invitation_id, str) or len(invitation_id) != 36:
        raise InvitationError("office_invitation_cursor_invalid", status_code=422)
    try:
        expires_at = dt.datetime.fromisoformat(value["expires_at"])
        parsed_invitation_id = uuid.UUID(invitation_id)
        if str(parsed_invitation_id) != invitation_id or expires_at.tzinfo is None:
            raise ValueError("cursor fields are not canonical")
        normalized_expiry = expires_at.astimezone(dt.timezone.utc)
    except (TypeError, ValueError, OverflowError) as error:
        raise InvitationError("office_invitation_cursor_invalid", status_code=422) from error
    return normalized_expiry, str(parsed_invitation_id)


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


def _idempotency_key(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as error:
        raise InvitationError("office_invitation_idempotency_invalid", status_code=422) from error
    normalized = str(parsed)
    if normalized != value.lower():
        raise InvitationError("office_invitation_idempotency_invalid", status_code=422)
    return normalized


def _mutation_digest(*, operation: str, actor_user_id: int, project_id: int,
                     email: str | None = None, audience: str | None = None,
                     invitation_id: str | None = None, expected_version: int | None = None) -> bytes:
    """Hash only stable user intent, never volatile proof or retry metadata."""
    fields = {
        "actor_user_id": actor_user_id, "audience": audience, "email": email,
        "expected_version": expected_version, "invitation_id": invitation_id,
        "operation": operation, "project_id": project_id,
    }
    return hashlib.sha256(json.dumps(fields, separators=(",", ":"), sort_keys=True).encode("utf-8")).digest()


async def _load_mutation_receipt(cur, *, idempotency_key: str, digest: bytes,
                                 operation: str, actor_user_id: int, project_id: int):
    """Lock a global retry key after authority is current and before mutation."""
    await cur.execute(
        "SELECT request_digest,operation,actor_user_id,project_id,invitation_id,expected_version,response_version,result_event_id,authorization_event_id,response_contract,expires_at "
        "FROM office_invitation_mutation_receipt WHERE idempotency_key=%s FOR UPDATE", (idempotency_key,))
    row = await cur.fetchone()
    if row is None:
        return None
    if (not hmac.compare_digest(bytes(row[0]), digest) or row[1] != operation
            or int(row[2]) != actor_user_id or int(row[3]) != project_id):
        raise InvitationError("office_invitation_idempotency_conflict")
    await cur.execute(
        "SELECT 1 FROM office_invitation_mutation_receipt r JOIN office_invitation i ON i.invitation_id=r.invitation_id "
        "JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id JOIN office_invitation_event e ON e.event_id=r.result_event_id "
        "JOIN office_authorization_event a ON a.event_id=r.authorization_event_id "
        "WHERE r.idempotency_key=%s AND r.response_contract='office-invitation-mutation-response/v1' "
        "AND i.project_id=r.project_id AND e.invitation_id=i.invitation_id AND "
        "((r.operation='ISSUE' AND r.expected_version=0 AND r.response_version=1 AND e.event_kind='ISSUED' AND e.actor_user_id=r.actor_user_id AND s.issuance_authorization_event_id=r.authorization_event_id) OR "
        "(r.operation='REVOKE' AND r.response_version=r.expected_version+1 AND e.event_kind='REVOKED' AND e.actor_user_id=r.actor_user_id AND a.event_kind='AUTHORIZATION' AND a.actor_user_id=r.actor_user_id AND a.operation='ADMINISTER_PROJECT_MEMBERS' AND a.requested_project_id=r.project_id AND a.project_id=r.project_id AND a.result='ALLOWED'))",
        (idempotency_key,))
    if await cur.fetchone() is None:
        raise InvitationError("office_invitation_integrity", status_code=503)
    return row


async def _write_mutation_receipt(cur, *, idempotency_key: str, digest: bytes,
                                  operation: str, actor_user_id: int, project_id: int,
                                  invitation_id: str, response_version: int,
                                  expected_version: int, result_event_id: int,
                                  authorization_event_id: int, expires_at: dt.datetime | None) -> None:
    """The receipt is last: it cannot attest a lifecycle mutation that rolled back."""
    await cur.execute(
        "INSERT INTO office_invitation_mutation_receipt "
        "(idempotency_key,request_digest,operation,actor_user_id,project_id,invitation_id,expected_version,response_version,result_event_id,authorization_event_id,response_contract,expires_at,created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'office-invitation-mutation-response/v1',%s,UTC_TIMESTAMP(6))",
        (idempotency_key, digest, operation, actor_user_id, project_id, invitation_id,
         expected_version, response_version, result_event_id, authorization_event_id,
         None if expires_at is None else expires_at.replace(tzinfo=None)),
    )


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


async def lock_office_actor_project_tenants_in_cursor(cur, *, actor_user_id: int,
                                                       project_id: int) -> None:
    """Acquire the common Office tenant prefix before actor/scope locks.

    Discovery reads are deliberately unlocked and advisory only.  The
    canonical resolver re-reads actor, scope, profile, and grants after this
    prefix, so a deletion or tenant change cannot be authorized from this
    snapshot.  Missing rows are likewise decided by that definitive read.
    """
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (actor_user_id,))
    actor_tenant = await cur.fetchone()
    await cur.execute("SELECT tenant_id FROM jax_project_scope WHERE project_id=%s", (project_id,))
    scope_tenant = await cur.fetchone()
    if actor_tenant is None or scope_tenant is None:
        return
    for tenant_id in sorted({int(actor_tenant[0]), int(scope_tenant[0])}):
        await cur.execute("SELECT tenant_id FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (tenant_id,))
        # Do not decide from the discovery snapshot.  The resolver below is
        # authoritative and returns the opaque denial for a missing tenant.
        await cur.fetchone()


async def _issuer_snapshot(cur, *, actor_user_id: int, project_id: int,
                           proof_event_id: int, mfa_config: MFAConfig):
    # Establish the same tenant-first lock order used by invitation
    # acceptance and JAX membership.  These are discovery hints only: the
    # canonical resolver below re-reads actor, tenant, scope and grants under
    # lock before it permits issuance or revocation.
    await lock_office_actor_project_tenants_in_cursor(
        cur, actor_user_id=actor_user_id, project_id=project_id,
    )
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


async def can_manage_invitations_in_cursor(cur, *, actor_user_id: int,
                                           project_id: int, proof_event_id: int | None,
                                           mfa_config: MFAConfig | None) -> bool:
    """Return the exact issue/revoke capability without exposing a grant.

    Expected authority and MFA denials become ``False`` for the project
    detail capability.  Configuration failure remains operational failure;
    callers must not present a false capability when the system could not
    evaluate it.
    """
    if mfa_config is None:
        raise InvitationError("office_invitation_unavailable", status_code=503)
    if type(proof_event_id) is not int or proof_event_id < 1:
        return False
    try:
        await _issuer_snapshot(
            cur, actor_user_id=actor_user_id, project_id=project_id,
            proof_event_id=proof_event_id, mfa_config=mfa_config,
        )
    except InvitationError as error:
        if error.status_code == 403:
            return False
        raise
    return True


async def list_active_invitations_in_cursor(
    cur, *, actor_user_id: int, project_id: int, proof_event_id: int | None,
    mfa_config: MFAConfig | None, limit: int, cursor: str | None,
) -> tuple[list[ListedInvitation], str | None]:
    """List a project administrator's live invitations by an indexed keyset.

    The issuer snapshot runs before any invitation/e-mail query.  Every
    continuation is scoped to the project and repeats this gate.
    """
    if mfa_config is None:
        raise InvitationError("office_invitation_unavailable", status_code=503)
    if type(actor_user_id) is not int or type(project_id) is not int:
        raise InvitationError("office_invitation_invalid", status_code=422)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvitationError("office_invitation_invalid", status_code=422)
    await _issuer_snapshot(
        cur, actor_user_id=actor_user_id, project_id=project_id,
        proof_event_id=proof_event_id, mfa_config=mfa_config,
    )
    predicate = ""
    params: tuple[object, ...] = (project_id,)
    if cursor is not None:
        expires_at, invitation_id = decode_invitation_list_cursor(cursor, project_id=project_id)
        # MariaDB datetime parameters are naive UTC values; keep the position
        # identical to the stored UTC timestamp used for the tie-break range.
        predicate = (" AND (i.expires_at > %s OR "
                     "(i.expires_at = %s AND i.invitation_id > %s))")
        naive_expiry = expires_at.replace(tzinfo=None)
        params += (naive_expiry, naive_expiry, invitation_id)
    await cur.execute(
        "SELECT i.invitation_id,i.email_normalized,s.audience,i.status,i.version,i.expires_at "
        "FROM office_invitation i JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id "
        "WHERE i.project_id=%s AND i.status='ISSUED' AND i.expires_at>UTC_TIMESTAMP(6)"
        + predicate + " ORDER BY i.expires_at ASC,i.invitation_id ASC LIMIT %s",
        (*params, limit + 1),
    )
    rows = await cur.fetchall()
    page = rows[:limit]
    result = [ListedInvitation(
        invitation_id=str(row[0]), email=str(row[1]), audience=str(row[2]),
        status=str(row[3]), version=int(row[4]),
        expires_at=row[5].replace(tzinfo=dt.timezone.utc)
        if row[5].tzinfo is None else row[5].astimezone(dt.timezone.utc),
    ) for row in page]
    next_cursor = None
    if len(rows) > limit and result:
        last = result[-1]
        next_cursor = encode_invitation_list_cursor(
            project_id=project_id, expires_at=last.expires_at,
            invitation_id=last.invitation_id,
        )
    return result, next_cursor


async def issue_invitation_in_cursor(
    cur, *, actor_user_id: int, project_id: int, email: str, audience: str,
    proof_event_id: int | None, mfa_config: MFAConfig | None,
    invitation_config: InvitationConfig, idempotency_key: str,
) -> IssuedInvitation:
    """Derive the target and audience server-side, then queue delivery."""
    if type(actor_user_id) is not int or type(project_id) is not int:
        raise InvitationError("office_invitation_invalid", status_code=422)
    if mfa_config is None or type(proof_event_id) is not int or proof_event_id < 1:
        raise InvitationError("office_invitation_issuer_denied", status_code=403)
    normalized_email = _normalize_email(email)
    idempotency_key = _idempotency_key(idempotency_key)
    if audience not in {"INTERNAL", "CLIENT_PUBLISHED"}:
        raise InvitationError("office_invitation_target_denied", status_code=409)
    target_account_class = "OFFICE_INTERNAL" if audience == "INTERNAL" else "EXTERNAL_CLIENT"
    decision, proof, profile_version, grant_version = await _issuer_snapshot(
        cur, actor_user_id=actor_user_id, project_id=project_id,
        proof_event_id=proof_event_id, mfa_config=mfa_config,
    )
    mutation_digest = _mutation_digest(
        operation="ISSUE", actor_user_id=actor_user_id, project_id=project_id,
        email=normalized_email, audience=audience,
    )
    receipt = await _load_mutation_receipt(
        cur, idempotency_key=idempotency_key, digest=mutation_digest, operation="ISSUE",
        actor_user_id=actor_user_id, project_id=project_id,
    )
    if receipt is not None:
        expires_at = receipt[10]
        if expires_at is None:
            raise InvitationError("office_invitation_integrity", status_code=503)
        expires_at = expires_at.replace(tzinfo=dt.timezone.utc) if expires_at.tzinfo is None else expires_at.astimezone(dt.timezone.utc)
        return IssuedInvitation(invitation_id=str(receipt[4]), expires_at=expires_at,
                                version=int(receipt[6]), replayed=True)
    # The global idempotency key may race an issuer in another project.  Keep
    # all provisional lifecycle rows inside a savepoint so a duplicate key can
    # be re-read without committing a second invitation.
    await cur.execute("SAVEPOINT office_invitation_issue")
    await cur.execute(
        "SELECT tenant_id,status,authorization_namespace FROM jax_project_scope WHERE project_id=%s FOR UPDATE", (project_id,))
    scope = await cur.fetchone()
    if scope is None or scope[1] != "ACTIVE" or scope[2] != "OFFICE_CONFIDENTIAL":
        raise InvitationError("office_invitation_target_denied", status_code=409)
    tenant_id = int(scope[0])
    await cur.execute(
        "SELECT user_id,tenant_id,account_class,status FROM jax_users WHERE BINARY email=BINARY %s",
        (normalized_email,))
    target = await cur.fetchone()
    # Issuance is deliberately opaque about account existence and compatibility.
    # A nonmatching account receives the same pending invitation/outbox as an
    # unknown address; never lock that other tenant's user while retaining the
    # issuer tenant lock.  A compatible target is locked only after its tenant
    # is already in the canonical tenant set, then revalidated under lock.
    target_user_id = None
    if target is not None and (int(target[1]) == tenant_id and target[2] == target_account_class
                               and target[3] == "active"):
        await cur.execute(
            "SELECT user_id,tenant_id,account_class,status FROM jax_users WHERE user_id=%s FOR UPDATE",
            (target[0],))
        locked_target = await cur.fetchone()
        if (locked_target is not None and int(locked_target[1]) == tenant_id
                and locked_target[2] == target_account_class and locked_target[3] == "active"):
            target_user_id = int(locked_target[0])
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
        "INSERT INTO office_invitation (invitation_id,issuance_id,project_id,email_normalized,target_user_id,expires_at) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (invitation_id, issuance_id, project_id, normalized_email, target_user_id,
         expires_at.replace(tzinfo=None)))
    await cur.execute(
        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,bearer_token_hash,observed_outbox_state,correlation_id,authorization_event_id,invocation_digest,occurred_at) "
        "VALUES (%s,'ISSUED',NULL,%s,NULL,NULL,%s,%s,%s,UTC_TIMESTAMP(6))", (invitation_id, actor_user_id, correlation_id, authorization_event_id, mutation_digest))
    issued_event_id = int(cur.lastrowid)
    await cur.execute(
        "INSERT INTO office_invitation_outbox (outbox_id,invitation_id,state,attempt_id,attempt_count,cancel_requested,payload_ciphertext,message_id,result_code,claimed_at,finished_at,created_at,updated_at) "
        "VALUES (%s,%s,'QUEUED',NULL,0,FALSE,%s,NULL,NULL,NULL,NULL,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
        (outbox_id, invitation_id, payload))
    try:
        await _write_mutation_receipt(
            cur, idempotency_key=idempotency_key, digest=mutation_digest, operation="ISSUE",
            actor_user_id=actor_user_id, project_id=project_id, invitation_id=invitation_id,
            expected_version=0, response_version=1, result_event_id=issued_event_id,
            authorization_event_id=authorization_event_id, expires_at=expires_at,
        )
    except (IntegrityError, OperationalError) as error:
        if not (getattr(error, "args", ()) and error.args[0] == 1062):
            raise
        await cur.execute("ROLLBACK TO SAVEPOINT office_invitation_issue")
        await cur.execute("RELEASE SAVEPOINT office_invitation_issue")
        winner = await _load_mutation_receipt(
            cur, idempotency_key=idempotency_key, digest=mutation_digest, operation="ISSUE",
            actor_user_id=actor_user_id, project_id=project_id)
        if winner is None:
            raise InvitationError("office_invitation_retryable", status_code=503) from error
        winner_expiry = winner[10]
        if winner_expiry is None:
            raise InvitationError("office_invitation_integrity", status_code=503)
        winner_expiry = (winner_expiry.replace(tzinfo=dt.timezone.utc)
                         if winner_expiry.tzinfo is None else winner_expiry.astimezone(dt.timezone.utc))
        return IssuedInvitation(invitation_id=str(winner[4]), expires_at=winner_expiry,
                                version=int(winner[6]), replayed=True)
    await cur.execute("RELEASE SAVEPOINT office_invitation_issue")
    return IssuedInvitation(invitation_id=invitation_id, expires_at=expires_at)


async def revoke_invitation_in_cursor(
    cur, *, actor_user_id: int, project_id: int, invitation_id: str, expected_version: int,
    proof_event_id: int | None,
    mfa_config: MFAConfig | None,
    idempotency_key: str,
) -> tuple[int, bool]:
    """Revoke an unconsumed invitation and let the DB guard cancel delivery."""
    if mfa_config is None or type(proof_event_id) is not int or type(expected_version) is not int or expected_version < 1:
        raise InvitationError("office_invitation_issuer_denied", status_code=403)
    idempotency_key = _idempotency_key(idempotency_key)
    decision, _proof, _profile_version, _grant_version = await _issuer_snapshot(cur, actor_user_id=actor_user_id, project_id=project_id,
                           proof_event_id=proof_event_id, mfa_config=mfa_config)
    mutation_digest = _mutation_digest(
        operation="REVOKE", actor_user_id=actor_user_id, project_id=project_id,
        invitation_id=invitation_id, expected_version=expected_version,
    )
    receipt = await _load_mutation_receipt(
        cur, idempotency_key=idempotency_key, digest=mutation_digest, operation="REVOKE",
        actor_user_id=actor_user_id, project_id=project_id,
    )
    if receipt is not None:
        return int(receipt[6]), True
    # Revoke receipts share the global key space with issuance and other
    # projects.  A losing unique-key race must discard its lifecycle mutation
    # before the winner's receipt is re-read.
    await cur.execute("SAVEPOINT office_invitation_revoke")
    await cur.execute(
        "SELECT i.status,i.version,i.project_id,s.correlation_id FROM office_invitation i "
        "JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id "
        "WHERE i.invitation_id=%s FOR UPDATE", (invitation_id,))
    invitation = await cur.fetchone()
    if invitation is None or int(invitation[2]) != project_id:
        raise InvitationError("office_invitation_not_found", status_code=404)
    if invitation[0] == "REVOKED":
        raise InvitationError("office_invitation_terminal")
    if invitation[0] != "ISSUED":
        raise InvitationError("office_invitation_terminal")
    if int(invitation[1]) != expected_version:
        raise InvitationError("office_invitation_version_conflict")
    await cur.execute(
        "SELECT event_id FROM office_invitation_event WHERE invitation_id=%s AND event_kind='ISSUED' FOR UPDATE",
        (invitation_id,))
    issued = await cur.fetchone()
    if issued is None:
        raise InvitationError("office_invitation_integrity", status_code=503)
    authorization_event_id = await record_authorization_event(cur, decision)
    await cur.execute(
        "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,bearer_token_hash,observed_outbox_state,correlation_id,authorization_event_id,invocation_digest,occurred_at) "
        "VALUES (%s,'REVOKED',%s,%s,NULL,NULL,%s,%s,%s,UTC_TIMESTAMP(6))",
        (invitation_id, int(issued[0]), actor_user_id, invitation[3], authorization_event_id, mutation_digest))
    revoked_event_id = int(cur.lastrowid)
    await cur.execute(
        "UPDATE office_invitation SET status='REVOKED',version=version+1,revoked_by_user_id=%s,revoked_at=UTC_TIMESTAMP(6) "
        "WHERE invitation_id=%s AND status='ISSUED' AND version=%s",
        (actor_user_id, invitation_id, expected_version))
    if cur.rowcount != 1:
        raise InvitationError("office_invitation_retryable", status_code=503)
    response_version = expected_version + 1
    try:
        await _write_mutation_receipt(
            cur, idempotency_key=idempotency_key, digest=mutation_digest, operation="REVOKE",
            actor_user_id=actor_user_id, project_id=project_id, invitation_id=invitation_id,
            expected_version=expected_version, response_version=response_version,
            result_event_id=revoked_event_id, authorization_event_id=authorization_event_id, expires_at=None,
        )
    except (IntegrityError, OperationalError) as error:
        if not (getattr(error, "args", ()) and error.args[0] == 1062):
            raise
        await cur.execute("ROLLBACK TO SAVEPOINT office_invitation_revoke")
        await cur.execute("RELEASE SAVEPOINT office_invitation_revoke")
        winner = await _load_mutation_receipt(
            cur, idempotency_key=idempotency_key, digest=mutation_digest, operation="REVOKE",
            actor_user_id=actor_user_id, project_id=project_id)
        if winner is None:
            raise InvitationError("office_invitation_retryable", status_code=503) from error
        return int(winner[6]), True
    await cur.execute("RELEASE SAVEPOINT office_invitation_revoke")
    return response_version, False


class _InvitationAcceptanceAuthorizer:
    """Trusted JXP proof resolver used only by JAX's typed capability call."""
    def __init__(self, *, invitation_id: str, issuer_user_id: int, target_user_id: int,
                 target_tenant_id: int, project_id: int, mfa_version: int,
                 proof_event_id: int, mfa_at: dt.datetime):
        self.invitation_id = invitation_id
        self.issuer_user_id = issuer_user_id
        self.target_user_id = target_user_id
        self.target_tenant_id = target_tenant_id
        self.project_id = project_id
        self.mfa_version = mfa_version
        self.proof_event_id = proof_event_id
        self.mfa_at = mfa_at
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
        decision = await authorize_invitation_issuer_in_cursor(
            cursor, issuer_user_id=self.issuer_user_id, project_id=self.project_id,
            proof_event_id=self.proof_event_id, issued_mfa_version=self.mfa_version,
            issued_mfa_at=self.mfa_at)
        if not decision.allowed:
            raise AuthorizationDenied("Office invitation issuer authority is no longer current")
        self.authorization_event_id = await record_authorization_event(cursor, decision)


async def _expire_if_needed(cur, *, invitation_id: str, status: str, version: int,
                            issued_event_id: int, correlation_id: bytes, expires_at: dt.datetime) -> bool:
    now = await _db_now(cur)
    if status != "ISSUED" or now < (expires_at.replace(tzinfo=dt.timezone.utc) if expires_at.tzinfo is None else expires_at):
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


async def _translate_acceptance_deadline_guard(cur, error: Exception, *, expires_at: dt.datetime) -> None:
    """Convert a SQL deadline guard race into the public expired result."""
    if getattr(error, "args", ()) and error.args[0] == 1644:
        expiry = expires_at.replace(tzinfo=dt.timezone.utc) if expires_at.tzinfo is None else expires_at.astimezone(dt.timezone.utc)
        if await _db_now(cur) >= expiry:
            raise InvitationError("office_invitation_expired", status_code=410) from error
    raise error


async def invitation_bearer_preflight(bearer_token: str) -> bool:
    """Cheap, non-disclosing SHA-256 check performed before bcrypt.

    The transaction still rereads the same digest under lock before consuming
    anything.  This only prevents arbitrary network input from buying bcrypt
    work when it cannot name an issued invitation.
    """
    token_hash = _token_hash(bearer_token)
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM office_invitation i JOIN office_invitation_issuance s "
                "ON s.issuance_id=i.issuance_id WHERE s.token_hash=%s AND i.status='ISSUED' LIMIT 1",
                (token_hash,))
            return await cur.fetchone() is not None


async def accept_invitation_in_cursor(
    cur, *, accepting_user_id: int | None, accepting_email: str | None,
    new_password_hash: str | None, bearer_token: str,
) -> bool | None:
    """Consume one bearer through JAX and the Office companion atomically."""
    token_hash = _token_hash(bearer_token)
    # Discover only the immutable issuance binding without a lock, then take
    # the issuer/project locks before the lifecycle row.  The locked read
    # below must match this hint exactly.  This gives acceptance the same
    # actor → scope → invitation order as revoke and prevents their former
    # lock inversion.
    await cur.execute(
        "SELECT issued_by_user_id,project_id,tenant_id,target_user_id,issuer_proof_event_id,issuer_mfa_version,issuer_mfa_at "
        "FROM office_invitation_issuance WHERE token_hash=%s", (token_hash,))
    discovered = await cur.fetchone()
    if discovered is None:
        raise InvitationError("office_invitation_invalid", status_code=403)
    (discovered_issuer, discovered_project, discovered_tenant, discovered_target,
     discovered_proof, discovered_mfa_version, discovered_mfa_at) = discovered
    await cur.execute("SELECT tenant_id FROM jax_project_scope WHERE project_id=%s", (discovered_project,))
    discovered_scope = await cur.fetchone()
    tenant_ids = {int(discovered_tenant)}
    if discovered_scope is not None:
        tenant_ids.add(int(discovered_scope[0]))
    for tenant_id in sorted(tenant_ids):
        await cur.execute("SELECT tenant_id FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (tenant_id,))
        if await cur.fetchone() is None:
            raise InvitationError("office_invitation_invalid", status_code=403)
    await cur.execute("SELECT user_id FROM jax_users WHERE user_id=%s FOR UPDATE", (discovered_issuer,))
    if await cur.fetchone() is None:
        raise InvitationError("office_invitation_invalid", status_code=403)
    if discovered_target is not None and int(discovered_target) != int(discovered_issuer):
        await cur.execute("SELECT user_id FROM jax_users WHERE user_id=%s FOR UPDATE", (discovered_target,))
        if await cur.fetchone() is None:
            raise InvitationError("office_invitation_invalid", status_code=403)
    predecision = await authorize_invitation_issuer_in_cursor(
        cur, issuer_user_id=int(discovered_issuer), project_id=int(discovered_project),
        proof_event_id=int(discovered_proof), issued_mfa_version=int(discovered_mfa_version),
        issued_mfa_at=discovered_mfa_at)
    if not predecision.allowed:
        raise InvitationError("office_invitation_issuer_denied", status_code=403)
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
    if (int(issuer_user_id), int(project_id), int(proof_event_id), int(issuer_mfa_version), issuer_mfa_at) != (
            int(discovered_issuer), int(discovered_project), int(discovered_proof),
            int(discovered_mfa_version), discovered_mfa_at):
        raise InvitationError("office_invitation_retryable", status_code=503)
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
    await cur.execute(
        "SELECT state FROM office_invitation_outbox WHERE invitation_id=%s FOR UPDATE", (invitation_id,))
    outbox = await cur.fetchone()
    if outbox is None or outbox[0] not in {"QUEUED", "CLAIMED", "SMTP_ACCEPTED", "UNKNOWN", "CANCELLED"}:
        raise InvitationError("office_invitation_integrity", status_code=503)
    try:
        await cur.execute(
            "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,bearer_token_hash,observed_outbox_state,correlation_id,occurred_at) "
            "VALUES (%s,'INVITATION_BEARER_PRESENTED',%s,NULL,%s,NULL,%s,UTC_TIMESTAMP(6))",
            (invitation_id, int(issued[0]), token_hash, correlation_id))
    except (IntegrityError, OperationalError) as error:
        await _translate_acceptance_deadline_guard(cur, error, expires_at=expires_at)
    bearer_event_id = int(cur.lastrowid)
    from db.connection import get_pool
    from jax.memory.b9_mariadb import MariaDBB9Store
    from jax.memory.project_authority import (NamespacedMemberKind, NamespaceMembershipExecution,
                                              NamespaceMembershipExecutionKind, ProjectAuthorityAdmin,
                                              ProjectNamespace)
    authorizer = _InvitationAcceptanceAuthorizer(
        invitation_id=invitation_id, issuer_user_id=int(issuer_user_id), target_user_id=accepting_user_id,
        target_tenant_id=int(tenant_id), project_id=int(project_id), mfa_version=int(issuer_mfa_version),
        proof_event_id=int(proof_event_id), mfa_at=issuer_mfa_at)
    try:
        result = await ProjectAuthorityAdmin(MariaDBB9Store(await get_pool())).grant_namespaced_member_in_transaction(
            cur, actor_user_id=int(issuer_user_id), target_tenant_id=int(tenant_id), project_id=int(project_id),
            target_user_id=accepting_user_id, namespace=ProjectNamespace.OFFICE_CONFIDENTIAL,
            member_kind=(NamespacedMemberKind.INTERNAL if account_class == "OFFICE_INTERNAL" else NamespacedMemberKind.EXTERNAL_CLIENT),
            idempotency_key=str(uuid.uuid5(uuid.NAMESPACE_URL, f"office-invitation-acceptance/v1/{invitation_id}")), authorizer=authorizer,
            execution=NamespaceMembershipExecution(NamespaceMembershipExecutionKind.INVITATION_ACCEPTANCE, invitation_id),
        )
    except Exception as error:
        from jax.memory.project_authority import AuthorizationDenied, ProjectStateConflict
        if isinstance(error, (AuthorizationDenied, ProjectStateConflict)):
            raise InvitationError("office_invitation_issuer_denied", status_code=403) from error
        raise
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
    try:
        await cur.execute(
            "INSERT INTO office_invitation_event (invitation_id,event_kind,causation_event_id,actor_user_id,bearer_token_hash,observed_outbox_state,correlation_id,occurred_at) "
            "VALUES (%s,'ACCEPTED',%s,%s,NULL,%s,%s,UTC_TIMESTAMP(6))",
            (invitation_id, bearer_event_id, accepting_user_id, outbox[0], correlation_id))
    except (IntegrityError, OperationalError) as error:
        await _translate_acceptance_deadline_guard(cur, error, expires_at=expires_at)
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
        "SELECT invitation_id FROM office_invitation_outbox WHERE state='QUEUED' "
        "ORDER BY created_at,outbox_id LIMIT 1")
    row = await cur.fetchone()
    if row is None:
        return None
    invitation_id = row[0]
    await cur.execute(
        "SELECT i.status,i.version,s.expires_at,s.correlation_id FROM office_invitation i JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id "
        "WHERE i.invitation_id=%s FOR UPDATE", (invitation_id,))
    invitation = await cur.fetchone()
    if invitation is None or invitation[0] != "ISSUED":
        raise InvitationError("office_invitation_integrity", status_code=503)
    # Every invitation path locks the lifecycle first and its outbox second.
    await cur.execute(
        "SELECT outbox_id,payload_ciphertext FROM office_invitation_outbox "
        "WHERE invitation_id=%s AND state='QUEUED' FOR UPDATE", (invitation_id,))
    outbox = await cur.fetchone()
    if outbox is None:
        return None
    outbox_id, ciphertext = outbox
    now = await _db_now(cur)
    expiry = invitation[2].replace(tzinfo=dt.timezone.utc) if invitation[2].tzinfo is None else invitation[2].astimezone(dt.timezone.utc)
    if now >= expiry:
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
    await cur.execute("SELECT invitation_id FROM office_invitation_outbox WHERE outbox_id=%s", (outbox_id,))
    candidate = await cur.fetchone()
    if candidate is None:
        return False
    await cur.execute("SELECT invitation_id FROM office_invitation WHERE invitation_id=%s FOR UPDATE", (candidate[0],))
    if await cur.fetchone() is None:
        raise InvitationError("office_invitation_integrity", status_code=503)
    await cur.execute(
        "SELECT invitation_id,state,attempt_id,cancel_requested FROM office_invitation_outbox WHERE outbox_id=%s FOR UPDATE",
        (outbox_id,))
    row = await cur.fetchone()
    if row is None or row[2] != attempt_id:
        return False
    invitation_id = str(row[0])
    # A cancellation fences another send, but cannot rewrite what a relay
    # already accepted after this attempt was claimed.
    state = "SMTP_ACCEPTED" if relay_accepted else "UNKNOWN"
    if row[1] == "UNKNOWN":
        # A stale lease records ambiguity.  The still-running SMTP thread may
        # later obtain a relay acceptance for this exact fenced attempt; that
        # refines uncertainty and never permits another send.
        if not relay_accepted:
            return False
    elif row[1] != "CLAIMED":
        return False
    if relay_accepted and (not isinstance(message_id, str) or not message_id or len(message_id) > 320 or not message_id.isascii()):
        raise InvitationError("office_invitation_integrity", status_code=503)
    code = ("SMTP_ACCEPTED" if relay_accepted else (result_code or "SMTP_UNKNOWN"))
    if not isinstance(code, str) or not code.isascii() or not 1 <= len(code) <= 64:
        raise InvitationError("office_invitation_integrity", status_code=503)
    await cur.execute(
        "UPDATE office_invitation_outbox SET state=%s,payload_ciphertext=NULL,message_id=%s,result_code=%s,"
        "finished_at=UTC_TIMESTAMP(6),updated_at=IF(UTC_TIMESTAMP(6)>updated_at,UTC_TIMESTAMP(6),DATE_ADD(updated_at,INTERVAL 1 MICROSECOND)) "
        "WHERE outbox_id=%s AND state=%s AND attempt_id=%s",
        (state, message_id if relay_accepted else None, code, outbox_id, row[1], attempt_id))
    if cur.rowcount != 1:
        return False
    # Lease expiry is an operational marker, not an observed transport fact:
    # the in-flight thread may still refine it to SMTP_ACCEPTED.  A real
    # ambiguous relay result remains terminal and receives its one evidence row.
    if state == "UNKNOWN" and code == "SMTP_STALE":
        return True
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
        "SELECT outbox_id,attempt_id,invitation_id FROM office_invitation_outbox WHERE state='CLAIMED' "
        "AND claimed_at < DATE_SUB(UTC_TIMESTAMP(6), INTERVAL %s SECOND) "
        "ORDER BY claimed_at,outbox_id LIMIT %s",
        (config.claim_timeout_seconds, config.stale_claim_batch_limit))
    stale = await cur.fetchall()
    settled = 0
    for outbox_id, attempt_id, invitation_id in stale:
        # Match acceptance/revocation's lock order: invitation before outbox.
        await cur.execute("SELECT invitation_id FROM office_invitation WHERE invitation_id=%s FOR UPDATE", (invitation_id,))
        if await cur.fetchone() is None:
            continue
        if await finalize_invitation_delivery_in_cursor(
            cur, outbox_id=str(outbox_id), attempt_id=str(attempt_id),
            relay_accepted=False, result_code="SMTP_STALE"):
            settled += 1
    return settled


async def expire_stale_invitations_in_cursor(cur, *, config: InvitationConfig) -> int:
    """Expire one bounded, indexed batch without rewriting relay history."""
    await cur.execute(
        "SELECT invitation_id FROM office_invitation "
        "FORCE INDEX (idx_office_invitation_status_expiry) "
        "WHERE status='ISSUED' AND expires_at < UTC_TIMESTAMP(6) "
        "ORDER BY expires_at,invitation_id LIMIT %s",
        (config.expiry_sweep_batch_limit,))
    candidates = await cur.fetchall()
    expired = 0
    for (invitation_id,) in candidates:
        # Re-read and lock the lifecycle row; a concurrent acceptance or
        # revocation may have won after the indexed candidate scan.
        await cur.execute(
            "SELECT i.status,i.version,i.expires_at,s.correlation_id "
            "FROM office_invitation i JOIN office_invitation_issuance s ON s.issuance_id=i.issuance_id "
            "WHERE i.invitation_id=%s FOR UPDATE", (invitation_id,))
        row = await cur.fetchone()
        if row is None:
            continue
        status, version, expires_at, correlation_id = row
        await cur.execute(
            "SELECT event_id FROM office_invitation_event "
            "WHERE invitation_id=%s AND event_kind='ISSUED' FOR UPDATE", (invitation_id,))
        issued = await cur.fetchone()
        if issued is None:
            raise InvitationError("office_invitation_integrity", status_code=503)
        if await _expire_if_needed(
            cur, invitation_id=str(invitation_id), status=status, version=int(version),
            issued_event_id=int(issued[0]), correlation_id=correlation_id,
            expires_at=expires_at):
            expired += 1
    return expired


async def deliver_one_invitation(*, config: InvitationConfig | None = None, smtp_settings=None,
                                 maintain: bool = True) -> bool:
    """Run one delivery outside database locks and settle its fenced result.

    This is intentionally not scheduled by application startup.  A reviewed
    worker may call it; until then issuing stays durable but no SMTP action is
    performed merely because a web process imported this module.
    """
    from db.transaccion import AISLAMIENTO_ADMIN, transaccion
    import smtp_config

    from .invitation_config import load_invitation_config_from_environment
    config = config or load_invitation_config_from_environment()
    if config is None:
        raise InvitationError("office_invitation_transport_unavailable", status_code=503)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        if maintain:
            await expire_stale_invitations_in_cursor(cur, config=config)
            await settle_stale_invitation_claims_in_cursor(cur, config=config)
        delivery = await claim_next_invitation_delivery_in_cursor(cur)
    if delivery is None:
        return False
    # The dedicated worker always passes preflighted settings.  This fallback
    # keeps the older one-shot library API usable for maintenance callers; it
    # is deliberately not used by the timer entry point.
    settings = smtp_settings if smtp_settings is not None else await smtp_config.cargar_settings()
    accepted = False
    message_id: str | None = None
    result_code = "SMTP_UNKNOWN"
    try:
        subject, text, body_html = _invitation_email(config, delivery)
        message = smtp_config.construir_mensaje(
            settings, delivery.recipient, subject, text, body_html,
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
        except Exception:  # fail-soft: the original relay failure is already preserved; leave the CLAIMED lease fenced when persisting its failure also fails
            # Leave a fenced CLAIMED row for the operator rather than claim a
            # false state after a second persistence failure.
            pass
        raise
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await finalize_invitation_delivery_in_cursor(
            cur, outbox_id=delivery.outbox_id, attempt_id=delivery.attempt_id,
            relay_accepted=accepted, message_id=message_id, result_code=result_code)
    return True


@dataclass(frozen=True)
class InvitationDeliveryRun:
    delivered: int
    expired: int
    stale_settled: int


async def drain_invitation_deliveries() -> InvitationDeliveryRun:
    """Deliver one bounded batch after all dependencies pass preflight.

    The dedicated systemd worker invokes this function.  It never starts from
    the web application and deliberately propagates claim, transport and
    finalization failures so the unit is failed and alerted.  A relay failure
    is still finalized as UNKNOWN before it is propagated; no bearer returns
    to QUEUED after an attempt begins.
    """
    from db.transaccion import AISLAMIENTO_ADMIN, transaccion
    import smtp_config

    from .invitation_config import load_invitation_config_from_environment
    config = load_invitation_config_from_environment()
    if config is None:
        raise InvitationError("office_invitation_transport_unavailable", status_code=503)
    # Read validated settings before claiming *any* row.  This is intentionally
    # outside the transaction: smtp_config owns its own short read transaction.
    settings = await smtp_config.cargar_settings()
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        expired = await expire_stale_invitations_in_cursor(cur, config=config)
        stale_settled = await settle_stale_invitation_claims_in_cursor(cur, config=config)
    # The transaction above has committed by the time this is raised.  Stop
    # here so an operator/alert sees the abandoned attempt before a later
    # queued bearer is claimed by this same oneshot execution.
    if stale_settled:
        raise InvitationDeliveryMaintenanceFailure()
    delivered = 0
    for _ in range(config.delivery_batch_limit):
        if not await deliver_one_invitation(config=config, smtp_settings=settings, maintain=False):
            break
        delivered += 1
    return InvitationDeliveryRun(delivered=delivered, expired=expired, stale_settled=stale_settled)


def _invitation_acceptance_url(config: InvitationConfig, bearer_token: str) -> str:
    """Keep the bearer in the fragment so it never reaches the web server."""
    _token_hash(bearer_token)
    return f"{config.public_base_url}/office/invitation#{urlencode({'token': bearer_token})}"


def _invitation_email(config: InvitationConfig, delivery: ClaimedInvitationDelivery) -> tuple[str, str, str]:
    """Render through the recipient catalog without exposing a raw bearer code."""
    url = _invitation_acceptance_url(config, delivery.bearer_token)
    # The invitation has no account or stored recipient preference yet.
    return render_invitation_email(recipient_locale=None, url=url, expires_at=delivery.expires_at)
