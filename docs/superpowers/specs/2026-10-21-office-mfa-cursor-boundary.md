# MFA3A2 caller-cursor boundary · OP345

2026-10-21 · DECISIÓN técnica Codex from read-only Sol delivery_path, checked against existing mfa.py/migrations.py and binding MFA Durable/Cohesive sections. No enabled capability. This freezes missing implementation interfaces; it does not redesign identity or supersede authorization.

Remove MFAProof and ALL old transaction-owning public wrappers. No compatibility shim for unguarded enrollment, caller-user recovery, generic challenge-purpose issuance, freely constructible proofs or generic reset. 3A2 exposes caller-cursor domain functions and immutable result receipts/outcomes only. 3B owns transactions/password/session/JWT/HTTP mapping/postcommit connection cuts. No typed authenticated-context object, verified Boolean, new signed-context protocol or callback authority.

## Inputs and interfaces

Names may vary; semantics/authority boundaries may not:

1. `issue_login_challenge_after_password_in_cursor(cur, *, preauthenticated_user_id, config)` → challenge or MFADenied. 3B calls only after canonical password verification. Locked subject/current versions define LOGIN.
2. `issue_step_up_challenge_in_cursor(cur, *, actor_user_id, config)` → challenge/denial. Actor is server-verified self-session only.
3. `verify_and_consume_totp_in_cursor(cur, *, challenge_token, otp, server_correlation_id)` → immutable MFAProofReceipt or MFADenied. NO caller user/purpose/principal/verified inputs. Token lookup derives subject/purpose/versions; only LOGIN/STEP_UP accepted. LOGIN proof is PASSWORD_PREAUTH actorNULL/challengeID. STEP_UP proof USER_SESSION actor=subject/challengeNULL per ledger shape. Verify actual active-secret OTP, unique counter, CAS consume, private proof append in the SAME transaction. Receipt includes eventID/subject/purpose/versions/DBtime, never authority by its object construction.
4. `start_first_enrollment_after_password_in_cursor(cur, *, preauthenticated_user_id, config)` → EnrollmentStart/denial. 3B verified real password against canonical subject in same request/transaction. Creates ENROLLMENT capability without claiming USER_SESSION or emitting a proof: one raw opaque token, SHA256 stored in BOTH pending_enrollment_id and challenge.token_hash; same subject/current versions/purpose/expiry/budget. Returns raw token/new secret once.
5. `start_reenrollment_after_password_in_cursor(cur, *, actor_user_id, proof_event_id, config)` → EnrollmentStart/denial. 3B just verified password; actor/eventID come from verified signed session context, NEVER request body. Reload durable verified current/recent/nonfuture self LOGIN or STEP_UP proof; retain old ACTIVE secret/state while storing pending bundle and fresh ENROLLMENT capability.
6. `consume_recovery_code_in_cursor(cur, *, recovery_code, config, server_correlation_id)` → RecoveryCapability/denial. NO caller userID. Hash lookup derives subject; canonical locks then revalidate/CAS. Increment both versions, clear factor/pending, invalidate other recovery/challenges, create same-subject RECOVERY challenge at new versions, append verified RECOVERY proof and linked RECOVERY_CONSUME state event. Returns only opaque recovery capability, no login/session/stepup proof.
7. `start_recovery_enrollment_in_cursor(cur, *, recovery_capability_token, config)` → EnrollmentStart/denial. Derive subject/purpose/versions from locked RECOVERY row, consume it, create pending secret and ENROLLMENT capability. No actor/user argument.
8. `confirm_enrollment_in_cursor(cur, *, enrollment_capability_token, otp, config, server_correlation_id)` → EnrollmentConfirmation/denial. Derive subject from ENROLLMENT row; require hash equals credential pending id. Verify pending-secret OTP/unique counter, CAS consume, ENROLLMENT proof at old versions; atomically swap pending→ACTIVE/increment both versions/replace recovery codes/invalidate other capabilities/linked state event. Return codes+newversions+stateeventID. Old factor remains active until atomic success.
9. `reload_current_self_mfa_proof_in_cursor(cur, *, actor_user_id, proof_event_id, config)` → DB-reloaded receipt. Accept only VERIFIED/current/recent/nonfuture LOGIN PASSWORD_PREAUTH subject=authenticated actor or STEP_UP USER_SESSION actor=subject. Mutations reload eventID again under locks, never trust a receipt object as authority.
10. `regenerate_recovery_codes_after_password_in_cursor` and `disable_mfa_after_password_in_cursor(cur, *, actor_user_id, proof_event_id, config, server_correlation_id)` → state receipt/denial. 3B verified password in same request/transaction; domain reloads durable self proof and ACTIVE factor, changes both versions/state and appends linked evidence. Result identifies subject/newversions so3B cuts aftercommit.
11. NO root-reset service in3A2;3B adds only with current Office-root authorization+durable real-actor proof+separateALLOWEDauthorizationevent in same transaction. Never choose another user's secret.

`preauthenticated_user_id` and `actor_user_id` are trusted server values resolved by3B canonical password/session code, never request DTO fields. `proof_event_id` comes only from verified signed claims, never a browser selector. Raw capabilities/code may come from body because DB derives their subject/authority. Trusted Python cannot cryptographically prove a previous password check; the binding spec explicitly chooses call graph+same transaction+tests over another permission protocol. Narrow entry-point names make this obligation visible. `server_correlation_id` is server-generated UUID; security time is DB NOW(6), not caller-controlled time.

## Denials, commits and locking

Invalid OTP, replay, expiry/exhaustion or stale versions return MFADenied(code,attempts_remaining,terminal) after persisting attempt/terminal state (and optional truthful DENIED event), rather than raising an HTTP/auth error inside the transaction.3B exits transaction normally, then maps denial outside it. DB/programmer/crypto failure raises and rolls everything back. Password failures are3B/rate-limiter-owned and do not enter primitives.

For token/code lookups: indexed nonlocking lookup learns subject only → lock jax_users → reread capability/code FOR UPDATE and revalidate → credential/budget/event work. All issuance/state functions lock user first. This closes challenge→user/user→challenge inversion. Future root mutations lock actor/target in canonical ID order.

Private proof/state append helpers consume internally derived locked rows. No public actor/subject/result/verified writer. VERIFIED proof is reachable only after actual OTP/code verification and unique/CAS consumption; append/state/version failure rolls the whole transaction back.

## Minimal schema prerequisites

A. ACTIVE credential permits either all pending fieldsNULL or all pending_secret_ciphertext/pending_enrollment_id/pending_expires_at present while retaining secret_ciphertext. PENDING remains first/recovery enrollment with no active secret. Upgrade canonical CHECK explicitly with old-history preflight; don't blindly accept arbitrary old definitions or rewrite evidence.

B. Challenge adds INVALIDATED (not false EXPIRED/CONSUMED history for version invalidation).

C. Indexed budget lookup `(user_id,purpose,expected_token_version,expected_mfa_version,state,expires_at)`. Under locked subject, reject another ISSUED/EXHAUSTED challenge within its policy window, preserving exhaustion through retries; no extra table is required if this existing-row window proves the contract. New token issuance cannot extend/reset that window. Document any genuinely necessary alternative before adding a store.

D. Before shipping the private writer, tighten shape/truth CHECKs and canonical digest tests to actual principal semantics. Proof mapping: LOGIN→PASSWORD_PREAUTH; STEP_UP→USER_SESSION self; ENROLLMENT→ENROLLMENT_CAPABILITY; RECOVERY→RECOVERY_CAPABILITY. State mapping: ENROLLMENT_CONFIRM/ENROLLMENT_CAPABILITY/ENROLLMENT; RECOVERY_CONSUME/RECOVERY_CAPABILITY/RECOVERY; RECOVERY_REGENERATE/USER_SESSION-self/RECOVERY; MFA_DISABLE/USER_SESSION-self/STEP_UP; MFA_RESET/USER_SESSION actor!=subject/STEP_UP with authorization_event required. Non-reset authorization_eventNULL. Add missing RECOVERY_REGENERATE operation and state-purpose STEP_UP. Preserve system fence. Existing dedicated ledger remains append-only with exact metadata/FKs and same runtimeDML tests; readiness advances for required new schema.

No new challenge principal column: frozen purpose→principal is one-to-one and private issuance creates those rows. Fresh/upgrade/idempotency, valid positive controls, malformed SQL and EXPLAIN actual lookup paths remain mandatory. Any impossible old evidence fails migration without fabricating readiness or silently repairing history.

## Cost if wrong

Removing unapproved wrappers costs test/caller updates now, before any route exists. A wrong cursor boundary would require reworking3A2/3B; retaining old permissive wrappers would enable the concrete takeovers already found. The new gates remain disabled until reviewed together with their actual3B call graph.
