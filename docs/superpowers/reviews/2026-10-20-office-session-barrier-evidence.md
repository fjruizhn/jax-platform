# Task 2 report · session barriers and classic-surface containment

Status: product files frozen for Task 2.  The paired JAX core
`office-namespace.2` was independently approved at
`aeb7278de923ba3417e52e13ec77d01d8bfcd879` and is consumed only through its
typed, in-transaction adapters.  No Office HTTP route is enabled.

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
  SSE re-verifies a second time immediately before each generator yield, so an
  event queued while an account was INTERNAL is still withheld if its class or
  session changes before the consumer receives it.
- `backend/virtual_office/identity_security.py` and
  `backend/api/admin/users.py`: a permanent target-side binding (Office class
  or any profile including REVOKED) blocks classic password, reset link,
  email, role, reactivate, unlock and baja.  Session revocation and classic
  deactivation remain unchanged.  The Task 3 recent-MFA proof is absent, so
  the required root-only identity security authority fails closed.
- `backend/proyectos_documentos/repositorio.py`: all direct classic document
  reads, writes, dispatch, requeue predicates, diagnostics and open jobs now
  require `authorization_namespace='CLASSIC'`.
- `backend/virtual_office/namespace_adapter.py`: JAX project creation calls
  `OfficeNamespaceAuthorizer` in its transaction.  It locks the canonical
  actor/profile and target tenant, accepts only an active
  `OFFICE_SUPERUSER`, permits its cross-tenant Office scope, and appends the
  successful authorization record with the actual actor and target tenant in
  that same transaction.  The typed membership callback uses the exact `.2`
  idempotency/execution signature and has no HTTP entry point.  Typed
  `INVITATION_ACCEPTANCE` fails closed because Task 3 has not yet supplied a
  trusted invitation issuer/acceptance proof; interactive administration keeps
  its fresh database authority check.
- `backend/virtual_office/{capabilities,authority}.py`: the separate
  `ADMINISTER_PROJECT_MEMBERS` operation keeps membership lifecycle cleanup
  distinct from content `MANAGE_PROJECT`.  A project administrator can clean
  an archived exact scope with its current active Office grant; a delegated
  general administrator remains active-scope-only and gains no content right.
- `backend/par_jax.py`: startup requires the exact
  `office-namespace.2` marker and public typed-contract shape before any F2
  bridge or external client can load.  A stale `.1`, missing module, or module
  from another checkout fails closed.
- Focused tests cover real MariaDB restricted HTTP/WS paths, trailing and
  encoded aliases, permanent revoked bindings, reset-link early denial, a real
  Office queued document denied to classic repository operations, and EXPLAIN
  of the modified canonical session lookup.

## Evidence

Command (disposable MariaDB 127.0.0.1:33991):

```text
python3 /tmp/cv-office-run-tests.py jxp -q --disable-warnings \
  tests/test_sesiones_token_version.py tests/test_admin_usuarios_guardas.py \
  tests/test_websocket_isolation.py tests/test_sse_isolation.py \
  tests/test_office_session_barrier.py tests/test_office_identity_guard.py \
  tests/test_proyectos_documentos_repositorio.py tests/test_office_authority.py \
  tests/test_office_namespace_adapter.py tests/test_par_jax_visible.py
216 passed, 133 warnings in 4.74s
```

`EXPLAIN` for `auth.middleware.SQL_ESTADO_DE_SESION` is asserted against the
real fixture and remains `type=const`, `key=PRIMARY`, no filesort/temporary.
The stream tests include a class transition in the registration race (no
`auth_ok`) and an SSE class transition before publish (stream closes, no
event is yielded).  The namespace tests exercise actual disposable MariaDB:
root cross-tenant creation/audit, denial with no residual scope/audit, and the
typed `.2` membership callback creating only canonical `CONTRIBUTOR` state.
They also prove a queued allowed SSE event cannot cross a later class change,
and a syntactically valid invitation execution creates neither membership nor
authorization event before Task 3's issuer proof exists.

## Intentionally not touched

- No Office routes, identity bootstrap, invitation, MFA, SMTP delivery,
  deployment, DNS, service files, or production state.
- No Office project route, invitation flow, acceptance, or MFA assertion.

## Remaining risks / required next gate

- Task 3 must replace the fail-closed identity-security placeholder with the
  persisted recent-MFA/root proof and dedicated Office endpoint, and owns the
  caller context that can construct invitation acceptance execution.
- A final Task 2 review still needs the root's exact-SHA package and broad
  regression run after no other writer is active.

## Final root Task2 gate
2026-10-20 Codex independently253session/admin/identity/namespace/docs/auth/project/pair/WS/SSE tests passed5.72s,133expectedwarnings against disposable33991. RootrequiredqueuedSSEreauthimmediatelybeforeyield and defaultdenialofinvitationexecutionuntiltrustedissuanceproof; bothimplementedwithrealnegatives. namespace.2producerindependentlyapprovedaeb7278d. No Office/MFA/invite route enabled, no bootstrap/production. ExactSHAindependentaudit follows.
