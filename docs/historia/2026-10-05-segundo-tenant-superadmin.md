# Aislamiento del superadmin al incorporar un segundo tenant

- Fecha: 2026-10-05.
- Tipo: HISTORIA / DECISIÓN técnica.
- Quién: Fernando ordenó completar el pendiente hoy; Hyde decidió adelantar la protección antes de incorporar el segundo tenant; Codex implementó en rama propia.
- Fuente: `/home/fruiz/encargos-codex/jxp-segundo-tenant.md`; PR #204 y su revisión adversarial de ronda 2.

Se limitó la vista de pipelines ocultos y descartados al tenant autenticado. Los proxies de ocultar/restaurar comprueban pertenencia antes de llamar a Jacobs. Las listas, mutaciones y auditoría de usuarios quedan acotadas al tenant. Las métricas del dashboard sobre uso, cuentas, pipelines y hechos filtran el tenant. Se añadieron índices compuestos y pruebas con dos tenants reales en la base aislada de test, incluyendo EXPLAIN.

El KPI de proveedores/llaves quedó sin cambios para decisión de Fernando. No se tocó producción ni `PENDIENTES.md`. Pendiente de auditoría de Hyde y su integración.

Verificación hasta el registro: TDD comprobado (las nuevas pruebas fallaron con el código previo); 108 pruebas focalizadas pasaron con `JAX_TEST_DB_SUFIJO=jxp_20261005_codex_b1`. Base de test usada: `jax_memory_test_jxp_20261005_codex_b1`, nunca la base productiva.

## Corrección de ronda 2 — 2026-10-06

- Tipo: HISTORIA / CORRECCIÓN.
- Quién: Fernando ordenó ejecutar el encargo de ronda 2; Codex corrigió el PR y abrió un PR separado de Jacobs.
- Fuente: `/home/fruiz/encargos-codex/jxp-segundo-tenant-r2.md`; PR [jax-platform #204](https://github.com/fjruizhn/jax-platform/pull/204); PR [Jax #358](https://github.com/fjruizhn/Jax/pull/358).

La afirmación anterior de que `docs/fase1-credenciales-diseno.md` justificaba un KPI global era incorrecta y queda corregida: ese documento no fija el alcance tenant del KPI. M6 queda expresamente reservado a Fernando; el KPI no se cambia. La plataforma dejó de ejecutar DDL sobre `jacobs_pipelines`; el índice `idx_pipelines_tenant_status_date` ahora se declara en Jacobs `init_tables()`.

Se añadieron filtros tenant a memoria (hechos de usuario por el tenant del usuario; hechos compartidos con `user_id IS NULL` por el tenant de `jax_project_scope`; hechos globales sin proyecto excluidos) y uso. `recover` y la auditoría de descarte verifican tenant antes de acceder a Jacobs. Se retiraron los `FORCE INDEX` fail-soft en consultas admin y se corrigieron los filtros de dashboard.

Verificación de ronda 2: suite completa del backend, con `JAX_TEST_DB_SUFIJO=codexr2oct06final`, `JAX_REPO_PATH=/home/fruiz/wt/jax-pipelines-tenant-index`, `PYTHONPATH` apuntando al checkout y `JAX_WORKSPACE_DIR=/tmp`: **3803 passed, 6 skipped**. Pruebas unitarias del índice Jacobs: **18 passed**. La suite usó `jax_memory_test_codexr2oct06final`; nunca `jax_memory`. Los PRs quedan sin integrar, a la espera de auditoría Sol e integración por Hyde.
