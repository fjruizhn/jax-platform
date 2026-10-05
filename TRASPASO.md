# Traspaso continuo — SR2 exact pair

Fecha: 2026-10-05

## Objetivo

Reconciliar Platform PR #189 con `origin/master` y emparejar todo pin SR2 con
JAX PR #341 en `ad930fa5a9650afce83707217c8e301b49ef9782`.

## Hecho

- Se resolvió el único conflicto de `policy.yml` con los pisos medidos de
  `master`, sin alterar los gates exactos de `STEP_STATUS`.
- Los dos pins genéricos y los dos pins del job SR2 apuntan al SHA exacto
  publicado.
- Se conservaron el bridge runtime-status v4 y la comprobación de procedencia
  común de `jacobs`; `master` aportó el guard de tareas de fondo bajo pytest.
- La paridad de extensiones halló cinco formatos declarados por el JAX fijado
  que Platform no aceptaba; la lista y sus pruebas ahora incluyen
  `apng`, `jpe`, `jfif`, `mpo` y `gif`.
- Pruebas enfocadas ejecutadas con `JAX_CI_NO_DB=1`: 17 passed / 1 deselected
  para estado+bridge+SR2, y 2 passed para paridad de extensiones.

## Falta

- Ejecutar la suite backend sin DB, la prueba exact-pair con MariaDB si el
  clonador de la base puede copiar sus triggers, y revisión final.
- Antes de auditoría final, archivar este contenido en `docs/historia/` y
  borrar este archivo.

## Siguiente comando

`cd /home/fruiz/worktrees/sr2-k3-platform/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/worktrees/sr2-step-status-jax JAX_REPO_BASE=/home/fruiz/worktrees/sr2-step-status-jax JAX_CONFIG_PATH=/home/fruiz/worktrees/sr2-step-status-jax/las_manos/config.toml PYTHONPATH=/home/fruiz/worktrees/sr2-step-status-jax:/home/fruiz/worktrees/sr2-step-status-jax/las_manos python3 -m pytest -q -p no:warnings`
