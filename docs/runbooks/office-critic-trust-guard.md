# Office critic trust guard

This is an inert, private verifier for `rights.review` evidence. It neither
writes an Office table nor exposes HTTP, a public key, a release decision, or
an approval authority. The Unix socket is fixed at
`/run/jax/office-critic-trust-guard/guard.sock`.

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

Install the unit only with a reviewed `/etc/jax/office-critic-trust-guard.env`
owned by root and mode `0600`, containing the dedicated database credentials,
the expected `OFFICE_CRITIC_TRUST_GUARD_PRINCIPAL`, and numeric UID allowlist.
It must also set a positive, at-most-30-second
`OFFICE_CRITIC_TRUST_GUARD_TIMEOUT_SECONDS`; the guard has no silent deadline
default.
Validate its effective unit, ownership/modes, peer rejection, malformed frame,
signature rejection, revoked-key race, and database-unavailable result before
any future receipt consumer is enabled. This runbook does not authorize that
future activation, publication, or release.
