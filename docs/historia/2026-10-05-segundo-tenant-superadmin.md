# Aislamiento del superadmin al incorporar un segundo tenant

- Fecha: 2026-10-05.
- Tipo: HISTORIA / DECISIÓN técnica.
- Quién: Fernando ordenó completar el pendiente hoy; Hyde decidió adelantar la protección antes de incorporar el segundo tenant; Codex implementó en rama propia.
- Fuente: `/home/fruiz/encargos-codex/jxp-segundo-tenant.md`; especificación de credenciales `docs/fase1-credenciales-diseno.md`.

Se limitó la vista de pipelines ocultos y descartados al tenant autenticado. Los proxies de ocultar/restaurar comprueban pertenencia antes de llamar a Jacobs. Las listas, mutaciones y auditoría de usuarios quedan acotadas al tenant. Las métricas del dashboard sobre uso, cuentas, pipelines y hechos filtran el tenant. Se añadieron índices compuestos y pruebas con dos tenants reales en la base aislada de test, incluyendo EXPLAIN.

El KPI de proveedores/llaves sigue global porque `provider` y `credential` son catálogos y credenciales de plataforma compartidos por diseño; esto se declara junto a la consulta. No se tocó producción ni `PENDIENTES.md`. Pendiente de auditoría de Hyde y su integración.

Verificación hasta el registro: TDD comprobado (las nuevas pruebas fallaron con el código previo); 108 pruebas focalizadas pasaron con `JAX_TEST_DB_SUFIJO=jxp_20261005_codex_b1`. Base de test usada: `jax_memory_test_jxp_20261005_codex_b1`, nunca la base productiva.
