# Traspaso · P100 frontend estático mínimo

Objetivo: reemplazar el PR #213 rechazado con build versionado, drop-in nginx y rollback que siempre se intenta.

Hecho: rama nueva desde `origin/master` 5eac74ef. Build `ops/frontend/build.py` usa `git archive`, `npm ci`, `npm run build` y manifiesto de SHA/hash por archivo; dos builds limpios dieron manifiestos idénticos. Primer commit 5935a09 empujado. `switch.py` instala drop-in nginx y revierte sin CAS. Ensayo systemd real en unidad desechable `/run/systemd/system/jax-p100-test-*.service`: aplicar, relanzar nginx, parar servicio, mutar marcadores de boot/checkout, revertir, aplicar, matar nginx y revertir; 2 pruebas pasaron. No se tocó la unidad productiva.

Decisión: Hyde 2026-10-14 20:42 pidió abandonar el controlador anterior y no desplegar antes de su aprobación. No tocar `.github/workflows/policy.yml`.

Siguiente comando: escribir `docs/runbooks/frontend-estatico-p100.md`, actualizar `docs/runbooks/despliegue.md` y cerrar `TRASPASO.md` antes de auditoría.
