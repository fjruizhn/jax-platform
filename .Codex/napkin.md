# Napkin Runbook

## Execution & Validation

1. **[2026-09-26] Memory selection does not grant access**
   Do instead: obtain candidates through the authorized B9 reader and select only those returned envelopes; keep database-backed scope checks intact.
2. **[2026-09-26] Adoption time does not establish factual recency**
   Do instead: preserve timestamps and provenance, use bounded representative selection, and validate recall against an actual legacy FACT identity.
3. **[2026-09-26] Chat proof requires a real authenticated turn**
   Do instead: distinguish administrator reader diagnostics from browser chat evidence; never use a fabricated JWT or read session credentials.

## Coordination

1. **[2026-09-26] Other sessions own project selector and attachments**
   Do instead: restrict this branch to memory prompt selection and its tests; preserve project authorization, attachment routes and shared-file edits from other branches.
