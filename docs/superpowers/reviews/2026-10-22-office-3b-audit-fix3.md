# Task3B Fix 3 · atomic password-login factor decision

2026-10-22 · Tier2 implementation evidence responding to the exact-SHA Sol
verdict in `2026-10-22-office-3b-audit-fix2-sol.md`. This is a request for a
new exact-SHA review, not a review verdict or approval for Task 3C.

## BLOCK closed

After bcrypt remains outside locks, `api.auth.login` now takes the canonical
`jax_users` and credential locks once, then revalidates current password,
status, live lock, and account class. Credential classification and the
ordinary Classic `token_version` bump/reload occur inside that same
transaction. An ACTIVE factor still returns only the opaque challenge from
that transaction. Token/cookie emission and connection cutting remain after
commit.

This removes the former commit gap between a no-factor classification and the
Classic bump: confirmation cannot make the factor ACTIVE in that interval.

## Deterministic real HTTP/DB race witnesses

`test_http_login_winning_before_enrollment_confirm_is_stale_after_confirm`
holds the actual login transaction after its user/credential `FOR UPDATE`
classification. An actual enrollment-confirm HTTP request is started and
waits for that lock. The resulting pre-login enrollment capability is rejected
with `enrollment_version`, with no cookie or duplicate bump. After its durable
owner is expired, actual HTTP enrollment start/confirm activates the factor;
the earlier Classic token is then rejected as stale.

`test_http_enrollment_confirm_winning_before_login_forces_mfa_challenge`
holds the actual confirmation transaction immediately after its canonical
user-row `FOR UPDATE`. Concurrent actual HTTP login waits, then sees ACTIVE
and returns an MFA challenge with no password-only token or refresh cookie.

Both tests use barriers only to schedule live transactions and their SQL locks;
they do not replace transactions, the login endpoint, or confirmation
primitives. Existing Classic login/password/version suites are part of the
focused and full validation below.

## Validation

Focused command:

```text
/usr/bin/python3 /home/fruiz/.local/share/codex-workspaces/office-20261020/cv-office-run-tests.py jxp -q --disable-warnings tests/test_office_mfa_login_api.py tests/test_login_bloqueo.py tests/test_sesiones_token_version.py tests/test_fijar_password.py
GREEN: 88 passed, 728 warnings in 7.66s
```

Full selected Task3B command:

```text
/usr/bin/python3 /home/fruiz/.local/share/codex-workspaces/office-20261020/cv-office-run-tests.py jxp -q --disable-warnings tests/test_office_authority.py tests/test_office_identity_guard.py tests/test_office_mfa.py tests/test_office_mfa_api_security.py tests/test_office_mfa_events.py tests/test_office_mfa_login_api.py tests/test_office_migrations.py tests/test_login_bloqueo.py tests/test_login_rate_limit.py tests/test_login_residuos.py tests/test_login_sin_enumeracion.py tests/test_sesiones_token_version.py tests/test_fijar_password.py tests/test_sesion_unica.py tests/test_office_session_barrier.py
GREEN: 315 passed, 3286 warnings in 20.10s
```

`git diff --check` is clean. No commit, push, PR, provider, mail, or production
action occurred.
