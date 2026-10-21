# Task 3A1 ledger finish report

2026-10-20 · Tier 2 handoff · schema/fences only; no 3A2 writer or route.

## Scope completed

- `backend/virtual_office/migrations.py`
  - compares all three MFA ledger `CHECK_CLAUSE` definitions against the exact
    whitespace-normalized MariaDB 12.3.3 rendering digest; operator grouping,
    parentheses, literal case and NULL guards remain significant;
  - fails closed on altered historical constraints, critical columns or unique
    indexes and does not rewrite evidence;
  - validates/reinstates the legacy challenge surrogate composite FK only when
    absent, repairs the proof-list index ordering, and uses `RECORDED` in the
    fresh authorization-ledger result constraint. The latter was a real
    fresh-schema defect discovered by the isolated migration test.
- `backend/db/runtime_contract.py`
  - rechecks MFA metadata under the restricted DML identity;
  - probes rejected UPDATE/DELETE against the fence and, where MFA evidence
    exists, a normal event row; both probes reread the fence after rollback.
- Focused test files cover exact metadata failures, literal/NULL/operator
  variants, temporary owned-schema fresh and legacy-surrogate upgrades,
  restricted-principal conditional-trigger rejection, and real `EXPLAIN`
  selection for challenge, event proof ordering and challenge/subject lookup.

## Evidence

RED during this finish:

```text
backend/.venv/bin/python /tmp/cv-office-run-tests.py jxp tests/test_office_migrations.py::test_mfa_ledger_fresh_schema_and_legacy_challenge_upgrade_are_idempotent -q
```

Initially failed because a fresh `office_authorization_event` table was created
with `chk_office_authorization_event_result` excluding `RECORDED`, while the
same migration required the canonical form to include it. The DDL now matches
its declared canonical contract.

GREEN:

```text
backend/.venv/bin/python /tmp/cv-office-run-tests.py jxp tests/test_office_mfa_events.py tests/test_office_runtime_contract.py tests/test_office_migrations.py tests/test_office_mfa.py tests/test_office_authority.py tests/test_office_namespace_adapter.py -q
```

Result: **52 passed** on sanitized disposable MariaDB `127.0.0.1:33991`.
The runner reported inherited duplicate-fixture migration warnings only.

Also green: `python -m py_compile` for the five modified Python files and
`git diff --check`.

## Frozen interface / remaining work

3A2 remains the sole owner of `verify_and_consume_totp_in_cursor` and the
private, inseparable MFA proof insert. This change adds no proof receipt API,
no authority input, no auth/JWT/routes, no provider behavior, and no use of
the old `mfa.py` primitive WIP. 3B must not consume or mint a proof until 3A2
establishes that boundary.

Root independent gate: 52 passed in 2.31s, 1596 expected repeated disposable-schema warnings. No production DB touched. Scope 3A1 only; old mfa.py primitives remain unapproved WIP and must be replaced in 3A2.
