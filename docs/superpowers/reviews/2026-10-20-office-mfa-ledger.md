# Task 3A1 ledger finish report

## Correction round1 · 2026-10-21 · Codex

### Round2 · same-schema foreign-key binding

ScopedSol rejected1051cd9 because REFERENCES metadata omitted referenced schema. Tier2 now compares REFERENCED_TABLE_SCHEMA with DATABASE() for each of the five canonical FKs. The real disposable negative matrix preserves missing/same-schema-wrong-target/cross-schema-homonym cases and canonical restoration, using only an owned random shadow schema. Root independently reran21focused tests (3.14s,1652warnings) before the final test-only restoration of the old wrong-target controls; the covering one-test case is rerun after that restoration. Private harness modejax now selects JAX-first imports and the existing JAX test interpreter; no production credentials or guard overrides.

The initial a11ea53 candidate was rejected: a pristine ledger protected only its fence could pass the runtime check, FK metadata was incomplete, and surrogate AUTO_INCREMENT was not verified. This correction adds exact metadata for all five ledger FKs/critical columns and checks the canonical trigger definition when visible. The restricted DML principal verifies metadata plus real rolled-back UPDATE/DELETE denial. With no ordinary event, it inserts a transaction-local DENIED canary, never persists a VERIFIED canary, and rereads the immutable fence after rollback. Known legacy pre-surrogate upgrade restores only that missing composite FK; an unexpectedly missing current FK fails closed.

Durable private harness now lives under `/home/fruiz/.local/share/codex-workspaces/office-20261020/`; it reconstructs disposable MariaDB12.3.3 on loopback33991 without production credentials. Tier2 reports 54 relevant tests passed in3.47s,1841 inherited fixture warnings, plus py_compile and diff check. Parent independent rerun and exact-SHA scoped review follow; this is not an activation verdict. Old mfa.py remains unapproved until3A2.

Parent independently reran the same six relevant files with the durable harness: **54passed,1840warnings,3.63s**. Command: `python3 /home/fruiz/.local/share/codex-workspaces/office-20261020/cv-office-run-tests.py jxp -q --disable-warnings tests/test_office_mfa_events.py tests/test_office_runtime_contract.py tests/test_office_migrations.py tests/test_office_mfa.py tests/test_office_authority.py tests/test_office_namespace_adapter.py`. The warning-count difference is fixture history, not a reduced test floor. No live account/provider/production change.

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
