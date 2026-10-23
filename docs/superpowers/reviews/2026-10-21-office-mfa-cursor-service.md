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

## Service fix round 2 · 2026-10-21

The earlier current-version recovery-lineage lookup was corrected after the verified
token-only session-revocation counterexample. PENDING origin now comes from the exact locked
challenge stored in `pending_enrollment_id`; its historical token/MFA version anchors the
immutable `RECOVERY_CONSUME` lookup. Invalid anchors fail closed. This preserves recovery
origin across session revocation while retaining the normal first-origin expired-window restart.

Validation: focused MFA service suite on disposable MariaDB `33991` → `27 passed, 707 warnings`.
Actual primary-key anchor and forced proof-index lineage EXPLAIN checks passed without
filesort/temporary; compile and diff checks passed. No schema, route, auth, provider, commit,
or production action was performed.

## Service fault matrix completion · 2026-10-21

The service matrix now executes both recovery-enrollment CAS-loss branches after the real
`ENROLLMENT` insert and the respective real UPDATE. Each injected lost rowcount raises and the
transaction rolls back to the issued recovery capability and `RECOVERY_REQUIRED` credential,
without a new enrollment, secret, or event. It also covers the serialized password-first versus
recovery-start race, old-factor login while a reenrollment is pending, and a state-append failure
after enrollment confirmation has inserted its durable proof. The latter preserves the old active
factor, pending capability, counters, versions, recovery codes, and event count.

The replay sequence is explicit: a successful OTP, a fresh LOGIN challenge, two invalid attempts,
the replayed previously used OTP exhausting the third attempt, denied issuance until that original
window expires, then reissuance. Focused service command: `cv-office-run-tests.py jxp -q
--disable-warnings tests/test_office_mfa.py` → **32 passed**, 812 inherited disposable-fixture
warnings, 2.38 seconds. No production or external action occurred.

## Fix round3 — ventana anclada y base UTC

2026-10-21, Codex. Se conserva la ventana y presupuesto del ancla de primer enrolamiento incluso tras INVALIDATED por corte de token_version; el reemplazo vencido expira el hash histórico exacto. Sol delivery_path confirmó defecto raíz: producción usa SYSTEM/CST, y NOW(6) no es UTC para DATETIME/TOTP. Servicio y eventos privados usan UTC_TIMESTAMP(6); receipts awareUTC; no cambio global de pool ni semántica Classic. Contrato específico office-mfa-utc-time-basis.md. Comprobación readonly3308 no halló tablas office_mfa_* ni columna mfa_version; no dominio desplegado a migrar.

Parent ejecutó `cv-office-run-tests.py jxp -q --disable-warnings tests/test_office_mfa.py tests/test_office_mfa_events.py tests/test_office_migrations.py tests/test_office_session_barrier.py`:84passed,2299fixturewarnings,6.04s antes de últimos límites/tripwire; misma matriz se repite después. Incluye sesiones -06:00/+05:30, código actual, replay, recencia y terminales. No endpoints/JWT nuevos habilitados. Pendiente scoped exactSHA spec+quality, no aprobación inferida por pruebas.

Parent final tras límites UTC/tripwires:84passed,2299warnings,6.74s; git diff --check limpio.

## Fix round4 — conservar causa terminal histórica

2026-10-22, Codex. Al reiniciar una inscripción inicial vencida, la limpieza del
ancla histórica exacta sólo cambia `ISSUED` a `EXPIRED`. Un `EXHAUSTED` o
`INVALIDATED` conserva su causa, contador y `used_at`; el nuevo capability se
emite igual después del vencimiento. La prueba parametrizada realiza los dos
tránsitos reales: tres OTP erróneos hasta `EXHAUSTED`, y corte de
`token_version` seguido de confirmación real hasta `INVALIDATED`; después vence
la ventana y verifica la preservación byte a byte de esos tres campos. Antes
del cambio falló en ambos casos al observar `EXPIRED` y un `used_at` reescrito.

Validación: `cv-office-run-tests.py jxp -q tests/test_office_mfa.py -k
historical_terminal_anchor` → 2 passed; suite MFA completa → **41 passed**
(980 advertencias heredadas del fixture reutilizado). Sin esquema, rutas,
protocolo, commit, push ni producción.

Parent independiente fix4:86passed6.12s2341fixturewarnings, mismo comando completo de cuatro archivos. Delta producto una condición ISSUEDonly; estados terminales preservados bajo casos reales. Revisión exactSHA pendiente.
