# Office SSE sibling barrier · round2

2026-10-20 · Codex · HISTORIA verified on disposable MariaDB33991.

Prior scoped Sol review RECHAZADO986323aaa4a5d2a5bfa44f7eac9c0329727cfeaa confirmed three earlier findings addressed but reproduced SSE-only siblings remaining open, because the single EventBus callback closed only its captured queue.

This fix closes all registered WS and SSE siblings on callback or pre-yield reauthorization failure (including infrastructure failure). Existing registries retain cleanup responsibility. Root inspected actual two-SSE/no-WS tests: DB class change and separate injected verifier failure both stop both generators without event data, clear queues/counters and unsubscribe EventBus. No broad EventBus redesign.

Root reran the same broad matrix recorded in round1:223passed133warnings4.60s. Independent exact-SHA review required; no Office routes, external identities, services or production enabled.
