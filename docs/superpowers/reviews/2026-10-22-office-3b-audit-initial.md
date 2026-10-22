# Task3B · initial independent audit

2026-10-22 · Codex records read-only Sol `mfa_cursor_service_audit` verdict on exact SHA f5972e7cdd1c947a751de7a9f49dbae1262937d7, base approved3A2 4223b7dac33021a9bcdc71b09eb6d604242e4cfc. Spec RECHAZADO; quality RECHAZADO. Reviewer accepted root302-pass evidence but independently reproduced password validation reflection and inspected source, without DB writes.

1. BLOCK recovery password-login bypass: api/auth.py only challenges ACTIVE; RECOVERY_REQUIRED/recovery-origin PENDING fall through to Classic access+refresh. Recovery capability is enrollment-only. Required real HTTP denial in both states, no cookie/session.
2. BLOCK live lock ignored: mfa.py verifier does not read locked_until under user lock and LOGIN clears it. Recheck live lock, retire challenge, no proof/version bump/session. Required challenge-issued then lock-imposed regression.
3. MAJOR absolute STEP_UP session policy: checks refresh exp but not current vida_de_sesion_segundos/sesion_vencida; shortening policy can be bypassed until old JWT exp. Enforce current policy while preserving original iat/exp; prove future-exp old-iat denied.
4. MAJOR active reenrollment has no HTTP path: safe primitive exists but first-enrollment route rejects active factor. Add authenticated self-service current password+recent durable proof path; old factor remains active until atomic confirmation.
5. MINOR auth secrecy/no-store covers MFA namespace only: malformed login reflects submitted password and Classic login/refresh lack explicit no-store. Extend sanitized validation and no-store to auth success/errors without changing Classic no-factor behavior.

No further concrete defect found in central authority, root-reset proof/ALLOWED linkage, identity mutex or Classic guard. Fix round1/5 uses same Task3B scope, not new task. No Task3C consumer until exact-SHA clean spec+quality re-review.
