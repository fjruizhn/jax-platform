# F2-D Web Chat output lifecycle

F2-D covers only the governed Web Chat HTTP response path. It does not govern
REPL, Comando, Jacobs, LAS MANOS, or other output channels (later F2-E/F2-F
work), and it does not provide the final adversarial closure assigned to
F2-G.

## Observed transport boundary

Web Chat buffers provider output, applies the F2-C governed bridge, constructs
the `ChatResponse`, then passes a Starlette response through FastAPI. The
framework serializes an HTTP response and the ASGI server receives the body
through its `send` callable. There is no client acknowledgement tied to a
response id, attempt, or digest. A WebSocket completion event is not an
acknowledgement of the HTTP reply.

The F2-D response adapter stores the exact compact UTF-8 JSON body before
returning it to ASGI. It transitions to `TRANSPORT_COMMITTING`, revalidates
current claims immediately before the first send, sends those same stored
bytes, then records `OUTPUT_COMMITTED_TO_TRANSPORT` only after the terminal
ASGI body-send call returns successfully.

`OUTPUT_PREPARED` means a MariaDB transaction durably stored the effective
F2-C output, exact response bytes, identity/scope, version identifiers,
idempotency identity, and initial lifecycle event. It does not authorize a
client to retrieve the row by identifier alone.

`OUTPUT_COMMITTED_TO_TRANSPORT` means the server's terminal ASGI body-send
call returned successfully for those prepared bytes. It does not prove
network receipt, browser receipt, UI display, or human reading. A process
failure around the send or before the post-send database transition leaves
`TRANSPORT_OUTCOME_UNKNOWN` or durable `TRANSPORT_COMMITTING`; the system does
not manufacture certainty.

The persisted payload digest covers the exact canonical UTF-8 JSON bytes. The
route also binds the frontend-visible facet, timestamp, degraded flag, and
absence of a canned notice to its trusted request context. A caller cannot
prepare one governed response and substitute those fields at transport time.

`DELIVERY_ACKNOWLEDGED` is intentionally unreachable. Web Chat HTTP has no
authenticated acknowledgement protocol. HTTP return, TCP write, and the
WebSocket completion event do not prove delivery to or reading by a person.

## Durable record, idempotency, and retries

MariaDB InnoDB stores one immutable outbox row per logical idempotency key and
transport attempt, plus append-only-by-application lifecycle event rows. The
two-table schema is versioned as `f2-d.outbox.2` and record schema as
`f2-d.outbox.record.2`; the JAX-owned lifecycle API is `f2-d.lifecycle.2`;
the serialized Web Chat projection is
`f2-d.web-chat-json.1`. Unknown versions fail closed. DDL is reversible via
the explicit legacy downgrade (which removes the expiry column and preserves
rows) or the separate disposable-schema teardown. No production migration is
executed by this implementation task.

The idempotency key is derived from governed scope, request id, response id,
and transport kind. Concurrent duplicate preparations converge only when
every immutable scope, version, governance reference, claim, digest, and byte
field agrees. Reuse with changed output is rejected. Attempts have separate
ids, monotonically increasing sequence numbers, and parent-attempt lineage.
Only a known precommit failure/cancellation can authorize a new attempt; an
uncertain or committed attempt is never silently retried as if it had not
sent. A changed governed output requires a new response/preparation identity.
This provides server-side idempotency and auditability, not exactly-once
delivery or guaranteed single display after network ambiguity.

## Freshness, failure, and recovery

Current observations are revalidated before preparation, after its durable
transaction, before durable commit intent, before the response headers, and
immediately before sending the body. Their minimum authenticated `not_after`
is persisted with the row. Expiry before headers cancels the prepared attempt;
expiry after headers withholds the body and records transport uncertainty. The
stored output is never edited. A process restart snapshot reports prepared,
committing, and committed counts without replaying output or inferring
commitment. Presence of a prepared row never implies transport commitment.

Preparation, lifecycle state changes, and their audit events are transactional.
If persistence or version validation fails, governed dynamic output is not
sent. If a send raises or the post-send state write fails, the system records
uncertainty where possible and never rolls a committed state backward. The
safe static protocol error is not provider text and carries no claim of
governed dynamic delivery.

The effective F2-C render and original sealed-envelope digest are stored
separately. The transport authorization is minted only from the effective
render, so a safe unavailable replacement is what receives transport
authority; the rejected original remains linked only for audit. Raw provider
candidate text and secrets are not stored in the outbox.

The platform startup migration follows existing MariaDB migration
conventions. Tests cover migration schema/up/down contract and database
transaction behavior in the dedicated non-production MariaDB test job. No
production schema, key, credential, systemd unit, or deployment is changed.
