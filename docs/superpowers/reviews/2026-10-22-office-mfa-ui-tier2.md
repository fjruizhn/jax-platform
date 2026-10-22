# Tier 2 evidence: Office MFA UI

Date: 2026-10-22

The frontend implements the approved Task 3B MFA contract without inventing administrative or Office-job APIs. Public MFA capability endpoints are explicitly isolated from the 401 refresh interceptor. The store accepts only a canonical `access_token` response as a session; the password-login MFA response leaves token and user empty.

`RequireAuth` is the central pre-mount barrier for Classic routes. It redirects `OFFICE_INTERNAL` and `EXTERNAL_CLIENT` to `/account/security`; that route is the sole frontend allowlist exception. This prevents dashboard/history/projects/admin effects from requesting Classic data before navigation.

The implementation has focused tests for challenge/session separation, malformed and late OTP flows, recovery enrollment with no session, exact interceptor bypasses, first-hop invalid versus ambiguous recovery outcomes, recovery-start outcome pairs (retryable 503, ambiguous 500/network, terminal 401), session clearing, restricted classes, stale Account Security step-up, post-cut code acknowledgment, post-cut-code removal on restoration, atomic A-to-B account-state clearing (including an object URL), and a late A restore blocked by a newer MFA B session. A real Zustand/router integration test proves Classic and Office navigation after the login-owned epoch bump and blocks a stale A response after B establishes a newer session. The final full frontend suite passed with 1448 tests in 116 files; the production build also passed.

The implementation is a candidate, not a completed Task 4A: Sol's BLOCK-4 remains open because the backend does not provide an idempotent reconciliation/replay contract after a recovery enrollment request loses its response. The UI preserves a known bearer only for the explicit `503 office_mfa_unavailable` pre-transaction result, blocks automatic replay after all other 5xx/network unknown results, clears terminal recovery-capability rejections, and asks in its own dialog before discarding it. Root will schedule the backend contract and re-audit this UI against it.

The exact owned source files are listed in the Task 4A report. No backend, package metadata, lockfile, production, provider, real account, secret, commit, or push was modified.
