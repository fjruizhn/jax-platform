# Task 3A2 service report

Implemented the frozen 3A2 caller-cursor boundary in `backend/virtual_office/mfa.py`.

Public operations are `issue_login_challenge_after_password_in_cursor`,
`issue_step_up_challenge_in_cursor`, `verify_and_consume_totp_in_cursor`,
`start_first_enrollment_after_password_in_cursor`,
`start_reenrollment_after_password_in_cursor`, `consume_recovery_code_in_cursor`,
`start_recovery_enrollment_in_cursor`, `confirm_enrollment_in_cursor`,
`reload_current_self_mfa_proof_in_cursor`,
`regenerate_recovery_codes_after_password_in_cursor`, and
`disable_mfa_after_password_in_cursor`.  Root reset is deliberately unavailable.
All accept the transaction cursor from 3B; no transaction wrappers, password/session
verification, JWT, routes, connection cuts, or authorization-ledger writes remain.

The service derives capability subjects from SHA-256 lookups, locks the user before
rereading capabilities, uses `NOW(6)`, preserves old active secrets during reenrollment,
creates only private durable security events after actual OTP/recovery verification, and
returns immutable receipts or committed `MFADenied` values. DB/crypto failures raise so the
caller transaction rolls back. Challenge issuance consults the frozen indexed policy window
to prevent fresh issuance from clearing a live/exhausted budget.

TDD evidence:

* RED: `cv-office-run-tests.py jxp tests/test_office_mfa.py` failed at collection because
  the new cursor-boundary types/functions did not exist.
* GREEN: the same command passed `11 passed`; its fixture emitted unrelated migration
  duplicate-object warnings while repeatedly applying the shared fixture schema.
* `python3 -m py_compile backend/virtual_office/mfa.py` and `git diff --check` passed.

Focused tests exercise enrollment evidence, committed login and enrollment OTP attempts,
the enrollment-purpose boundary, refusal to replace a live pending enrollment, complete
recovery consume→re-enroll→confirm, durable-proof subject/version rejection, concurrent OTP
and recovery single winners, and durable-proof-gated regeneration/disable state cuts. The parent
should run independent schema/contract review and 3B orchestration tests before enabling a route.

The real MariaDB `EXPLAIN` of the exact budget lookup selected
`idx_office_mfa_challenge_user` with `type=range` and `Using index condition; Using filesort`.
The filesort is caused by `ORDER BY created_at` beyond the frozen index; no schema change was
made in this task, and the result is reported for the serialized schema review.

Parent independent run2026-10-21:11passed371warnings1.31s, disposableMariaDB33991. Not route activation or comprehensive approval.

## Service fix round 1 · 2026-10-21

Service-only correction of `ffb6eb529e069c547bf0702c10bec3d8c3f4858b` following the
binding 3A2 cursor specification and Sol delivery-path ruling. It closes the recovery-origin
escalation, fail-soft crypto, replay-budget, reenrollment, and expiry findings without schema,
route, authorization, or provider changes. Recovery origin uses the existing current-version
append-only `RECOVERY_CONSUME` event; no new proof protocol was introduced.

Focused disposable-MariaDB `33991` validation passed: `21 passed, 603 warnings` for
`tests/test_office_mfa.py`. The two actual budget plans use `idx_office_mfa_challenge_user`
with `Using where; Using index` and `Using index condition`, without filesort or temporary.
Compile and diff-whitespace checks passed. An independent adversarial review remains required
before 3B integration or route enablement.
