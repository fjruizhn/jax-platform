# Task3B Fix 2 · recovery-PENDING lineage and restricted reenrollment route

2026-10-22 · Tier2 implementation evidence for the Sol Fix 1 review at
`2026-10-22-office-3b-audit-fix1.md`. This is a freeze request for a new
exact-SHA review, not a review verdict or authorization for Task 3C.

## BLOCK closed: recovery origin survives a token-only session cut

`virtual_office.mfa.pending_enrollment_is_recovery_origin_in_cursor` locks the
credential-owned `pending_enrollment_id` anchor and reads recovery lineage
from its immutable historical token/MFA versions. It intentionally does not
use the current token version. A missing, malformed, foreign, or current-MFA
inconsistent anchor returns the fail-closed recovery classification, so login
cannot fall back to Classic issuance.

`test_recovery_origin_pending_password_login_never_mints_session` is a real
HTTP/DB witness:

1. first enrollment is confirmed, a recovery code is consumed, and recovery
   enrollment produces `PENDING` through canonical primitives;
2. the existing canonical Classic `POST /api/admin/users/{id}/revoke-sessions`
   route executes a token-only cut;
3. password login is `403` with neither access material nor refresh cookie;
4. after the pending anchor is expired by a controlled durable-fixture update,
   the same HTTP login is still `403` with no tokens.

`test_first_enrollment_pending_keeps_classic_password_login_compatibility`
and `test_disabled_factor_keeps_classic_password_login_compatibility` prove
the required distinct first-PENDING and DISABLED legacy cases still receive a
normal Classic session.

## MAJOR closed: exact restricted route only

`auth.office_barrier._AUTH_ALLOWLIST` now contains only
`("POST", "/api/auth/mfa/reenrollment/start")`; it does not introduce a prefix
or wildcard allowance. The parametrized real HTTP test
`test_restricted_account_reaches_safe_reenrollment_after_actual_step_up` covers
both `OFFICE_INTERNAL` and `EXTERNAL_CLIENT`: it completes actual STEP_UP,
reaches the reenrollment handler with its resulting durable proof, and obtains
the no-store pending replacement while the old factor remains ACTIVE.

The existing classic-handler boundary test is parametrized for both restricted
classes and confirms they are denied before the Classic admin handler runs.

## Validation

Focused regression command:

```text
/usr/bin/python3 /home/fruiz/.local/share/codex-workspaces/office-20261020/cv-office-run-tests.py jxp -q --disable-warnings tests/test_office_mfa_login_api.py tests/test_office_session_barrier.py
GREEN: 46 passed, 686 warnings
```

Full selected Task3B command:

```text
/usr/bin/python3 /home/fruiz/.local/share/codex-workspaces/office-20261020/cv-office-run-tests.py jxp -q --disable-warnings tests/test_office_authority.py tests/test_office_identity_guard.py tests/test_office_mfa.py tests/test_office_mfa_api_security.py tests/test_office_mfa_events.py tests/test_office_mfa_login_api.py tests/test_office_migrations.py tests/test_login_bloqueo.py tests/test_login_rate_limit.py tests/test_login_residuos.py tests/test_login_sin_enumeracion.py tests/test_sesiones_token_version.py tests/test_fijar_password.py tests/test_sesion_unica.py tests/test_office_session_barrier.py
GREEN: 313 passed, 3244 warnings in 20.04s
```

`git diff --check` is clean. No commit, push, PR, provider, mail, or production
action occurred.
