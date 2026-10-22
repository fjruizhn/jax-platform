# Task 3C1 bounded checkpoint · independent Tier 3 audit

2026-10-22 · Sol `office_3c1_checkpoint_audit` · exact source SHA
`495ba4ec7f2d6309bf353a44758a31368faf7815`.

**SPEC RECHAZADO · QUALITY RECHAZADO.** This is a review of implemented
bootstrap and Office project list/create only. Routes deliberately left for
later 3C1 work were not counted as defects. Passing 328 private MariaDB tests
does not override the following findings.

1. **BLOCK · project listing bypasses the resolver** (`backend/api/virtual_office/projects.py`). The separate profile and data queries permit a profile-revocation race. The internal grant query lacks active JAX membership, `INTERNAL` audience, same-tenant and active-tenant checks. Privileged views lack current durable verified MFA. The current positive test itself grants without membership. Use a coherent batch equivalent of central `VIEW_PROJECT` authority and prove no/revoked membership, wrong audience/tenant, inactive tenant, missing proof and revoke-between-reads cases.
2. **BLOCK · non-self bootstrap evidence** (`backend/virtual_office/bootstrap.py`). `credential_self_control_in_cursor` ignores `user_admin_audit.actor_user_id`; a `password_reset_completed` attributed to another actor satisfies self-control. Require actor equals target for self events, while later admin events invalidate earlier evidence; prove with real audit rows.
3. **BLOCK · fail-open bootstrap attestation** (`backend/virtual_office/migrations.py`, `backend/db/runtime_contract.py`). It checks FK and trigger names, but not FK mappings, CHECK clauses, trigger body/behavior or PK/collation. Same-name no-op triggers pass. Require exact metadata and runtime update/delete denial, and test drift on isolated schema.
4. **MAJOR · confidential responses cacheable.** `/api/virtual-office/*` responses lack `Cache-Control: no-store`, unlike `/api/auth/*`. Apply to 2xx and errors; test.
5. **MAJOR · unbounded list/no measured indexes.** The grant/relation filters are user-leading while schema indexes are project-leading; list returns every scope. Add bounded pagination and representative real-query `EXPLAIN`, retaining only measured useful indexes.

The reviewer found no additional blocker in JAX-first creation, Phase B's lock
order, exact request/scope reread, revoked-group handling or same-key repair.
Root assigned a sole Tier 2 backend writer to resolve the findings. The next
candidate requires independent exact-SHA review; this verdict is not carried
forward automatically.
