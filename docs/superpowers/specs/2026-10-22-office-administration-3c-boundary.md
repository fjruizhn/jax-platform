# Task3C · implementation boundary and ordering

2026-10-22 · OP343/345 · Codex technical ruling following read-only Sol office_3c_boundary. Existing product decisions and administration/MFA/invitation/bootstrap specifications remain binding; no conceptual question for Fernando remains. This document describes development interfaces, not deployed capability. Start only after Task3B exact-SHA spec+quality approval.

## Delivery slices

3C1 implements durable schema and explicit self-bootstrap plus interactive administrative projects/people/delegations/grants. 3C2 implements immutable invitation issuance, atomic acceptance and durable SMTP outbox. These are bounded slices of existing Task3C, not changes to the closed Task3B/4C fix caps. One JXP writer at a time; each slice has its independent spec/quality gate before the next consumes it. No external account creation, actual bootstrap or real email during tests.

The split follows two distinct proof contracts: interactive mutations use current recent session MFA; delayed invitation acceptance uses verified issuance history plus current inviter authority. It avoids implementing one as a bypass flag on the other. Frontend can consume only approved backend interfaces.

## Shared transaction order

Discover immutable identity/tenant hints without locking, then lock affected tenants ascending, actor user/profile, target user/profile, project scope and current basis, invitation/issuance, JAX idempotency/membership, Office grant/event/outbox. Recheck discovery under locks, fail closed on drift. Every profile writer first locks its jax_users owner. Reuse the existing pair-lock helper where applicable; no global mutex table. SMTP runs without DB locks via asyncio.to_thread and the existing mail transport.

## Bootstrap and identity control

Follow 2026-10-21-office-bootstrap-self-identity.md exactly. Share a narrowly scoped credential-control helper with later existing-user invitation acceptance: actual self password_reset_completed audit after latest create/update_email/password_set_by_admin, preserving signed BIGINT user_admin_audit.id. Do not fabricate such an event or infer control from a classical role/email.

New invitation identity is created with a subject-chosen password validated by canonical password rules; hash outside the transaction. Existing compatible identity needs its own current authenticated session and valid own-credential evidence; invitation never resets/replaces an existing password. If the email becomes an existing identity during a race, do not use the new-user password to overwrite it. Acceptance creates no access/refresh session.

Canonical user/tenant/project IDs are signed INT; MFA/authorization/group IDs are BIGINT UNSIGNED; credential audit ID is signed BIGINT. Invitation/attempt/idempotency IDs use canonical UUID CHAR36; hashes use BINARY32; security dates are DATETIME6 explicitly UTC_TIMESTAMP6.

## Current and delayed authority

Project authorizer receives server-derived current proof event/config/correlation; membership authorizer receives current interactive context or derives the invitation capability entirely from the typed JAX execution invitation_id and durable DB rows. Missing context denies. Never accept a proof/actor/tenant/role authority boolean from a request.

Interactive routes use the existing central resolver with current recent durable MFA. Office root may choose destination tenant only within Office; general admin stays in its canonical tenant and needs delegation; project admin stays in its own project and cannot promote admins/global roles/professional authority. New projects remain OFFICE_CONFIDENTIAL, one CLIENT group, never classic. No global synthetic tenant, account-class conversion, singleton root or classical privilege expansion.

3C2 delayed proof verifier lives in invitations.py and validates the immutable emission event/proof binding, subject/purpose/result, recent-at-issuance time, and current issuer mfa_version. It does NOT require original token_version or that mfa_at remain recent at acceptance: ordinary login must not cancel a valid invitation. Reuse the private current-grant phase of authority.py only after validating issuance proof; no new public MFA-skip boolean or freely constructible authority object. Reauthorize ADMINISTER_PROJECT_MEMBERS with current issuer/profile/grant/membership in the same transaction. Store actor as real issued_by and execution as office-invitation:{UUID}; JAX typed INVITATION_ACCEPTANCE already supports this, no new core protocol.

## Persistence and outbox

Add bootstrap state append-only, invitation state, immutable issuance, append-only invitation events and mutable outbox with real constraints/FKs/indexes/readiness. No FK from JAX core to JXP tables. Extend existing migration/runtime verification rather than a second runner. Prove resumable upgrade and exact constraint/index/FK metadata plus append-only behavior under DML runtime principal; no runtime DDL.

Only token SHA256 is kept as invitation identity; encrypted transient outbox payload may contain the raw link until terminal/cancelled/expired, then erase ciphertext. Queued/claimed/accepted/unknown states describe relay transport, not inbox delivery. Claim atomically with attempt_id fencing, revalidate before sending, lost result remains UNKNOWN, stale CLAIMED becomes UNKNOWN, never auto-requeue an ambiguous send. Acceptance of a valid bearer is allowed even while SMTP UNKNOWN; preserve original transport truth and record observed state. Acceptance during QUEUED cancels payload; CLAIMED marks cancel_requested without reviving the invitation. Use the existing secret encryption/mail configuration, never another secret store.

## Required verification

Real MariaDB isolated only: bootstrap own-proof/admin-control/replay/race/rollback/no-singleton; root/general/project scope and no escalation; paired namespace/atomic membership+grant; delayed proof after login succeeds but MFA change/revocation fails; duplicate acceptance one identity/membership/grant; target class/tenant mismatch denied; existing password never changed; SMTP before/after-claim ambiguity and no retry; payload erasure/fencing; both audiences and all canonical fences. Exact real-query EXPLAIN with representative skew, no new cache, async blocking scan. Full load plus backup/restore/public browser checks remain Task6 production gates, not claims from unit tests.
