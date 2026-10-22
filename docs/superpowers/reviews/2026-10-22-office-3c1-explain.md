# Office 3C1 · EXPLAIN evidence

2026-10-22 · Codex · disposable loopback MariaDB `127.0.0.1:33991` only.

This evidence follows the statements in the Office routes and repository, not
similar hand-written queries.  `backend/tests/test_office_explain_3c1.py`
seeds 160 additional permanent Office profiles before measuring the directory,
executes a real member grant followed by its exact idempotent replay, and runs
`EXPLAIN` on every load-bearing read below.

| Path / exact read | Chosen access | Plan contract |
| --- | --- | --- |
| `GET /people` tenant cursor | `jax_users.PRIMARY` range, then `jax_tenants.PRIMARY` const and `office_account_profile.PRIMARY` eq-ref | Ordered by `u.user_id`; no full scan, temporary table, or filesort. The PRIMARY range preserves the cursor ordering, so a new tenant-leading JAX index was not justified by this measurement. |
| profile enable/global-role read | `jax_users.PRIMARY` const, `jax_tenants.PRIMARY` const, `office_account_profile.PRIMARY` eq-ref | Canonical target profile read is a bounded identity probe; no sort or temporary table. |
| actor/target identity lock | the same primary-key chain | Both actor and target lock statements are keyed by canonical `user_id`; no target-directory scan is introduced. |
| membership receipt replay | composite `office_membership_mutation_receipt.PRIMARY(namespace,idempotency_key)` const | Measured after an actual grant and exact replay, so it tests the terminal receipt lookup rather than an empty-table optimizer shortcut. |
| admin delegation | `uq_office_admin_delegation(tenant_id,user_id,capability)` const | Exact scoped capability lookup; no scan/sort/temp. |
| professional grant | `uq_office_professional_grant(project_id,user_id,capability)` const | Exact project capability lookup; no scan/sort/temp. |

The regression test requires every plan table to have a non-`ALL` access type
and non-null selected key; it rejects `Using filesort` and `Using temporary`.
It also pins the directory driver and terminal receipt to their respective
primary keys.  MariaDB's estimated `rows` field is intentionally not frozen:
it is statistic-dependent, while the test supplies the meaningful guard here
(160-profile skew, cursor limit 51, and bounded indexed access).

Verification was run against this worktree with the private environment (not
production): `tests/test_office_explain_3c1.py` → **1 passed** in **1.01 s**.
The pre-existing authority and atomic membership EXPLAIN checks also passed:
**2 passed** in **0.89 s**.  No schema/index migration was added.
