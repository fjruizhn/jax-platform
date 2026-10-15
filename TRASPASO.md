# Traspaso · P100 frontend estático mínimo

Objetivo: reemplazar el PR #213 rechazado con build versionado, drop-in nginx y rollback que siempre se intenta.

Hecho: rama nueva desde `origin/master` 5eac74ef. Build `ops/frontend/build.py` usa `git archive`, `npm ci`, `npm run build` y manifiesto de SHA/hash por archivo; todavía faltan pruebas, activación, rollback y runbook.

Decisión: Hyde 2026-10-14 20:42 pidió abandonar el controlador anterior y no desplegar antes de su aprobación. No tocar `.github/workflows/policy.yml`.

Siguiente comando: `python3 ops/frontend/build.py --repo . --releases /tmp/p100-releases --revision HEAD` desde este worktree.
