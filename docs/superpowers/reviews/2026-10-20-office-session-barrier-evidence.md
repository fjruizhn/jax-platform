# Task 2 report · session barriers and classic-surface containment

Status: product files frozen for the implemented Task 2 barrier slice.  This
is **not** a completion claim for the JAX namespace adapter: core
`office-namespace.2` remains under its independent audit and was not consumed.

## What changed

- `backend/auth/{models,middleware,office_barrier}.py`: account class is read
  from canonical `jax_users` for every session verification; unknown/missing
  values fail closed.  A strict method/path allowlist permits only subject
  session/recovery flows and the dedicated Office namespace to restricted
  classes.
- `backend/api/auth.py`: login and `/me` expose the DB-derived class for later
  Office UI, never as a JWT authority claim.
- `backend/main.py` and `backend/api/events.py`: WebSocket and SSE reject
  restricted classes before classic registration, re-check the freshly read
  class after registration before `auth_ok`, and re-check before every event
  delivery.  A trusted classification change therefore closes the stream
  without emitting a queued event.  HTTP/SSE are also intercepted by the
  central middleware before a classic handler runs.
- `backend/virtual_office/identity_security.py` and
  `backend/api/admin/users.py`: a permanent target-side binding (Office class
  or any profile including REVOKED) blocks classic password, reset link,
  email, role, reactivate, unlock and baja.  Session revocation and classic
  deactivation remain unchanged.  The Task 3 recent-MFA proof is absent, so
  the required root-only identity security authority fails closed.
- `backend/proyectos_documentos/repositorio.py`: all direct classic document
  reads, writes, dispatch, requeue predicates, diagnostics and open jobs now
  require `authorization_namespace='CLASSIC'`.
- Focused tests cover real MariaDB restricted HTTP/WS paths, trailing and
  encoded aliases, permanent revoked bindings, reset-link early denial, a real
  Office queued document denied to classic repository operations, and EXPLAIN
  of the modified canonical session lookup.

## Evidence

Command (disposable MariaDB 127.0.0.1:33991):

```text
python3 /tmp/cv-office-run-tests.py jxp -q --disable-warnings \
  tests/test_office_session_barrier.py tests/test_office_identity_guard.py \
  tests/test_proyectos_documentos_repositorio.py
158 passed, 133 warnings in 4.60s
```

Earlier focused compatibility suite after all repository constraints:

```text
... tests/test_office_identity_guard.py tests/test_office_authority.py \
tests/test_admin_usuarios_guardas.py tests/test_sesiones_token_version.py \
tests/test_office_session_barrier.py tests/test_proyectos_documentos_repositorio.py \
tests/test_proyectos_documentos_processing_owner.py
197 passed, 133 warnings in 10.10s
```

`EXPLAIN` for `auth.middleware.SQL_ESTADO_DE_SESION` is asserted against the
real fixture and remains `type=const`, `key=PRIMARY`, no filesort/temporary.
The stream tests include a class transition in the registration race (no
`auth_ok`) and an SSE class transition before publish (stream closes, no event
is yielded).

## Intentionally not touched

- No Office routes, identity bootstrap, invitation, MFA, SMTP delivery,
  deployment, DNS, service files, or production state.
- No consumption of the unaudited JAX `office-namespace.2` adapter/marker.

## Remaining risks / required next gate

- The namespace adapter and its startup exact-marker gate wait for the core
  Task 3 independent audit.  They must be implemented and reviewed before an
  Office project or invitation route is enabled.
- Task 3 must replace the fail-closed identity-security placeholder with the
  persisted recent-MFA/root proof and dedicated Office endpoint.
- A final Task 2 review still needs the root's exact-SHA package and broad
  regression run after no other writer is active.

## Independent root verification
Codex2026-10-20:207focusedidentity/authority/admin/session/stream/docs/auth/project tests passed5.34s,133expectedwarnings on disposable33991. Existing registration-race expectation tightened to closebeforeauth_ok. This is a reversibledevelopment checkpoint only; Task2 namespaceadapter/parity and independentaudit stillrequired.
