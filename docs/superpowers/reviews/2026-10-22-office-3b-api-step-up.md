# Task 3B API/JWT finish — bounded STEP_UP delivery

2026-10-22 · Tier 2 source awaiting parent checkpoint and independent review.

`POST /api/auth/mfa/step-up/verify` accepts strict opaque capability/OTP
input, a current signed access session and the same user's valid refresh
cookie. It verifies the locked capability owner before the canonical TOTP
consumer, then mints new access/refresh claims only from the durable STEP_UP
receipt. The endpoint is version-neutral.

The replacement refresh preserves the exact verified source `iat` and `exp`;
only its durable MFA references change. Its cookie lifetime is the remaining
original window. The finite Office auth barrier allowlist contains this route.
STEP_UP and sensitive MFA responses use `Cache-Control: no-store`.

Focused command and result:

```text
python3 /home/fruiz/.local/share/codex-workspaces/office-20261020/cv-office-run-tests.py jxp -q --disable-warnings tests/test_office_mfa.py -k step_up_verify
2 passed, 46 deselected
```

The HTTP/database tests prove durable-proof claims, unchanged token/MFA
versions, exact refresh-window preservation, and that another authenticated
subject cannot consume the owner's capability.

Broader run:

```text
... cv-office-run-tests.py jxp -q --disable-warnings tests/test_office_mfa.py tests/test_office_authority.py tests/test_office_session_barrier.py tests/test_auth.py
80 passed, 4 failed
```

All four failures are authority fixtures that call privileged Office operations
without an MFA proof and now receive `office_mfa_required`. They are separate
central-authority matrix work, not a failure path of the new endpoint.

## API secret-boundary microtask

`tests/test_office_mfa_api_security.py` adds real TestClient/DB coverage for
strict MFA request models: opaque capabilities/recovery codes reject non-ASCII
values, every secret DTO rejects non-string and extra fields, and the generic
422 body is exactly `mfa_invalid_request` without echoing the submitted
sentinel. It also proves unauthenticated, invalid-configuration, validation,
and rate-limit MFA HTTP responses plus status success carry `no-store`.

The finite MFA namespace now has an outer HTTP cache-control boundary so
dependency, barrier and validation responses carry `Cache-Control: no-store`,
not just responses emitted by individual handlers. Existing login limiters are
called before bcrypt in enrollment start, recovery-code regeneration and MFA
disable; the test replaces the canonical limiter with a 429 and proves bcrypt
is not reached.

```text
python3 /home/fruiz/.local/share/codex-workspaces/office-20261020/cv-office-run-tests.py jxp -q --disable-warnings tests/test_office_mfa_api_security.py tests/test_office_mfa.py -k 'api_security or step_up_verify'
21 passed, 46 deselected
```

Remaining 3B work is intentionally open: LOGIN/race/config matrix and the
central authority matrix have not been claimed complete by this microtask.

## LOGIN/API proof microtask

`tests/test_office_mfa_login_api.py` now exercises the active-factor login
contract through the real HTTP route and disposable MariaDB. Password success
returns only an opaque challenge, with no JWT or refresh cookie. A valid OTP
creates one post-cut durable LOGIN proof; access/refresh carry its exact UTC
instant, its new token version, and the unchanged MFA version. The test also
compares `UNIX_TIMESTAMP(last_login)` to that receipt instant.

Negative coverage commits OTP attempts but emits no session for invalid,
replayed/exhausted or version-invalidated capabilities. Two concurrent HTTP
verifiers produce exactly one success. An injected append-only ledger failure
rolls back the TOTP use, challenge consumption and version cut and does not
call the post-commit connection closer. Each missing/noncanonical policy value
keeps an active-factor login fail-closed at `office_mfa_unavailable`; a
no-factor Classic login remains compatible. Ordinary refresh preserves the
same durable MFA claims and does not rotate a refresh cookie.

```text
python3 /home/fruiz/.local/share/codex-workspaces/office-20261020/cv-office-run-tests.py jxp -q --disable-warnings tests/test_office_mfa_login_api.py tests/test_office_mfa_api_security.py tests/test_office_mfa.py -k 'login_api or api_security or step_up_verify'
36 passed, 46 deselected
```

The standalone service-level non-UTC transaction coverage remains in
`test_office_mfa.py`; an end-to-end pool-zone variation remains a follow-up
matrix item. Central Office authority and classic identity guard work remain
outside this writer's bounded scope.

## Final bounded API coverage

The login API matrix now recreates the actual portal-loop DB pool as a
one-connection pool and fixes that connection to each hostile session zone
`-06:00` and `+05:30`. It asserts the HTTP handler used that session, the
challenge TTL remains UTC, `mfa_at` equals the raw UTC proof epoch, and
`UNIX_TIMESTAMP(last_login)` represents the same instant. Refresh preserves
the receipt claims under both zones. This proves the HTTP/JWT boundary rather
than only the cursor service.

It additionally covers an expired LOGIN capability and verifies the actual
WebSocket/SSE registry closers are called only after a successful MFA LOGIN
commit. The append-failure case continues to prove no closer is called before
a transaction succeeds.

```text
python3 /home/fruiz/.local/share/codex-workspaces/office-20261020/cv-office-run-tests.py jxp -q --disable-warnings tests/test_office_mfa.py tests/test_office_mfa_api_security.py tests/test_office_mfa_login_api.py tests/test_sesion_unica.py tests/test_fijar_password.py
130 passed
```

This freezes the bounded API source for parent checkpoint/review. It does not
claim the separate central-role authority or classic identity-guard work.

Parent independiente:130passed9.79s1505fixturewarnings con el comando exacto anterior. Diff-check limpio. Solo checkpoint de API; Task3B completo todavía requiere resolver central/guard/lockorder y revisión independiente SHA exacto.
