# Traspaso — jax-platform #204, segundo tenant, ronda 3

Fecha: 2026-10-06. Rama `codex/jxp-segundo-tenant`, base `c63475a9e16b19e590ba6891f8d87658766228e0`.

## Cambios de esta ronda

- `fundir_hechos` bloquea y selecciona solo hechos del tenant autenticado; los ids ajenos producen 404 sin modificación. La prueba cruzada confirma que tampoco se filtra el id.
- El agrupamiento semántico filtra por `tenant_id`. Los hechos globales siguen excluidos, según la decisión M6; el comentario registra la observación entregada (0 activos globales de 104) y el SQL para repetir el conteo.
- Las consultas administrativas de ocultos/descartados fuerzan el índice que incluye tenant; las pruebas EXPLAIN exigen ese índice. Se restauró la comprobación del índice cubriente de uso por período.
- Se corrigieron el nombre/cita del EXPLAIN, la lista de referencias M6 en el PR #204 y se retiró `_require_pipeline_exists` junto con su cita obsoleta.

## Pruebas y estado

- Base por sesión; nunca `jax_memory`. Variables de full-suite: `JAX_TEST_DB_PERMITIR_INSTANCIA_DE_PRODUCCION=1`, `JAX_TEST_DB_SUFIJO=codex_jxp_r3_full2_20261006`, `JAX_WORKSPACE_DIR=/tmp`, `JAX_REPO_PATH=/home/fruiz/wt/jax-pipelines-tenant-index`.
- Dependencias de `requirements-archivos.txt` instaladas en `/tmp/codex_jxp_r3_extractors`, fuera del entorno compartido.
- Suites dirigidas finales: 5 passed en memoria/citas/M1/EXPLAIN; suite completa del backend: **3807 passed, 6 skipped**.
- Variables de suite completa: `JAX_TEST_DB_PERMITIR_INSTANCIA_DE_PRODUCCION=1`, `JAX_TEST_DB_SUFIJO=codex_jxp_r3_full4_20261006`, `JAX_WORKSPACE_DIR=/tmp`, `JAX_REPO_PATH=/home/fruiz/wt/jax-pipelines-tenant-index`. Base usada: `jax_memory_test_codex_jxp_r3_full4_20261006`; nunca `jax_memory`.
- Dependencias de `requirements-archivos.txt` instaladas en `/tmp/codex_jxp_r3_extractors`, fuera del entorno compartido.
- Auditoría adversarial Tier 3: APROBADO, sin hallazgos abiertos.
- No se integró ni se empujó código; no se tocó producción ni `PENDIENTES.md`. El cuerpo del PR #204 sí se actualizó conforme al encargo.
