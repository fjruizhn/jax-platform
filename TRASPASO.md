# Traspaso · P100 frontend estático mínimo

Objetivo: reemplazar el PR #213 rechazado con build versionado, drop-in nginx y rollback que siempre se intenta.

Hecho: rama nueva desde `origin/master` 5eac74ef. Build `ops/frontend/build.py` usa `git archive`, `npm ci`, `npm run build` y manifiesto de SHA/hash por archivo; tres builds limpios dieron manifiestos idénticos, uno desde el checkout productivo sin escribirlo. Commits 5935a09 y 4fc3b5b empujados. `switch.py` instala drop-in nginx y revierte sin CAS. Ensayo systemd real en unidad desechable `/run/systemd/system/jax-p100-test-*.service`: aplicar, probar caché/API/WS/Host, relanzar nginx, parar servicio, mutar marcadores de boot/checkout, revertir, aplicar, matar nginx y revertir; 2 pruebas pasaron. No se tocó la unidad productiva. Runbook nuevo preparado.

Decisión: Hyde 2026-10-14 20:42 pidió abandonar el controlador anterior y no desplegar antes de su aprobación. No tocar `.github/workflows/policy.yml`.

Siguiente comando: correr pruebas finales, empujar documentación, retirar `TRASPASO.md`, abrir PR nuevo y cerrar #213 enlazándolo.
