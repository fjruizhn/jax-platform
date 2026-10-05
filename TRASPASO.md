# F2-E structured projection — Platform reconciliation

- Objective: reconcile Platform PR #190 with `origin/master` `64b6d1ff706d4d200e757460531c102676b8105a`, pin every exact JAX dependency to published JAX #351 `c2c0aa4d58128ef62333972ce72ce41257b8a1de`, and restore E2a extension parity without weakening the F2-E producer → F2-B → F2-C → F2-D contract.
- Starting head: `b2849fff2896b4ea8eb8025f1979e8150ea0c909`, clean worktree. This is the isolated Platform worktree only; do not mix SR2 files or branches.
- Verified cause: `backend/tests/test_proyectos_documentos_repositorio.py::test_extensiones_aceptadas_coinciden_con_la_compuerta_de_jax` derives JAX's canonical `EXTENSIONES_IMAGEN`; at JAX `c2c0aa4`, it includes `apng`, `jpe`, `jfif`, `mpo`, and `gif`, while `backend/proyectos_documentos/tipos.py` omits them. The test asserts equality and its one-sided-addition mutation guard remains intact.
- Required CI reconciliation: preserve master floors `PISO_PASSED = 3671` and `JAX_CI_MIN_PASSED = "2219"`; the next runner measures the final branch. Known merge conflict is only `.github/workflows/policy.yml`.
- Next command: `git merge --no-ff --no-commit origin/master`
