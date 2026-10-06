# Traspaso — segundo tenant en jax-platform

- Fecha: 2026-10-05
- Autor: Codex
- Pendiente: aislar datos y acciones del superadmin por tenant; encargo `/home/fruiz/encargos-codex/jxp-segundo-tenant.md`.
- Rama: `codex/jxp-segundo-tenant` desde `origin/master` (`8a781cb1ccef36522d1134f27cc1edd735cca7b7`).
- Estado: implementación y TDD listos; suite focalizada verde (108 pruebas). Falta ejecutar verificaciones finales, registrar historia, commit/push, abrir PR y pedir auditoría de escalón 3 a la sesión de Hyde. No integrar.
- Archivos: filtros de pipelines, hide/restore, usuarios/auditoría, dashboard, índices de soporte, y pruebas multi-tenant.
- Base de pruebas: `JAX_TEST_DB_SUFIJO=jxp_20261005_codex_b1`; nunca `jax_memory`.
- Decisión: el KPI de llaves permanece global porque `provider`/`credential` son credenciales compartidas por plataforma según `docs/fase1-credenciales-diseno.md`. Los demás KPI con datos de tenant filtran por `tenant_id`.
- Límites: no editar `PENDIENTES.md`; no tocar producción; no integrar.
- Continuación: partir del último commit empujado y actualizar este archivo mientras exista.
