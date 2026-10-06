# Carga del feed global de auditoría de descartes — 2026-10-06

## Alcance y entorno

Medición local del camino global de `GET /api/admin/auditoria-descarte` en MariaDB 12.3.3 desechable, publicada solo en loopback y contra `jax_memory_test`. No se consultó ni modificó `jax_memory`; tampoco se usaron los puertos 3308 ni 3306.

Se sembraron 200.000 eventos de auditoría en un tenant, distribuidos en 100 pipelines y entre los cuatro tipos de evento. La petición se hizo como superadmin, sin filtro de tipo ni de pipeline, con la ventana máxima de 366 días y página de 50 eventos. Es el peor caso del feed global: cada página combina los cuatro tipos sobre un tenant con 200.000 eventos. El SQL usa `idx_events_auditoria_fecha`; el test de `EXPLAIN` verifica la consulta real, el índice y la ausencia de `Using filesort` y `Using temporary`.

Backend y DB corrieron localmente en Hall9000. JAX se fijó al head de JAX #360, `bfdff28f3deaa46a06a352c6f881fee1291fc4b7`. Para preparar una repetición, usar un contenedor efímero MariaDB 12.3.3, inicializar Jacobs con `jacobs.store.init_tables()` y cargar `jax_memory_schema.sql` dentro de `jax_memory_test`, con el mismo recorte de `CREATE DATABASE` y cambio de `USE` que documenta `.github/workflows/policy.yml`. Nunca pasar el SQL sin reescribir `USE jax_memory`.

## Resultado

| Concurrencia | Solicitudes | req/s | p95 (ms) | Errores |
|---:|---:|---:|---:|---:|
| 1 | 100 | 273.85 | 4.43 | 0 |
| 5 | 100 | 552.76 | 11.24 | 0 |
| 10 | 200 | 531.04 | 29.45 | 0 |
| 25 | 500 | 579.22 | 66.35 | 0 |
| 50 | 1.000 | 543.50 | 175.93 | 0 |
| 100 | 2.000 | 559.41 | 397.74 | 0 |

La latencia p95 cruza 100 ms a concurrencia 50, que es el inicio medido de degradación. La tasa de errores fue cero en todos los niveles. Estas cifras describen el contenedor temporal local, no capacidad de producción.

## Registro de decisión

- **Fecha y fuente:** 2026-10-06; encargo `/home/fruiz/encargos-codex/jxp-auditoria-descarte-r3.md`.
- **Decisión ejecutada:** la pantalla y el endpoint quedan solo para superadmin y usan el camino global. El camino tenant se retira hasta el diseño M6; `admin` no pertenece a `VALID_ROLES` en la rama actual.
- **Motivo:** el camino tenant midió 308 ms por petición sobre 200.000 eventos y p95 de 18 s a concurrencia 100. La carga global confirma que el feed indexado degrada a p95 175.93 ms a concurrencia 50, sin error.
- **Alternativas descartadas:** conservar la consulta tenant basada en el índice de dueño, porque su plan aún materializaba y ordenaba el volumen del tenant; agregar `tenant_id` a `jacobs_events` en esta ronda, porque el encargo reserva ese diseño a M6.
- **Pendiente:** M6 debe definir y medir el camino tenant, incluidos su rol autorizado y si los eventos llevan `tenant_id`.
- **Responsabilidad:** Codex implementó y midió la decisión técnica de Hyde descrita en el encargo; no se atribuye esa decisión a otra persona.

## Repetición

El arnés [auditoria_descarte_orquestar.py](../loadtest/auditoria_descarte_orquestar.py) deriva el código backend de su propia ubicación y recibe el checkout compatible de JAX mediante `--jax-repo` o `JAX_REPO_PATH`; no contiene rutas de worktree. Las credenciales de la DB y el puerto HTTP se reciben por entorno. El arnés falla cerrado si la DB no es `jax_memory_test`, el host no es `127.0.0.1`, falta la contraseña, o se usa un puerto reservado.

```bash
export JAX_REPO_PATH="/path/to/compatible/Jax"
export JAX_LOADTEST_DB_HOST=127.0.0.1
export JAX_LOADTEST_DB_PORT=33316
export JAX_LOADTEST_DB_NAME=jax_memory_test
export JAX_LOADTEST_DB_USER=root
read -rsp 'Password de la MariaDB temporal: ' JAX_LOADTEST_DB_PASSWORD
printf '\n'
python3 loadtest/auditoria_descarte_orquestar.py --jax-repo "$JAX_REPO_PATH"
```

El arnés borra al terminar los eventos y pipelines que creó. Los pisos backend con y sin DB se midieron por separado en la misma ronda: `3838 passed / 1 skipped` con DB y `2361 passed / 1481 skipped` sin DB.
