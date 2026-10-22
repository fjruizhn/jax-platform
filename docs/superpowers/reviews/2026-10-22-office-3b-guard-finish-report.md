# Task 3B · guard central y mutex de reset — estado parcial

2026-10-22 · escritor Tier 2 Codex. Estado: **NO APROBADO / no listo para 3C**.

## Implementado

- `virtual_office.mfa.load_mfa_config_from_environment` centraliza el parser de
  las cinco variables MFA y `api.auth._mfa_config` conserva el alias público.
- La guarda de identidad Office recibe solo `AuthUser.mfa_proof_event_id`; para
  objetivos Office (incluido perfil revocado) vuelve a resolver
  `ADMINISTER_OFFICE_IDENTITY_SECURITY` con prueba reciente dentro de la misma
  transacción. Sin configuración, prueba o root actual devuelve
  `office_identity_guard_required`.
- Email, rol, reactivación, unlock, ambos controles de reset-link, password y
  baja transmiten ese puntero. El administrador clásico bloquea su perfil tras
  su fila de usuario.
- `lock_office_identity_pair_in_cursor` descubre ambos tenants, los bloquea en
  orden ascendente y luego bloquea actor/perfil y objetivo/perfil. La ruta de
  root reset lo invoca antes del resolver/auditoría.
- El primitivo de reset bloqueado limita el objetivo a `OFFICE_INTERNAL`,
  `EXTERNAL_CLIENT`, o `INTERNAL` con cualquier perfil Office; por tanto una
  cuenta Classic sin perfil se niega antes de cambiar versión, factor o ledger.

## Evidencia ejecutada

```text
/usr/bin/python3 .../cv-office-run-tests.py jxp -q \
  tests/test_office_mfa.py::test_root_reset_rejects_classic_only_target_before_any_state_change
RED: falló porque retornó MFAStateReceipt y cambió el objetivo classic.
GREEN: 1 passed (161 warnings de migraciones preexistentes).

/usr/bin/python3 .../cv-office-run-tests.py jxp -q \
  tests/test_office_identity_guard.py::test_office_root_step_up_from_jwt_allows_office_target_identity_mutation
GREEN: 1 passed (140 warnings de migraciones preexistentes).

/usr/bin/python3 .../cv-office-run-tests.py jxp -q \
  tests/test_office_mfa.py::test_root_reset_reloads_authorization_and_append_failure_rolls_everything_back
GREEN: 1 passed (161 warnings de migraciones preexistentes).
```

Una corrida de `tests/test_office_identity_guard.py tests/test_office_mfa.py`
antes del ajuste de la fixture produjo 53 pass / 1 fail: la prueba de rollback
usaba un objetivo Classic que el nuevo contrato niega antes del append. La
fixture ya fue corregida y su prueba aislada está verde; la corrida combinada
no se repitió aún.

## Trabajo obligatorio pendiente

- Matriz completa de pruebas de guard: pruebas LOGIN recientes, punteros
  foreign/stale/future/old-version, non-root y root sin superadmin clásico,
  perfil objetivo revocado, relectura bajo corte de perfil/versión, y pruebas
  de no-body proof.
- Matriz de objetivo del root reset: HTTP/directo para interno con perfil
  revocado, OFFICE_INTERNAL/EXTERNAL_CLIENT sin perfil, cross-tenant y rollback
  sin `ALLOWED`/evento/cut sobre blanco Classic.
- Pruebas de mutex: carrera de resets cruzados, exclusión con writer clásico,
  orden de claves, drift/desaparición antes de autoridad/efecto.
- `EXPLAIN` real de los `SELECT` PK de usuario, tenant y perfil; matriz de
  autoridad Task 3B completa y corrida seleccionada/final.

No se hizo commit, push, PR, despliegue ni operación contra producción.

## Actualización · matriz central de autoridad

`tests/test_office_authority.py` ahora genera pruebas MFA reales mediante
enrollment y STEP_UP cursor-only, y pasa el `event_id` durable junto con la
configuración de servidor. Las fixtures heredadas que permiten una mutación o
una vista privilegiada dejaron de depender de un allow implícito. La matriz
incluye colaborador ordinario sin MFA, PROJECT_ADMIN sin prueba denegado aun
con `require_recent_mfa=False`, y el mismo principal permitido con prueba
actual para VIEW y MANAGE.

```text
/usr/bin/python3 .../cv-office-run-tests.py jxp -q \
  tests/test_office_authority.py \
  tests/test_office_identity_guard.py::test_office_root_step_up_from_jwt_allows_office_target_identity_mutation \
  tests/test_office_mfa.py::test_root_reset_rejects_classic_only_target_before_any_state_change
22 passed (warnings de migraciones preexistentes); git diff --check limpio.
```

## Actualización · matriz congelada de prueba MFA por rol

La matriz central se amplió sin mocks del resolvedor. Cada principal obtiene
primero un enrollment y STEP_UP por los primitivos MFA reales. Para casos de
recencia, futuro y versión, `_insert_controlled_step_up_proof` agrega un
registro STEP_UP válido y append-only con `occurred_at` calculado por MariaDB;
no actualiza el ledger. Esto prueba exactamente la relectura de sujeto,
resultado, hora y versiones desde el cursor.

- `test_privileged_view_requires_current_verified_proof_but_not_recency`: 4
  parámetros — Office Root, General Admin, PROJECT_ADMIN y aprobador
  profesional. En cada uno VIEW sin prueba se niega y una prueba VERIFIED
  actual pero de 301 segundos permite VIEW.
- `test_old_current_proof_cannot_authorize_mutations_and_false_flag_cannot_downgrade`:
  colaborador ordinario VIEW sin MFA permite; `require_recent_mfa=True` lo
  endurece; `False` no rebaja PROJECT_ADMIN; una prueba vieja actual permite
  VIEW privilegiado y niega MANAGE y administración de identidad root.
- `test_privileged_view_rejects_untrusted_or_version_stale_durable_proofs`: 5
  parámetros — sujeto ajeno, `DENIED`, futuro, token viejo y MFA vieja; los
  cinco devuelven `office_mfa_required`.
- `test_cursor_authority_re_reads_profile_and_grant_revocation_before_deciding`:
  revoca perfil root y grant PROJECT_ADMIN dentro de la misma transacción,
  antes de `authorize_office_in_cursor`; ambos se niegan por su estado actual.

El RED inicial de esta ampliación demostró que un collaborator VIEW con
`require_recent_mfa=True` todavía se permitía. Se corrigió solamente
`virtual_office.authority._apply_mfa_requirement`: la bandera ahora añade una
exigencia verificada; no puede bajar ninguna exigencia derivada.

```text
/usr/bin/python3 .../cv-office-run-tests.py jxp -q \
  tests/test_office_authority.py::test_privileged_view_requires_current_verified_proof_but_not_recency \
  tests/test_office_authority.py::test_old_current_proof_cannot_authorize_mutations_and_false_flag_cannot_downgrade \
  tests/test_office_authority.py::test_privileged_view_rejects_untrusted_or_version_stale_durable_proofs \
  tests/test_office_authority.py::test_cursor_authority_re_reads_profile_and_grant_revocation_before_deciding
RED: collaborator VIEW con require_recent_mfa=True devolvió office_grant.
GREEN: 11 passed (140 warnings de migraciones preexistentes); git diff --check limpio.

/usr/bin/python3 .../cv-office-run-tests.py jxp -q \
  tests/test_office_authority.py \
  tests/test_office_identity_guard.py::test_office_root_step_up_from_jwt_allows_office_target_identity_mutation \
  tests/test_office_mfa.py::test_root_reset_rejects_classic_only_target_before_any_state_change
GREEN: 33 passed (161 warnings de migraciones preexistentes); git diff --check limpio.
```

## Actualización · matriz HTTP de guarda de identidad clásica

`tests/test_office_identity_guard.py` ahora cubre la frontera HTTP y la
relectura de la guarda con SMTP completamente stubbed. No usa la autoridad de
un campo de body/query: solo el puntero MFA del JWT llega al guard.

- `test_office_bound_identity_endpoints_deny_classic_or_unproved_actor`: 7
  parámetros — email, rol, reactivación, unlock, password, reset-link y baja;
  un superadmin Classic con configuración MFA presente pero sin perfil/prueba
  Office recibe 403 y no cambia el objetivo.
- `test_office_root_recent_proof_allows_each_guarded_identity_endpoint`: 7
  parámetros con Office Root + STEP_UP durable reciente; cubre los mismos siete
  endpoints. Reset-link usa settings y envío SMTP stubs.
- `test_guard_http_rejects_old_future_or_foreign_durable_proof`: 3 parámetros
  (prueba vieja, futura y de otro sujeto), todos 403. Los eventos de reloj son
  nuevos registros append-only emitidos con tiempo DB, nunca UPDATE del ledger.
- `test_reset_link_never_reaches_smtp_on_initial_guard_denial_or_claim_injection`
  demuestra que body/query no concede autoridad y SMTP no se consulta.
- `test_reset_link_rechecks_office_authority_before_creating_token_or_sending`
  revoca el perfil root después de la primera guarda (en stub de settings): la
  segunda guarda en TX devuelve 403, no crea token y no llama al envío.
- Las pruebas restantes cubren perfil objetivo revocado, target Classic
  INTERNAL sin perfil, emergency revoke-sessions, deactivate sola frente a
  deactivate+email, root sin rol Classic superadmin, y cortes de perfil/MFA
  antes del cursor.

El primer intento RED halló que el refactor del parser MFA había eliminado un
`import os` aún usado por `_crear_enlace_de_recuperacion`; se restauró, sin
cambiar su comportamiento. La matriz final pasó:

```text
/usr/bin/python3 .../cv-office-run-tests.py jxp -q tests/test_office_identity_guard.py
GREEN: 26 passed (140 warnings de migraciones preexistentes); git diff --check limpio.
```

## Actualización · mutex, alcance de root reset y planes

Avance parcial de la microtarea de mutex; **no declara la matriz completa**.

- `test_root_reset_target_scope_accepts_only_office_bound_classes`: 3
  parámetros positivos: INTERNAL con perfil REVOKED, OFFICE_INTERNAL sin
  perfil y EXTERNAL_CLIENT sin perfil, incluido este último cross-tenant.
- `test_root_reset_rejects_login_proof_even_when_recent_without_effect`:
  LOGIN actual se niega para root reset; STEP_UP sigue siendo el camino de
  éxito ya cubierto por la prueba HTTP existente.
- `test_pair_mutex_fails_closed_for_missing_side_and_explain_uses_pk_indexes`:
  falta de actor/target falla antes de autoridad y EXPLAIN cubre discovery de
  usuarios, tenant y perfil sin ALL/filesort/temporary.
- `test_cross_reset_pair_mutex_serializes_reversed_roots_without_deadlock`:
  dos Office roots en tenants distintos (actor A en tenant numéricamente
  mayor) corren A→B y B→A con límite de 5 s; exactamente uno escribe MFA_RESET
  y el otro recibe `mfa_reset_denied` tras el corte de versión.

```text
/usr/bin/python3 .../cv-office-run-tests.py jxp -q \
  tests/test_office_authority.py tests/test_office_identity_guard.py tests/test_office_mfa.py
GREEN: 112 passed (1274 warnings de migraciones preexistentes); git diff --check limpio.
```

Pendiente de esta microtarea: carrera explícita root-reset contra mutación
Classic guardada del mismo tenant, y simulaciones observables de drift de
tenant/disappearance entre discovery y lock. No se hizo commit, push ni
producción.

## Freeze candidate · full selected Task 3B evidence

Las dos carreras pendientes se completaron:

- `test_same_tenant_root_reset_and_classic_guarded_mutation_complete_without_deadlock`
  ejecuta el reset y una mutación Classic EMAIL sobre el mismo target, ambos
  con orden tenant→actor→target y límite de cinco segundos. El reset único se
  confirma; la mutación Classic recibe `office_identity_guard_required` y no
  registra autoridad Office.
- `test_pair_mutex_rechecks_discovery_under_locks_before_authority_or_effect`:
  2 parámetros. Un cursor envolvente observa el discovery real y ejecuta SQL
  de drift de tenant o desaparición antes de los locks. La función devuelve
  `False`, no crea autorización/efecto, y registra locks de tenants en orden
  ascendente aun con actor inicialmente en tenant mayor.

El recibo EXPLAIN permanece en
`test_pair_mutex_fails_closed_for_missing_side_and_explain_uses_pk_indexes`:
las tres consultas nuevas (discovery `jax_users`, PK `jax_tenants`, PK
`office_account_profile`) no usan `ALL`, `Using filesort` ni `Using temporary`.

```text
/usr/bin/python3 .../cv-office-run-tests.py jxp -q \
  tests/test_office_authority.py tests/test_office_identity_guard.py \
  tests/test_office_mfa.py tests/test_office_mfa_api_security.py \
  tests/test_office_mfa_events.py tests/test_office_mfa_login_api.py \
  tests/test_office_migrations.py tests/test_login_bloqueo.py \
  tests/test_login_rate_limit.py tests/test_login_residuos.py \
  tests/test_login_sin_enumeracion.py tests/test_sesiones_token_version.py \
  tests/test_fijar_password.py
GREEN: 271 passed (3076 warnings de migraciones preexistentes); git diff --check limpio.
```

Estado del escritor Tier 2: **FREEZE CANDIDATE para auditoría exacta de Task
3B**, no aprobación ni paso a 3C. No commit, push, PR ni operación de
producción.

Parent independiente finalcandidate:302passed20.51s3076fixturewarnings. Mismo comando fullselected anterior más tests/test_sesion_unica.py y tests/test_office_session_barrier.py. Gitdiff-check limpio. Endpointload/RPS/p95 siguegatepublishingTask6, no publicreadinessclaim. Próximo fullTask3BscopedSol exactSHA contra4223b7d (3A2approved); no3Cconsumerantesverdictclean.

## Sol rejection round 1/5 · partial implementation

Implemented source fixes before this writer window ended:

- password login now rejects any MFA credential state other than ACTIVE, so
  `PENDING`/`RECOVERY_REQUIRED` cannot fall through to classic token/cookie;
- OTP verification re-reads `locked_until` under the canonical user lock,
  invalidates an issued LOGIN/STEP_UP challenge when a live lock is observed,
  and returns before proof/version/cookie work;
- STEP_UP refresh replacement calls `sesion_vencida` with the current absolute
  session policy before preserving iat/exp;
- added authenticated `POST /api/auth/mfa/reenrollment/start`, requiring
  current password plus recent proof and using the existing atomic primitive;
- auth-wide validation sanitization/no-store now covers `/api/auth/`, while
  retaining `mfa_invalid_request` in the MFA namespace.

Focused existing validation:

```text
/usr/bin/python3 .../cv-office-run-tests.py jxp -q \
  tests/test_office_mfa_api_security.py tests/test_office_mfa_login_api.py \
  tests/test_login_bloqueo.py
GREEN: 40 passed (518 migration warnings).
```

**Not ready for Sol re-audit:** new dedicated HTTP regressions for all five
findings and the requested full suite have not yet been added/run. No commit,
push, PR or production action.

## Sol round 1/5 · final writer state (not audit-ready)

Corrected the login-state implementation to preserve the canonical distinction:
first-enrollment PENDING and DISABLED retain their existing login semantics;
only RECOVERY_REQUIRED and PENDING with current recovery lineage are denied.
The selected existing matrix passed after the source changes:

```text
/usr/bin/python3 .../cv-office-run-tests.py jxp -q --disable-warnings \
  tests/test_office_authority.py tests/test_office_identity_guard.py \
  tests/test_office_mfa.py tests/test_office_mfa_api_security.py \
  tests/test_office_mfa_events.py tests/test_office_mfa_login_api.py \
  tests/test_office_migrations.py tests/test_login_bloqueo.py \
  tests/test_login_rate_limit.py tests/test_login_residuos.py \
  tests/test_login_sin_enumeracion.py tests/test_sesiones_token_version.py \
  tests/test_fijar_password.py tests/test_sesion_unica.py \
  tests/test_office_session_barrier.py
GREEN: 303 passed (3076 migration warnings); git diff --check clean.
```

A real HTTP validation-sentinel regression was added. The planned reenrollment
end-to-end regression was removed after the STEP_UP test fixture failed at the
existing refresh/CSRF boundary; it must be rebuilt using the repository's
canonical authenticated STEP_UP fixture before claiming coverage. Dedicated
HTTP regressions for recovery-origin PENDING/RECOVERY_REQUIRED, future-lock
challenge invalidation, and absolute-window STEP_UP refresh also remain
required. Therefore this is **not ready for Sol re-audit** despite the green
existing suite. No commit, push, PR or production action.

## Sol round 1/5 · resumed evidence

Rebuilt the authenticated reenrollment HTTP regression using the canonical
pre-login access+refresh STEP_UP fixture (not a session-client cookie). It
proves recent-proof/password access to the endpoint, no-store, ACTIVE old
factor plus pending replacement before confirmation, and atomic ACTIVE
confirmation afterward.

```text
/usr/bin/python3 .../cv-office-run-tests.py jxp -q --disable-warnings [full selected list]
GREEN: 304 passed (3097 migration warnings); git diff --check clean.
```

The remaining dedicated HTTP regressions for recovery-state password login,
live lock challenge retirement, and shortened absolute STEP_UP refresh remain
unimplemented; this remains not Sol-ready. No commit/push/prod.

## Sol round 1/5 · three HTTP groups

Added real HTTP regressions for RECOVERY_REQUIRED password login (403, no
refresh cookie), live lock imposed after LOGIN challenge (challenge becomes
INVALIDATED, no version bump), and a STEP_UP refresh rejected under a
shortened current absolute session policy (401/no-store). The full selected
matrix passed:

```text
...cv-office-run-tests.py jxp -q --disable-warnings [15 selected files]
GREEN: 307 passed (3160 migration warnings); git diff --check clean.
```

The recovery-origin PENDING variant is still not an independent HTTP setup in
this file; the name was intentionally not overstated. Existing primitive
coverage proves its lineage behavior. This report is therefore not a claim of
Sol approval. No commit/push/prod.

## Sol round 1/5 · Fix 1 freeze candidate

The final recovery-origin PENDING HTTP fixture uses the real sequence:
ACTIVE enrollment → recovery-code consumption → recovery capability → recovery
enrollment PENDING → password login. Login returns 403 with no access payload
or refresh cookie while the credential remains PENDING.

The absolute-window regression now preserves a JWT expiration 600 seconds in
the future while setting original iat two seconds earlier; a positive current
one-second policy rejects STEP_UP refresh with 401/no-store. This proves the
current absolute policy, rather than JWT exp, controls the denial.

```text
/usr/bin/python3 .../cv-office-run-tests.py jxp -q --disable-warnings [15 selected files]
GREEN: 308 passed (3181 migration warnings); git diff --check clean.
```

All five Sol round 1 findings now have source changes and focused HTTP
coverage. **FREEZE CANDIDATE for exact Sol re-audit; not a Sol approval and
not authorization for 3C.** No commit, push, PR or production action.
