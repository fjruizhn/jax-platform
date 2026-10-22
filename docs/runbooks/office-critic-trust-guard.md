# Office critic trust guard

This is an inert, private verifier for `rights.review` evidence. It neither
writes an Office table nor exposes HTTP, a public key, a release decision, or
an approval authority. The Unix socket is fixed at
`/run/jax/office-critic-trust-guard/guard.sock`.

**Activation is deliberately disabled.** This revision returns
`INERT_CONTRACT_PENDING` from its readiness function and refuses `run_guard`;
the systemd contract lives only at
`ops/virtualoffice/contracts/office-critic-trust-guard.systemd-contract.txt`,
outside the restore/install glob. A later contract must define a
root-owned independent expected trigger `DEFINER` and canonical `SQL_MODE`,
then attest both under MDL. This revision must not infer either value from the
database or `CURRENT_USER`.

## Boundary and protocol

Only callers whose Linux UID appears in
`OFFICE_CRITIC_TRUST_GUARD_ALLOWED_UIDS` pass the kernel `SO_PEERCRED` check.
The service also has a filesystem boundary: it runs as `jaxcriticguard` and
the socket directory is group-accessible only to `jaxoffice`. Do not treat
filesystem permissions as a replacement for the peer-credential allowlist.

Each connection carries exactly one big-endian uint32 length followed by at
most 20 KiB of UTF-8 JSON. The exact request is:

```json
{"protocol":"office-critic-trust-guard/v1","operation":"verify_evidence","nonce":"base64url-32-bytes","receipt":{}}
```

The response echoes the nonce and is one of `VERIFIED_EVIDENCE`, `REJECTED`,
or `UNAVAILABLE`. A verified response contains only the receipt bindings and
canonical digest; it never returns a public key or the raw envelope.

## Dedicated database principal

Provision a new principal with no inherited role and only these grants, after
substituting the deployed database and account names:

```sql
GRANT SELECT ON `jax_memory`.office_trusted_critic_key TO 'jaxcriticguard'@'localhost';
GRANT SELECT ON `jax_memory`.office_trusted_critic_key_revocation TO 'jaxcriticguard'@'localhost';
GRANT TRIGGER ON `jax_memory`.* TO 'jaxcriticguard'@'localhost';
```

`TRIGGER` is needed because the complete schema attestor reads trigger
metadata. It can also permit trigger DDL in MariaDB, so this account is used
only by this process and is never an application or migration principal. The
guard itself contains no DDL. Confirm the exact effective grants before
activation; any extra table, schema DML, role, or `GRANT OPTION` is a failed
deployment gate.

The guard begins `READ COMMITTED` and `READ ONLY`, opens both trust tables
with `LOCK IN SHARE MODE` to hold real metadata locks throughout the
transaction, calls the v13 trust-anchor attestor supplied by the anchor
contract inside that transaction, loads the named key, verifies Ed25519, and
re-reads the revocation tombstone under a fresh READ COMMITTED statement
before rollback. Trigger `DEFINER` and session `SQL_MODE` remain
environment-owned rather than attested digest inputs; the anchor contract
instead requires its exact trigger set/body/action order and behavioral DML
checks. Database/configuration failure returns `UNAVAILABLE`; it never falls
back to a web principal or a file key.

## Deployment remains closed

Do not install, enable, or manually start a unit in this revision. The
future activation review must specify a root-owned `/etc/jax/office-critic-
trust-guard.env` with the dedicated database credentials, expected
`OFFICE_CRITIC_TRUST_GUARD_PRINCIPAL`, numeric UID allowlist, and a positive,
at-most-30-second `OFFICE_CRITIC_TRUST_GUARD_TIMEOUT_SECONDS`; the guard has no
silent deadline default. It must validate effective unit ownership/modes, peer
rejection, malformed frame, signature rejection, revoked-key race,
database-unavailable result, and the new DEFINER/SQL_MODE contract before any
future receipt consumer is enabled. This runbook does not authorize that
future activation, publication, or release.

The activation review also needs a real MariaDB MDL/revocation-race exercise,
a client-side check of the server UID via `SO_PEERCRED`, a strict global
connection/concurrency limit, and table-specific grants. Do not label the
principal "read-only": its metadata `TRIGGER` privilege can permit DDL in
MariaDB.
