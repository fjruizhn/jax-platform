# Task3B fix2 · independent Sol verdict

2026-10-22 · Codex records mfa_cursor_service_audit read-only review of exact4d9fe7fb81d89786283ab1cc9d8cdd5332ce9073. Spec RECHAZADO; quality RECHAZADO. Prior historical-origin and restricted-allowlist findings closed; new BLOCK in same login path. Root313passed3244warnings19.94s accepted as evidence; no auditor DB writes.

## BLOCK: factor classification and password-only session use separate transactions

api/auth.py188-223 locks user/credential and classifies absent/first-PENDING/DISABLED as Classic-compatible, then commits. Separate transaction244-259 increments current token_version without binding the prior factor state. Concurrent confirm_enrollment can activate factor and bump T/M between transactions. Login then stacks another T increment over that cut and issues valid access/refresh without MFA although factor is ACTIVE. Existing tests lack this deterministic race.

Fix3/5 ruling: classification and Classic bump/reload must share the same canonical user/credential-locked transaction; tokens/cookie/cuts occur postcommit. Equivalent exact CAS is allowed but the same transaction is simplest. Preserve BCrypt outside locks, current password/status/live-lock checks, Classic no-factor behavior and single-session semantics. Do not reopen a gap by checking again before a second transaction.

Required real HTTP/DB concurrency witness must exercise actual login and enrollment confirmation with controlled barriers/locks, not only invoke primitives: whichever operation linearizes first, no current valid password-only session may survive an ACTIVE factor. If login wins first its issued token must be stale after confirm; if confirmation wins login must challenge. Verify no duplicate bump/proof/cookie on rejection/rollback and existing Classic race regressions remain green. No new schema/protocol/authority, same Task3B cap; no3C before clean exact-SHA review.
