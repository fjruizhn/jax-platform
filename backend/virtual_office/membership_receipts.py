"""Office receipt helpers for the non-interactive invitation capability."""
from __future__ import annotations

import hashlib
import json


def invitation_acceptance_digest(*, invitation_id: str, issuer_user_id: int,
                                 target_tenant_id: int, project_id: int,
                                 target_user_id: int, grant_role: str,
                                 audience: str) -> bytes:
    """Bind the accepted capability to exactly one Office companion mutation."""
    payload = {
        "audience": audience,
        "domain": "office-project-membership/invitation-acceptance/v1",
        "grant_role": grant_role,
        "invitation_id": invitation_id,
        "issuer_user_id": issuer_user_id,
        "project_id": project_id,
        "target_tenant_id": target_tenant_id,
        "target_user_id": target_user_id,
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")).digest()


async def write_invitation_acceptance_receipt(
    cur, *, idempotency_key: str, digest: bytes, invitation_id: str,
    issuer_user_id: int, target_tenant_id: int, project_id: int,
    target_user_id: int, grant_role: str, audience: str, result,
    office_grant_version: int, authorization_event_id: int,
    external_relation: tuple[int, int, int] | None,
) -> None:
    """Append the terminal JAX/Office pair after every companion row exists."""
    relation_id = relation_version = recipient_group_id = None
    if external_relation is not None:
        relation_id, relation_version, recipient_group_id = external_relation
    await cur.execute(
        "INSERT INTO office_membership_mutation_receipt "
        "(namespace,idempotency_key,request_digest,operation,actor_user_id,target_tenant_id,project_id,target_user_id,expected_version,grant_role,audience,execution_kind,jax_membership_id,jax_event_id,office_grant_version,office_grant_status,external_relation_id,external_relation_version,recipient_group_id,authorization_event_id,created_at) "
        "VALUES ('OFFICE_CONFIDENTIAL',%s,%s,'GRANT_OFFICE_MEMBER',%s,%s,%s,%s,0,%s,%s,'INVITATION_ACCEPTANCE',%s,%s,%s,'ACTIVE',%s,%s,%s,%s,UTC_TIMESTAMP(6))",
        (idempotency_key, digest, issuer_user_id, target_tenant_id, project_id,
         target_user_id, grant_role, audience, result.membership_id,
         result.event_id, office_grant_version, relation_id, relation_version,
         recipient_group_id, authorization_event_id),
    )
