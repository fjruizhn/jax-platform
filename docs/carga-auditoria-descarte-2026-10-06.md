# Carga del feed de auditoría de descartes — 2026-10-06

Medición local del endpoint `GET /api/admin/auditoria-descarte`, en Hall9000, el 2026-10-06. El backend se levantó en `127.0.0.1:18081`; MariaDB temporal fue `127.0.0.1:33316`, base `jax_memory_test`. No se consultó ni modificó la base `jax_memory`.

El arnés [auditoria_descarte_orquestar.py](../loadtest/auditoria_descarte_orquestar.py) sembró 5.000 eventos repartidos entre los cuatro tipos sobre un pipeline y ejecutó peticiones HTTP reales con la misma página limitada a 50 eventos. Cada concurrencia ejecutó `max(100, concurrencia × 20)` solicitudes. El arnés elimina los eventos y el pipeline al terminar.

| Concurrencia | Solicitudes | req/s | p95 (ms) | Errores |
|---:|---:|---:|---:|---:|
| 1 | 100 | 324.93 | 3.33 | 0 |
| 5 | 100 | 589.12 | 11.12 | 0 |
| 10 | 200 | 625.28 | 21.43 | 0 |
| 25 | 500 | 600.75 | 59.79 | 0 |
| 50 | 1.000 | 611.31 | 163.51 | 0 |
| 100 | 2.000 | 618.27 | 356.70 | 0 |

El rendimiento se mantuvo alrededor de 600 req/s desde concurrencia 5. El p95 superó 100 ms a concurrencia 50; se toma **50 clientes simultáneos** como inicio medido de degradación de latencia. La tasa de errores fue cero en todos los niveles. Estos números describen el contenedor temporal local y no deben interpretarse como capacidad de producción.
