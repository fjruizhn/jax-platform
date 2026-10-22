# Task3B fix1 · independent Sol review

2026-10-22 · Codex records `mfa_cursor_service_audit` read-only verdict on exact SHA124439ac126e5184d1a4d9c93bd53d4d2d46ce4c: spec RECHAZADO, quality RECHAZADO. Root independently308passed3181warnings19.20s; reviewer accepted that evidence, no DB writes.

## BLOCK: recovery origin cannot depend on current token version

Login api/auth.py190-205 classifies PENDING with current token_version/mfa_version. Recovery enrollment origin belongs to immutable pending challenge versions. After allowed token-only revoke-sessions, current T increments while origin remains at T1; lookup falsely reports no recovery and Classic login issues session. Existing immediate PENDING HTTP test misses this. Binding already established in3A: derive origin from locked pending_enrollment_id anchor and its expected historical T/M, not current user T. Expired/invalidated pending capability must not erase recovery origin. Required real flow recovery→pending→token-only cut→passwordlogin denied/no access/cookie; preserve ordinary first-enrollment and deliberately disabled compatibility.

## MAJOR: missing restricted-account reenrollment allowlist

Exact new `/api/auth/mfa/reenrollment/start` is absent from auth/office_barrier.py finite allowlist. INTERNAL test passes but OFFICE_INTERNAL/EXTERNAL_CLIENT receive office_surface_required before handler. Add only exact route, maintain default deny and prove both restricted classes can reach authenticated safe reenrollment with actual proof, while Classic surfaces remain blocked.

Live-lock challenge retirement, current absolute STEP_UP policy and auth-wide sanitization/no-store are closed. Active reenrollment is correct for INTERNAL. Fix2/5 same Task3B; no consumer until exact-SHA clean re-review. No new protocol or authority.
