# Tier 2 evidence: Office MFA UI

Date: 2026-10-22

The frontend implements the approved Task 3B MFA contract without inventing administrative or Office-job APIs. Public MFA capability endpoints are explicitly isolated from the 401 refresh interceptor. The store accepts only a canonical `access_token` response as a session; the password-login MFA response leaves token and user empty.

`RequireAuth` is the central pre-mount barrier for Classic routes. It redirects `OFFICE_INTERNAL` and `EXTERNAL_CLIENT` to `/account/security`; that route is the sole frontend allowlist exception. This prevents dashboard/history/projects/admin effects from requesting Classic data before navigation.

The implementation has focused tests for challenge/session separation, malformed and late OTP flows, recovery enrollment with no session, exact interceptor bypasses, session clearing, restricted classes, stale Account Security step-up, post-cut code acknowledgment, and post-cut-code removal on both successful and failed session restoration. A real Zustand/router integration test proves Classic and Office navigation after the login-owned epoch bump and blocks a stale A response after B establishes a newer session. The full frontend suite passed with 1438 tests in 116 files; the production build passed. A real browser/local-API exercise remains root's assigned integration step.

The exact owned source files are listed in the Task 4A report. No backend, package metadata, lockfile, production, provider, real account, secret, commit, or push was modified.
