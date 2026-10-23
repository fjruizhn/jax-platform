# Task 3B follow-up · loss-safe MFA recovery

2026-10-22 · Codex technical ruling from read-only Sol
`mfa_recovery_replay_contract`, prompted by Task 4A's independent UI audit.
This refines the recovery implementation after approved Task 3B SHA
`b738d1ba1de1d401d0b9efb6079a9e4a3f5ac6aa`; it does not authorize
production rollout or lower any other Task 3B gate.

## Failure being repaired

`POST /mfa/recover` commits one-time code consumption and returns the only
raw recovery bearer after commit. `/mfa/recovery/enrollment/start` then
consumes that bearer and returns the only raw enrollment token and TOTP secret
after commit. A lost response at either hop can strand a user, including the
sole Office root. Password reset does not restore the factor or provide a
self-control path. Frontend-only retry cannot distinguish a committed request
from an uncommitted one.

## Chosen contract

Keep one public `POST /api/auth/mfa/recover`, now atomic from recovery code to
pending enrollment. Its successful body is `{enrollment_token, secret,
expires_at}`. Retire public `/mfa/recovery/enrollment/start` before UI
integration; no parallel legacy path. The 192-bit physical recovery code is
already the natural request identity and bearer. No extra `Idempotency-Key`,
device-local secret, user/receipt lookup endpoint or new authority is needed.

One transaction derives and locks the subject from the code hash, and under
canonical user/challenge/credential lock order:

1. If an exact active receipt for this code exists, verify it against current
   subject, versions, pending credential and output challenge, decrypt its
   authenticated self-binding response envelope, and return the identical
   result. Replay never cuts versions, extends expiry, writes a new event or
   creates another enrollment.
2. Otherwise validate unused code and current version, claim its unique
   receipt, consume all codes and old factor, cut token/MFA versions, append
   the existing RECOVERY proof/state events, create and internally consume
   the RECOVERY challenge, create an ENROLLMENT challenge and pending secret,
   then encrypt the exact response and mark the receipt replayable. Every
   failure rolls back the entire transaction. Cut WS/SSE after commit, also
   after successful replay.

The receipt has unique `source_code_hash`, subject and output challenge FKs,
ceremony ID, new versions, state, `replay_until`, encrypted response and digest.
The encrypted JSON binds receipt/ceremony, code hash, subject, versions,
challenge, token, secret and expiry; decrypt and compare all fields before
returning a secret. A mismatch or missing key yields an opaque 503, with no
mutation or secret in logs. `CLAIMED` must never survive a successful commit;
if found durably, fail closed. Code hash is the sole replay selector.

The old physical code may recover only the *same* enrollment package until
the enrollment expires. Confirmation must erase receipt ciphertext in the
same transaction before promoting the pending secret to ACTIVE. Expiration,
exhaustion, reset and version invalidation terminalize and erase the receipt.
Authorization checks expiry immediately using DB UTC time; cleanup of expired
ciphertext is bounded and durable. Tombstones remain to prevent a second
ceremony from the old code. All response statuses use no-store/private and
no-cache headers. Apply request-size/rate controls before expensive DB work;
concrete limits are configuration/measurement, not copied from password login.

Schema migration/runtime readiness verify exact columns, FKs, indexes,
CHECKs and behavior, including encrypt/decrypt capability. Preflight fails if
any legacy `RECOVERY` challenge, `RECOVERY_REQUIRED` credential or
recovery-origin `PENDING` enrollment remains in flight: its lost raw output
cannot be reconstructed. Stop/drain old binaries before enabling the new
revision. Once a receipt exists, reverting to the old two-hop code is unsafe;
fail closed and roll forward.

The UI sends one recovery code request, stores no raw code/token/secret in
persistent browser storage, and may explicitly retry the *same code* after
an unknown response. It can resume after a browser crash or from another
device by reentering the physical code. It displays the returned enrollment
package and confirms with the existing endpoint. It must not call the
retired public bearer hop.

Required private MariaDB/HTTP proofs: dropped postcommit response replay,
browser restart with original code, concurrent same-code single winner,
used code without receipt denied, rollback at every mutation boundary,
same-name/shape schema drift, tampered ciphertext/metadata, terminal erasure,
version cut/reset, no-store/error redaction, cross-timezone UTC behavior,
representative `EXPLAIN` and load. Independent Sol spec/quality review is
required on the exact implementation SHA before Task 4A consumes it.
