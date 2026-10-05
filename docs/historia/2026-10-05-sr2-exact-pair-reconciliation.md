# SR2 exact-pair reconciliation

Fecha: 2026-10-05
Autor: Codex

## Qué se hizo

Platform PR #189 se reconcilió con `origin/master` en
`64b6d1ff706d4d200e757460531c102676b8105a`. Los cuatro pins del par SR2 se
actualizaron al head publicado de JAX PR #341:
`ad930fa5a9650afce83707217c8e301b49ef9782`.

El conflicto de `.github/workflows/policy.yml` se resolvió conservando los
pisos de pruebas medidos por `master`, sin rebajar ninguno, y manteniendo el
job `f2e-sr2-step-status-exact-pair` con sus comprobaciones de ambos SHA.

## Decisiones técnicas

- Se preservaron los dos contratos que coinciden en los módulos de estado:
  `runtime-status` v4 y la comprobación de que `jacobs`, `jacobs.store` y
  `jacobs.models` se cargan desde el mismo checkout JAX configurado; además se
  incorporó el guard de `master` que evita tareas de fondo bajo pytest.
- La prueba de paridad de extensiones contra JAX detectó que Platform no
  aceptaba `apng`, `jpe`, `jfif`, `mpo` y `gif`, aunque el extractor del SHA
  fijado sí las declara. La única lista de Platform y su prueba se ampliaron
  con esos formatos.

## Verificación

- Contrato de estado, bridge y STEP_STATUS sin MariaDB:
  `17 passed, 1 deselected`.
- Paridad de extensiones: `2 passed` después de un ciclo rojo que mostró los
  cinco formatos faltantes.
- `tests/test_processing_job_exact_pair.py` con el workspace temporal del
  workflow: `3 passed, 1 skipped`.
- Suite backend sin DB con JAX exacto y `JAX_WORKSPACE_DIR=/tmp/jax-workspace`:
  `2225 passed, 1453 skipped`.
- `policy.yml` se parseó correctamente con PyYAML.

## Límite local

La prueba MariaDB exact-pair no pudo ejecutarse en esta máquina: antes de
colectar, el clonador de bases detiene la sesión porque `jax_memory_test` tiene
cinco triggers que todavía no puede copiar. El control evita que la prueba se
ejecute contra un esquema distinto de la plantilla.
