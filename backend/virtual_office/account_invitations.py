"""Human Office-account invitations, intentionally independent of projects."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass

from crypto_secrets import clave_de_cifrado_utilizable, decrypt_db_secret, encrypt_secret
from pymysql.err import IntegrityError, OperationalError
from validacion import direccion_unica_valida

from .audit import record_authorization_event
from .authority import authorize_office_in_cursor
from .capabilities import OfficeOperation
from .invitation_config import InvitationConfig


class AccountInvitationError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409):
        self.code, self.status_code = code, status_code
        super().__init__(code)


@dataclass(frozen=True)
class IssuedAccountInvitation:
    invitation_id: str
    expires_at: dt.datetime
    version: int = 1
    replayed: bool = False


@dataclass(frozen=True)
class ClaimedAccountInvitationDelivery:
    outbox_id: str
    attempt_id: str
    invitation_id: str
    recipient: str
    bearer_token: str
    expires_at: str


def _email(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not value.isascii():
        raise AccountInvitationError("office_account_invitation_email_invalid", status_code=422)
    value = value.lower()
    if not direccion_unica_valida(value):
        raise AccountInvitationError("office_account_invitation_email_invalid", status_code=422)
    return value


def _token_hash(token: str) -> bytes:
    if not isinstance(token, str) or not token.isascii() or not 43 <= len(token) <= 256:
        raise AccountInvitationError("office_invitation_invalid", status_code=403)
    return hashlib.sha256(token.encode("ascii")).digest()


def _digest(*parts: str) -> bytes:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).digest()


async def _now(cur) -> dt.datetime:
    await cur.execute("SELECT UTC_TIMESTAMP(6)")
    value = (await cur.fetchone())[0]
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


async def _lock_actor_tenant(cur, actor_user_id: int) -> None:
    await cur.execute("SELECT tenant_id FROM jax_users WHERE user_id=%s", (actor_user_id,))
    row = await cur.fetchone()
    if row is not None:
        await cur.execute("SELECT tenant_id FROM jax_tenants WHERE tenant_id=%s FOR UPDATE", (int(row[0]),))
        await cur.fetchone()


def _payload(*, invitation_id: str, email: str, token: str, expires_at: dt.datetime) -> bytes:
    if not clave_de_cifrado_utilizable():
        raise AccountInvitationError("office_account_invitation_transport_unavailable", status_code=503)
    plain = json.dumps({"domain": "office-account-invitation-outbox/v1", "invitation_id": invitation_id,
                        "email": email, "token": token, "expires_at": expires_at.isoformat()},
                       separators=(",", ":"), sort_keys=True)
    try:
        return encrypt_secret(plain).encode("ascii")
    except (RuntimeError, ValueError, UnicodeError) as error:
        raise AccountInvitationError("office_account_invitation_transport_unavailable", status_code=503) from error


def _decode_payload(ciphertext: bytes | str) -> tuple[str, str, str]:
    try:
        raw = ciphertext.decode("ascii") if isinstance(ciphertext, bytes) else ciphertext
        payload = json.loads(decrypt_db_secret(raw))
        if (payload.get("domain") != "office-account-invitation-outbox/v1"
                or not isinstance(payload.get("email"), str)
                or not isinstance(payload.get("token"), str)
                or not isinstance(payload.get("expires_at"), str)):
            raise ValueError("invalid account invitation payload")
        return _email(payload["email"]), payload["token"], payload["expires_at"]
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise AccountInvitationError("office_account_invitation_transport_unavailable", status_code=503) from error


async def issue_account_invitation_in_cursor(cur, *, actor_user_id: int, email: str,
                                              idempotency_key: str, config: InvitationConfig) -> IssuedAccountInvitation:
    email = _email(email)
    try:
        key = str(uuid.UUID(idempotency_key))
    except (TypeError, ValueError) as error:
        raise AccountInvitationError("office_account_invitation_idempotency_invalid", status_code=422) from error
    if key != idempotency_key.lower():
        raise AccountInvitationError("office_account_invitation_idempotency_invalid", status_code=422)
    await _lock_actor_tenant(cur, actor_user_id)
    decision = await authorize_office_in_cursor(cur, actor_user_id, OfficeOperation.INVITE_OFFICE_PERSON)
    event_id = await record_authorization_event(cur, decision)
    if not decision.allowed:
        raise AccountInvitationError("office_account_invitation_issuer_denied", status_code=403)
    request_digest = _digest("ISSUE", str(actor_user_id), str(decision.tenant_id), email)
    await cur.execute("SELECT request_digest,invitation_id,response_version FROM office_account_invitation_receipt WHERE idempotency_key=%s FOR UPDATE", (key,))
    receipt = await cur.fetchone()
    if receipt is not None:
        if receipt[0] != request_digest:
            raise AccountInvitationError("office_account_invitation_idempotency_conflict", status_code=409)
        await cur.execute("SELECT expires_at FROM office_account_invitation WHERE invitation_id=%s", (receipt[1],))
        row = await cur.fetchone()
        if row is None:
            raise AccountInvitationError("office_account_invitation_integrity", status_code=503)
        return IssuedAccountInvitation(str(receipt[1]), row[0], int(receipt[2]), True)
    now = await _now(cur)
    expires_at = now + dt.timedelta(seconds=config.lifetime_seconds)
    invitation_id, outbox_id, token = str(uuid.uuid4()), str(uuid.uuid4()), secrets.token_urlsafe(32)
    try:
        await cur.execute(
            "INSERT INTO office_account_invitation (invitation_id,token_hash,email_normalized,tenant_id,issued_by_user_id,expires_at,authorization_event_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (invitation_id, _token_hash(token), email, decision.tenant_id, actor_user_id, expires_at, event_id))
        await cur.execute(
            "INSERT INTO office_account_invitation_outbox (outbox_id,invitation_id,payload_ciphertext) VALUES (%s,%s,%s)",
            (outbox_id, invitation_id, _payload(invitation_id=invitation_id, email=email, token=token, expires_at=expires_at)))
        await cur.execute(
            "INSERT INTO office_account_invitation_receipt (idempotency_key,request_digest,actor_user_id,tenant_id,invitation_id,operation,expected_version,response_version) VALUES (%s,%s,%s,%s,%s,'ISSUE',0,1)",
            (key, request_digest, actor_user_id, decision.tenant_id, invitation_id))
    except (IntegrityError, OperationalError) as error:
        if getattr(error, "args", ()) and error.args[0] == 1062:
            raise AccountInvitationError("office_account_invitation_pending", status_code=409) from error
        raise
    return IssuedAccountInvitation(invitation_id, expires_at)


async def account_invitation_bearer_preflight(bearer_token: str) -> bool:
    from db.connection import get_pool
    token_hash = _token_hash(bearer_token)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM office_account_invitation WHERE token_hash=%s AND status='ISSUED' AND expires_at>UTC_TIMESTAMP(6) LIMIT 1", (token_hash,))
            return await cur.fetchone() is not None


async def accept_account_invitation_in_cursor(cur, *, accepting_user_id: int | None,
                                               accepting_email: str | None, new_password_hash: str | None,
                                               bearer_token: str) -> bool | None:
    token_hash = _token_hash(bearer_token)
    await cur.execute("SELECT issued_by_user_id,tenant_id FROM office_account_invitation WHERE token_hash=%s", (token_hash,))
    hint = await cur.fetchone()
    if hint is None:
        raise AccountInvitationError("office_invitation_invalid", status_code=403)
    await _lock_actor_tenant(cur, int(hint[0]))
    await cur.execute("SELECT invitation_id,status,version,email_normalized,tenant_id,issued_by_user_id,expires_at,accepted_user_id FROM office_account_invitation WHERE token_hash=%s FOR UPDATE", (token_hash,))
    row = await cur.fetchone()
    if row is None:
        raise AccountInvitationError("office_invitation_invalid", status_code=403)
    invitation_id, status, version, email, tenant_id, issuer, expires_at, accepted = row
    if status == "ACCEPTED":
        if accepting_user_id is not None and int(accepted) == accepting_user_id:
            return True
        raise AccountInvitationError("office_invitation_existing_session_required", status_code=409)
    if status != "ISSUED":
        raise AccountInvitationError("office_invitation_invalid", status_code=403)
    if await _now(cur) >= (expires_at.replace(tzinfo=dt.timezone.utc) if expires_at.tzinfo is None else expires_at):
        await cur.execute("UPDATE office_account_invitation SET status='EXPIRED',version=version+1 WHERE invitation_id=%s AND status='ISSUED' AND version=%s", (invitation_id, version))
        await cur.execute("UPDATE office_account_invitation_outbox SET state='CANCELLED',attempt_id=COALESCE(attempt_id,%s),attempt_count=1,payload_ciphertext=NULL,finished_at=UTC_TIMESTAMP(6) WHERE invitation_id=%s AND state='QUEUED'", (str(uuid.uuid4()), invitation_id))
        return None
    decision = await authorize_office_in_cursor(cur, int(issuer), OfficeOperation.INVITE_OFFICE_PERSON)
    await record_authorization_event(cur, decision)
    if not decision.allowed or int(decision.tenant_id) != int(tenant_id):
        raise AccountInvitationError("office_invitation_issuer_denied", status_code=403)
    if accepting_user_id is None:
        if new_password_hash is None:
            raise AccountInvitationError("office_invitation_password_required", status_code=422)
        await cur.execute("SELECT user_id FROM jax_users WHERE BINARY email=BINARY %s FOR UPDATE", (email,))
        if await cur.fetchone() is not None:
            raise AccountInvitationError("office_invitation_existing_session_required", status_code=409)
        try:
            await cur.execute("INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class) VALUES (%s,%s,%s,'operator','active','OFFICE_INTERNAL')", (tenant_id, email, new_password_hash))
        except IntegrityError as error:
            raise AccountInvitationError("office_invitation_existing_session_required", status_code=409) from error
        accepting_user_id = int(cur.lastrowid)
        import user_audit
        await user_audit.registrar(cur, accepting_user_id, accepting_user_id, "create", {"email": email, "role": "operator"})
        await user_audit.registrar(cur, accepting_user_id, accepting_user_id, "password_reset_completed", None)
        credential_audit_id = int(cur.lastrowid)
        await cur.execute("INSERT INTO office_account_profile (user_id,global_role,status,version,bound_by_user_id,credential_audit_id,created_at,updated_at) VALUES (%s,'COLLABORATOR','ACTIVE',1,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))", (accepting_user_id, accepting_user_id, credential_audit_id))
    else:
        if new_password_hash is not None or _email(accepting_email or "") != email:
            raise AccountInvitationError("office_invitation_invalid", status_code=403)
        await cur.execute("SELECT tenant_id,account_class,status FROM jax_users WHERE user_id=%s FOR UPDATE", (accepting_user_id,))
        account = await cur.fetchone()
        if (account is None or int(account[0]) != int(tenant_id)
                or account[1] not in {"INTERNAL", "OFFICE_INTERNAL"} or account[2] != "active"):
            raise AccountInvitationError("office_invitation_invalid", status_code=403)
        from .bootstrap import credential_self_control_in_cursor
        credential_audit_id = await credential_self_control_in_cursor(cur, user_id=accepting_user_id)
        if credential_audit_id is None:
            raise AccountInvitationError("office_invitation_invalid", status_code=403)
        await cur.execute("SELECT status,bound_by_user_id,credential_audit_id FROM office_account_profile WHERE user_id=%s FOR UPDATE", (accepting_user_id,))
        profile = await cur.fetchone()
        if profile is None:
            await cur.execute("INSERT INTO office_account_profile (user_id,global_role,status,version,bound_by_user_id,credential_audit_id,created_at,updated_at) VALUES (%s,'COLLABORATOR','ACTIVE',1,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))", (accepting_user_id, accepting_user_id, credential_audit_id))
        elif profile[0] != "ACTIVE":
            raise AccountInvitationError("office_invitation_invalid", status_code=403)
        elif profile[1] is None and profile[2] is None:
            await cur.execute("UPDATE office_account_profile SET bound_by_user_id=%s,credential_audit_id=%s,version=version+1 WHERE user_id=%s", (accepting_user_id, credential_audit_id, accepting_user_id))
    await cur.execute("UPDATE office_account_invitation_outbox SET state='CANCELLED',attempt_id=COALESCE(attempt_id,%s),attempt_count=1,payload_ciphertext=NULL,finished_at=UTC_TIMESTAMP(6) WHERE invitation_id=%s AND state='QUEUED'", (str(uuid.uuid4()), invitation_id))
    await cur.execute("UPDATE office_account_invitation SET status='ACCEPTED',version=version+1,accepted_user_id=%s,accepted_at=UTC_TIMESTAMP(6) WHERE invitation_id=%s AND status='ISSUED' AND version=%s", (accepting_user_id, invitation_id, version))
    if cur.rowcount != 1:
        raise AccountInvitationError("office_invitation_retryable", status_code=503)
    return False


async def revoke_account_invitation_in_cursor(cur, *, actor_user_id: int, invitation_id: str,
                                               expected_version: int, idempotency_key: str) -> tuple[int, bool]:
    try:
        invitation_id, key = str(uuid.UUID(invitation_id)), str(uuid.UUID(idempotency_key))
    except (TypeError, ValueError) as error:
        raise AccountInvitationError("office_account_invitation_invalid", status_code=422) from error
    await _lock_actor_tenant(cur, actor_user_id)
    decision = await authorize_office_in_cursor(cur, actor_user_id, OfficeOperation.INVITE_OFFICE_PERSON)
    _event_id = await record_authorization_event(cur, decision)
    if not decision.allowed:
        raise AccountInvitationError("office_account_invitation_issuer_denied", status_code=403)
    await cur.execute("SELECT tenant_id,status,version FROM office_account_invitation WHERE invitation_id=%s FOR UPDATE", (invitation_id,))
    row = await cur.fetchone()
    if row is None or int(row[0]) != int(decision.tenant_id):
        raise AccountInvitationError("office_account_invitation_invalid", status_code=404)
    digest = _digest("REVOKE", str(actor_user_id), invitation_id, str(expected_version))
    await cur.execute("SELECT request_digest,response_version FROM office_account_invitation_receipt WHERE idempotency_key=%s FOR UPDATE", (key,))
    receipt = await cur.fetchone()
    if receipt is not None:
        if receipt[0] != digest:
            raise AccountInvitationError("office_account_invitation_idempotency_conflict", status_code=409)
        return int(receipt[1]), True
    if row[1] == "REVOKED" and int(row[2]) == expected_version + 1:
        raise AccountInvitationError("office_account_invitation_idempotency_required", status_code=409)
    if row[1] != "ISSUED" or int(row[2]) != expected_version:
        raise AccountInvitationError("office_account_invitation_version_conflict", status_code=409)
    await cur.execute("UPDATE office_account_invitation SET status='REVOKED',version=version+1,revoked_by_user_id=%s,revoked_at=UTC_TIMESTAMP(6) WHERE invitation_id=%s AND version=%s", (actor_user_id, invitation_id, expected_version))
    await cur.execute("UPDATE office_account_invitation_outbox SET state='CANCELLED',attempt_id=COALESCE(attempt_id,%s),attempt_count=1,payload_ciphertext=NULL,finished_at=UTC_TIMESTAMP(6) WHERE invitation_id=%s AND state='QUEUED'", (str(uuid.uuid4()), invitation_id))
    await cur.execute("INSERT INTO office_account_invitation_receipt (idempotency_key,request_digest,actor_user_id,tenant_id,invitation_id,operation,expected_version,response_version) VALUES (%s,%s,%s,%s,%s,'REVOKE',%s,%s)", (key, digest, actor_user_id, decision.tenant_id, invitation_id, expected_version, expected_version + 1))
    return expected_version + 1, False


async def list_account_invitations_in_cursor(cur, *, actor_user_id: int, limit: int,
                                             after_id: str | None = None) -> tuple[list[dict], str | None]:
    """List the tenant's human-account lifecycle, never project members."""
    await _lock_actor_tenant(cur, actor_user_id)
    decision = await authorize_office_in_cursor(cur, actor_user_id, OfficeOperation.INVITE_OFFICE_PERSON)
    await record_authorization_event(cur, decision)
    if not decision.allowed:
        raise AccountInvitationError("office_account_invitation_issuer_denied", status_code=403)
    cursor_date = None
    if after_id is not None:
        try:
            after_id = str(uuid.UUID(after_id))
        except ValueError as error:
            raise AccountInvitationError("office_account_invitation_cursor_invalid", status_code=422) from error
        await cur.execute(
            "SELECT created_at FROM office_account_invitation WHERE tenant_id=%s AND invitation_id=%s",
            (decision.tenant_id, after_id),
        )
        marker = await cur.fetchone()
        if marker is None:
            raise AccountInvitationError("office_account_invitation_cursor_invalid", status_code=422)
        cursor_date = marker[0]
    sql = (
        "SELECT i.invitation_id,i.email_normalized,i.status,i.version,i.expires_at,o.state,o.result_code "
        "FROM office_account_invitation i FORCE INDEX (idx_office_account_invitation_history) "
        "JOIN office_account_invitation_outbox o ON o.invitation_id=i.invitation_id "
        "WHERE i.tenant_id=%s"
    )
    params: list[object] = [decision.tenant_id]
    if cursor_date is not None:
        sql += " AND (i.created_at,i.invitation_id)<(%s,%s)"
        params.extend((cursor_date, after_id))
    sql += " ORDER BY i.created_at DESC,i.invitation_id DESC LIMIT %s"
    params.append(limit + 1)
    await cur.execute(sql, tuple(params))
    rows = await cur.fetchall()
    page = rows[:limit]
    output = [{"invitation_id": str(row[0]), "email": row[1], "status": row[2], "version": int(row[3]),
             "expires_at": row[4], "delivery_state": row[5], "delivery_result_code": row[6]}
            for row in page]
    return output, output[-1]["invitation_id"] if len(rows) > limit else None


async def _routine(cur, call: str, variables: tuple[str, ...]):
    await cur.execute(call)
    while await cur.nextset():
        pass
    await cur.execute("SELECT " + ",".join(variables))
    return await cur.fetchone()


async def claim_next_account_invitation_delivery_v1_in_cursor(cur) -> tuple[str, ClaimedAccountInvitationDelivery | None]:
    row = await _routine(cur, "CALL office_account_invitation_claim_next_v1(@d,@o,@a,@i,@p)",
                         ("@d", "@o", "@a", "@i", "@p"))
    if row is None:
        raise AccountInvitationError("office_account_invitation_integrity", status_code=503)
    disposition, outbox_id, attempt_id, invitation_id, ciphertext = row
    if disposition in {"EMPTY", "RETRY", "DUE"}:
        if any(value is not None for value in (outbox_id, attempt_id, invitation_id, ciphertext)):
            raise AccountInvitationError("office_account_invitation_integrity", status_code=503)
        return str(disposition), None
    if disposition != "CLAIMED" or not all((outbox_id, attempt_id, invitation_id, ciphertext is not None)):
        raise AccountInvitationError("office_account_invitation_integrity", status_code=503)
    recipient, token, expires_at = _decode_payload(ciphertext)
    return "CLAIMED", ClaimedAccountInvitationDelivery(str(outbox_id), str(attempt_id), str(invitation_id), recipient, token, expires_at)


async def expire_due_account_invitation_deliveries_v1_in_cursor(cur, *, limit: int) -> int:
    await cur.execute("CALL office_account_invitation_expire_due_v1(%s,@expired)", (limit,))
    while await cur.nextset():
        pass
    await cur.execute("SELECT @expired")
    row = await cur.fetchone()
    if row is None or not isinstance(row[0], int) or row[0] < 0:
        raise AccountInvitationError("office_account_invitation_integrity", status_code=503)
    return int(row[0])


async def settle_account_invitation_delivery_v1_in_cursor(cur, *, outbox_id: str, attempt_id: str,
                                                           relay_accepted: bool, message_id: str | None = None,
                                                           result_code: str | None = None) -> str:
    result = "SMTP_ACCEPTED" if relay_accepted else (result_code or "SMTP_UNKNOWN")
    await cur.execute("CALL office_account_invitation_settle_delivery_v1(%s,%s,%s,%s,@d)",
                      (outbox_id, attempt_id, result, message_id if relay_accepted else None))
    while await cur.nextset():
        pass
    await cur.execute("SELECT @d")
    row = await cur.fetchone()
    if row is None or row[0] not in {"APPLIED", "REPLAYED", "FENCED"}:
        raise AccountInvitationError("office_account_invitation_integrity", status_code=503)
    return str(row[0])


async def settle_stale_account_invitation_claims_v1_in_cursor(cur, *, config: InvitationConfig) -> int:
    await cur.execute("CALL office_account_invitation_settle_stale_v1(%s,%s,@settled)",
                      (config.claim_timeout_seconds, config.stale_claim_batch_limit))
    while await cur.nextset():
        pass
    await cur.execute("SELECT @settled")
    row = await cur.fetchone()
    if row is None or not isinstance(row[0], int) or row[0] < 0:
        raise AccountInvitationError("office_account_invitation_integrity", status_code=503)
    return int(row[0])


async def deliver_one_account_invitation_worker(*, config: InvitationConfig, smtp_settings) -> bool:
    """Claim one human-account mail through its separate, fenced outbox."""
    import asyncio
    import smtp_config
    from .invitations import _delivery_authority_cursor, _invitation_acceptance_url
    from .invitation_email_i18n import render_invitation_email

    for _ in range(2):
        async with _delivery_authority_cursor() as cur:
            disposition, delivery = await claim_next_account_invitation_delivery_v1_in_cursor(cur)
            if disposition == "DUE":
                await expire_due_account_invitation_deliveries_v1_in_cursor(cur, limit=1)
                continue
        if disposition in {"EMPTY", "RETRY"}:
            return False
        if delivery is None:
            raise AccountInvitationError("office_account_invitation_integrity", status_code=503)
        try:
            url = _invitation_acceptance_url(config, delivery.bearer_token)
            subject, text, body_html = render_invitation_email(recipient_locale=None, url=url, expires_at=delivery.expires_at)
            message = smtp_config.construir_mensaje(smtp_settings, delivery.recipient, subject, text, body_html)
            message_id = str(message["Message-ID"])
            await asyncio.to_thread(smtp_config.enviar, smtp_settings, message)
        except BaseException:
            async with _delivery_authority_cursor() as cur:
                await settle_account_invitation_delivery_v1_in_cursor(cur, outbox_id=delivery.outbox_id,
                    attempt_id=delivery.attempt_id, relay_accepted=False, result_code="SMTP_UNKNOWN")
            raise
        async with _delivery_authority_cursor() as cur:
            settled = await settle_account_invitation_delivery_v1_in_cursor(cur, outbox_id=delivery.outbox_id,
                attempt_id=delivery.attempt_id, relay_accepted=True, message_id=message_id)
        if settled not in {"APPLIED", "REPLAYED"}:
            raise AccountInvitationError("office_account_invitation_delivery_fenced", status_code=503)
        return True
    return False


async def drain_account_invitation_deliveries(*, config: InvitationConfig, smtp_settings) -> int:
    from .invitations import _delivery_authority_cursor
    async with _delivery_authority_cursor() as cur:
        await expire_due_account_invitation_deliveries_v1_in_cursor(cur, limit=config.expiry_sweep_batch_limit)
        stale = await settle_stale_account_invitation_claims_v1_in_cursor(cur, config=config)
    if stale:
        # Do not send a later bearer in an invocation that has just marked a
        # prior SMTP outcome UNKNOWN; the operator must see that ambiguity.
        raise AccountInvitationError("office_account_invitation_stale_claims_settled", status_code=503)
    delivered = 0
    for _ in range(config.delivery_batch_limit):
        if not await deliver_one_account_invitation_worker(config=config, smtp_settings=smtp_settings):
            break
        delivered += 1
    return delivered
