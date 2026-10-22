# Tier 2 evidence: Office MFA UI

Date: 2026-10-22

The frontend implements the approved Task 3B MFA contract without inventing administrative or Office-job APIs. Public MFA capability endpoints are explicitly isolated from the 401 refresh interceptor. The store accepts only a canonical `access_token` response as a session; the password-login MFA response leaves token and user empty.

`RequireAuth` is the central pre-mount barrier for Classic routes. It redirects `OFFICE_INTERNAL` and `EXTERNAL_CLIENT` to `/account/security`; that route is the sole frontend allowlist exception. This prevents dashboard/history/projects/admin effects from requesting Classic data before navigation.

The implementation has focused tests for challenge/session separation, malformed and late OTP flows, atomic recovery success with no session, explicit same-code retry after a dropped response, invalid/503/410 recovery outcomes, exact interceptor bypasses, an A-refresh versus B-session success/failure race, terminal and ambiguous enrollment confirmation behavior in public and account-security setup, session clearing, restricted classes, stale Account Security step-up, post-cut code acknowledgment, post-cut-code removal on restoration, atomic A-to-B account-state clearing (including an object URL), and a late A restore blocked by a newer MFA B session. A real Zustand/router integration test proves Classic and Office navigation after the login-owned epoch bump and blocks a stale A response after B establishes a newer session. The final full frontend suite passed with 1451 tests in 116 files; the production build also passed.

The implementation is a candidate, not a completed Task 4A: the frontend is wired to the approved atomic replay contract but requires backend implementation and audit before any live functional claim. It sends only the physical recovery code to `/auth/mfa/recover`, never persists a bearer/token/secret, and leaves retries after an ambiguous response as an explicit user action.

The exact owned source files are listed in the Task 4A report. No backend, package metadata, lockfile, production, provider, real account, secret, commit, or push was modified.
