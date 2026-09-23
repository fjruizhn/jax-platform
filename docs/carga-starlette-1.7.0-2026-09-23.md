# Carga: Starlette 1.3.1 → 1.7.0 (2026-09-23)

**Tipo:** HECHO medido · **Quién:** Mr. Hyde (Claude Code), a pedido de Fernando · **Caduca:** si cambia el esquema, el volumen o la infraestructura (regla 4 del rendimiento).

## Qué se midió

- **Arnés:** `loadtest/descartados_orquestar.py`, sin cambios. Es un solo proceso uvicorn con `main:app` en 127.0.0.1, HTTP real contra la base de TEST (`jax_memory_test`), y usuarios sembrados a escala (≥ 5.000 pipelines) y en forma extrema. Recorre los niveles de concurrencia c = 1, 25, 50, 100, 150 y 200, con 7 medidas por nivel y 53.900 pedidos por corrida.
- **Variable única:** el intérprete. La versión 1.3.1 corre con el venv de producción (`/srv/jax-prod/jax-platform/backend/.venv`). La 1.7.0 corre con un venv idéntico salvo `starlette==1.7.0`; tampoco trae `aiosmtplib`, que ningún código importa. Mismo código (rama `deps/starlette-1.7.0`), mismo checkout de jax (`232d927`) y misma máquina (hall9000).
- **Orden:** 1.3.1 → 1.7.0 → 1.7.0 → 1.3.1. El segundo par va invertido para no confundir el orden con la versión.

## Resultado

Ruido: el p95 de la misma versión entre dos corridas da una razón mediana de 1,011 en las dos versiones.

| c | p95 1.7/1.3 (mediana, promedio de 2 corridas) | Ruido 1.3 contra 1.3 (rango) |
|---|---|---|
| 1 | 1,003 | 0,93 – 1,06 |
| 25 | 0,994 | 0,43 – 1,23 |
| 50 | 1,057 | 0,43 – 1,07 |
| 100 | 1,009 | 0,98 – 1,10 |
| 150 | 1,026 | 0,95 – 1,11 |
| 200 | 1,013 | 0,95 – 1,09 |

- Errores: 1 y 0 con la 1.3.1; 1 y 0 con la 1.7.0, sobre 53.900 pedidos por corrida.
- rps a c=25 (mediana): 1.033 y 1.028 con la 1.3.1; 1.000 y 1.033 con la 1.7.0.
- Con una sola corrida, c=150 daba +8,4%. Con la segunda bajó a +2,6%, dentro del ruido. Una corrida sola no alcanzaba para concluir.

**Conclusión:** no hay degradación que supere el ruido medido en ningún nivel.

Aparte, la auditoría adversarial midió en proceso el costo de la pila FastAPI + CORS: +2,7 µs por petición. Es el `send` que la 1.7 envuelve en todas las peticiones para agregar `Vary: Origin`. No se ve frente a una petición con base de datos.

## Lo que NO se midió

- **Subida multipart (`/api/chat/upload`):** en 1.7 cambió el camino de ERROR del parser (400 "Invalid multipart data.", archivos temporales cerrados ante un corte). El bucle de parseo por trozos no cambió.
- **WebSocket y SSE:** en 1.7 cambió el tipo de la excepción al operar sobre un socket cerrado (`WebSocketDisconnected`, subclase de `RuntimeError`, que atrapan los mismos `except`), y CORS envuelve también la SSE. No hubo carga sobre estos canales.
