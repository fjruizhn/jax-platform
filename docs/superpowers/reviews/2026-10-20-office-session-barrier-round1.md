# Office barriers · fix round 1

2026-10-20 · Codex · HISTORIA verified on disposable MariaDB33991.

Previous verdict RECHAZADO on 0fc40d16e4fea26126a23850c286894dc95d5964 found middleware ordering/pending password, heartbeat/sibling streams, classic document writes/oracle, and absent-class fallback. This checkpoint corrects all four, subject to independent exact-SHA review.

Root inspected all product diffs and the actual stack/stream/DB regression tests. HTTP barrier now precedes upload limiting, classification explicitly admits pending password only for early denial; heartbeat rechecks canonical session and closes all WS/SSE channels; classic hide/restore/requeue/reprocess require CLASSIC; missing/unknown class cannot pass a direct stream guard. Test doubles explicitly identify a trusted INTERNAL class.

Root command:
```text
python3 /tmp/cv-office-run-tests.py jxp -q --disable-warnings tests/test_sesiones_token_version.py tests/test_admin_usuarios_guardas.py tests/test_websocket_isolation.py tests/test_sse_isolation.py tests/test_office_session_barrier.py tests/test_office_identity_guard.py tests/test_proyectos_documentos_repositorio.py tests/test_office_authority.py tests/test_office_namespace_adapter.py tests/test_par_jax_visible.py
221 passed, 133 warnings in 4.58s
```

No Office routes, accounts, delivery, services or production enabled. No final approval claimed before review.
