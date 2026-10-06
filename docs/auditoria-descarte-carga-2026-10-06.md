# Auditoría de descartes: carga por tenant — 2026-10-06

## Alcance y montaje

Medición local del camino por tenant de `GET /api/admin/auditoria-descarte`, con el rol `admin` que activa el filtro futuro. MariaDB 12.3.3 se ejecutó en un contenedor temporal sin volumen, publicado solo en `127.0.0.1:33316`, con la base `jax_memory_test`; la API escuchó solo en `127.0.0.1:18081`. No se usaron `jax_memory` ni el MariaDB de producción.

Se sembraron 100 tenants ajenos con 2.000 eventos cada uno (200.000 eventos), y 50 eventos en el tenant consultado. Las solicitudes no filtraron por tipo y usaron la ventana máxima admitida, desde `2025-10-05` hasta `2026-10-06` (366 días de diferencia). El plan comprobado por `EXPLAIN` accedió a `jax_users` mediante `idx_jax_users_tenant_role_status`, a `jacobs_pipelines` mediante `idx_jacobs_pipelines_duenio` y a `jacobs_events` mediante `idx_events_pipeline_auditoria_fecha`.

## Resultados

| Concurrencia | Solicitudes | req/s | p95 (ms) | Errores |
|---:|---:|---:|---:|---:|
| 1 | 100 | 348,92 | 2,94 | 0 |
| 5 | 100 | 853,16 | 7,51 | 0 |
| 10 | 200 | 875,21 | 13,92 | 0 |
| 25 | 500 | 850,56 | 45,92 | 0 |
| 50 | 1.000 | 844,66 | 131,66 | 0 |
| 100 | 2.000 | 294,71 | 1.030,39 | 0 |

La consulta por tenant no leyó los 200.000 eventos ajenos: las filas de evento quedaron acotadas a los pipelines del tenant consultado. La degradación bajo concurrencia 100 es visible; la carga se mantuvo sin errores.

## Comando y procedencia

Ejecutado desde el worktree de `jax-platform` en `feat/auditoria-descarte`, con `CI=1 python3 loadtest/auditoria_descarte_orquestar.py`. El script verifica host, puerto y nombre de la base antes de sembrar, elimina sus filas y restaura el rol del usuario semilla en `finally`.
