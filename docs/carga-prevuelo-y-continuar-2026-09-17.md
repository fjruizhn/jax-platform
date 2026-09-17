# Carga: pre-vuelo, lista de pipelines y continuar (2026-09-17)

Rama `feat/prevuelo-y-continuar` (jax-platform). LAS CUATRO DEL RENDIMIENTO, #4:
sin número medido no hay GO. Registrado por Mr. Hyde. Todas las horas en CST
(hall9000). Los números salen de las corridas; ninguno es estimado.

## Método de aislamiento (la base y Jacobs falso, iguales en las tres tandas; el spool de uso, no)

- Base `jax_memory_test` (nunca `jax_memory`); servicios de producción :7777 y
  :8080 sin tocar.
- Jacobs falso en `127.0.0.1:17777` que contesta al instante el contrato
  vigente (formas de `backend/tests/jacobs_falso.py`): lo que se mide es el
  costo de la Mesa (auth con relectura de sesión, ajustes, dueño, reenvío y
  validación estricta de la respuesta de Jacobs), no el de Jacobs.
- Backend desde el worktree en `127.0.0.1:18080`, un solo proceso uvicorn.
  `/proc/<pid>/environ` verificado ANTES de cargar: `JAX_DB_NAME=jax_memory_test`,
  `JACOBS_URL=http://127.0.0.1:17777/jacobs`, `LAS_MANOS_URL=http://127.0.0.1:17777`,
  `CANARY_INTERVAL_SECONDS=0`, `JAX_MISSIONS_DIR` en un directorio temporal. Las
  tandas de pre-vuelo (§1) y de `GET /api/pipelines` (§2, Tarea 12) NO
  aislaron `JAX_USAGE_SPOOL_DIR`: corrieron contra el default de producción
  `/srv/jax-data/usage-spool`. Sólo la tanda de continuar (§3) lo aisló en un
  directorio temporal (el default habría vaciado el drenaje de uso hacia la
  base de tests).
- Usuario operator desechable (tenant 1) y sus pipelines creados en
  `jax_memory_test` y BORRADOS después (conteo 0 verificado); token de acceso
  firmado para ese usuario.
- Cliente httpx asíncrono con c obreros; c=1 n=200, c=25 n=1000, c=50 n=1000.
- Al terminar: procesos detenidos y puertos 17777/18080 sin listener (verificado con `ss`).

## 1. `POST /api/pipelines/preflight` — 05:17 CST

Usuario desechable user_id=306318. Veredicto del Jacobs falso: 1 paso, 0.30 USD.

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 834,14 | 1,01 | 1,96 | 4,02 | 7,73 |
| 25 | 1000 | 1000 | 0 | 1013,75 | 22,92 | 33,14 | 41,95 | 50,06 |
| 50 | 1000 | 1000 | 0 | 989,69 | 47,73 | 62,61 | 72,38 | 88,05 |

## 2. `GET /api/pipelines`

### Usuario sin pipelines — 05:17 CST

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 2077,29 | 0,43 | 0,57 | 0,74 | 5,13 |
| 25 | 1000 | 1000 | 0 | 3703,68 | 6,36 | 8,75 | 13,85 | 15,14 |
| 50 | 1000 | 1000 | 0 | 3810,07 | 12,71 | 15,19 | 17,12 | 19,33 |

### Peor caso — 05:24 CST

Usuario user_id=312684 con `LISTA_PIPELINES_MAX`=50 pipelines detenidos
(aborted/expired alternados); por pipeline 200 eventos de otros tipos, 20
`STEP_FAILED` con `error` de 300 caracteres y 1 `PIPELINE_ABORTED`: **11.050
eventos**. Respuesta: 50 pipelines, 19.555 bytes, cada uno con su causa.

EXPLAIN de la consulta real (`sql_eventos_de_causa(50)`): `range` sobre
`idx_events_pipeline`, **11.050 filas examinadas para devolver 1.050**
(r_filtered 9,5 %): el índice es sólo `(pipeline_id)` y `event_type` se filtra
después de leer la fila. Sin filesort ni temporary. La lista usa
`idx_jacobs_pipelines_duenio` (ref, 50 filas, 0,22 ms). Por partes (mediana de
100): SQL de causa 7,63 ms (6,39 ms sin payload), `causa_de` en Python 1,0 ms.

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 103,04 | 9,58 | 10,04 | 10,29 | 16,09 |
| 25 | 1000 | 1000 | 0 | 231,65 | 105,07 | **147,33** | 181,03 | 217,26 |
| 50 | 1000 | 1000 | 0 | 224,41 | 188,23 | **451,37** | 645,20 | 896,29 |

## 3. Continuar — 06:17 CST

Usuario desechable user_id=325478 dueño de UN pipeline `aborted`
(`c0a7a9e0-0000-4000-8000-00000000c0a7`). Cuerpo `{"reasignar": {"4": "ada"}}`.
Jacobs falso: `continue/preflight` → continuable, pasos a correr [4, 5],
reusados [0..3], veredicto 0.30 USD (bajo el umbral 0.50: sin confirmación);
`continue` → 200 con `run_epoch`, pasos y costos. Conteo del Jacobs falso al
final: 6.602 `continue/preflight` (2 del humo + 2.200 de la corrida
descartada + 2.200 directos + 2.200 internos de `/continue` de la corrida
medida), 2.201 `continue` (1 del humo + 2.200), 0 cuerpos malos: cada
`/continue` medido llegó a Jacobs.

**Qué se midió en `/continue` y por qué (el cupo).** `/continue` ocupa un cupo
del tenant (`resource_manager`, en memoria, conjunto de pipeline_id) contra el
ajuste `max_pipelines`. Se continúa siempre el MISMO pipeline: el conjunto
queda en 1 elemento, así que con `max_pipelines` ≥ 2 cada pedido recorre el
camino completo (sesión, dueño, cupo, ajustes, pre-vuelo de continuar,
consentimiento, `POST continue`, saneo de la respuesta, admisión y evento
`pipeline_continued`). En `jax_memory_test` el ajuste estaba en 1 (rastro de
fixtures de otras suites): se subió a 3 (el valor sembrado por la migración)
sólo durante la carga y se repuso a 1 al terminar. Una primera corrida con el
valor 1 todavía en el caché de ajustes (TTL 30 s) dio 429 `limite_de_pipelines`
en los 2.200 pedidos (p95 16,2 ms a c=25): es el rechazo de cupo diseñado, no
se cuenta como medición del camino completo y se descartó. Muchos pipelines
DISTINTOS de un tenant por encima de `max_pipelines` reciben ese 429 por
diseño; no es un error de carga.

### `POST /api/pipelines/{id}/continue/preflight`

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 650,02 | 1,37 | 1,84 | 2,33 | 5,95 |
| 25 | 1000 | 1000 | 0 | 760,17 | 27,66 | 59,99 | 79,41 | 108,69 |
| 50 | 1000 | 1000 | 0 | 705,33 | 54,37 | 151,25 | 211,30 | 264,03 |

Muestra previa del mismo endpoint (06:16 CST, misma instancia): c=1 p95 1,89 ms;
c=25 p95 28,07 ms (991 rps); c=50 p95 91,68 ms (837 rps), 0 errores. La
diferencia entre las dos muestras es ruido de la máquina (otra suite de tests
corría en hall9000 al mismo tiempo).

### `POST /api/pipelines/{id}/continue`

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 417,75 | 2,21 | 2,74 | 4,98 | 7,22 |
| 25 | 1000 | 1000 | 0 | 431,93 | 49,22 | 113,53 | 152,09 | 256,11 |
| 50 | 1000 | 1000 | 0 | 469,54 | 90,03 | 207,15 | 268,31 | 342,66 |

Lectura: 0 errores a c=25 y c=50 en los dos endpoints. `/continue` hace dos
llamadas a Jacobs (pre-vuelo y continue) y dos lecturas de DB (sesión, dueño):
~2× el costo de `continue/preflight`, que es lo esperado. Con un solo worker
uvicorn la latencia a c=25/50 es cola, no trabajo por pedido (c=1 p95 < 3 ms).

## VERDAD OPERACIONAL — spool de uso sin tocar por las tandas sin aislar (verificado 2026-09-17 06:24 CST)

Las tandas de pre-vuelo (§1) y de `GET /api/pipelines` (§2) no aislaron
`JAX_USAGE_SPOOL_DIR` (ver Método de aislamiento). Se verificó que, pese a
eso, no dejaron rastro en el spool de producción ni en la tabla de uso de
tests:

- `ls -la /srv/jax-data/usage-spool`: directorio vacío, mtime del directorio
  2026-09-16 13:31:56 (`stat`), anterior a las corridas de hoy (§1 05:17 CST,
  §2 05:17/05:24 CST, §3 06:17 CST).
- `SELECT COUNT(*) FROM axioma_usage WHERE spool_id IS NOT NULL AND
  created_at >= '2026-09-16 12:00:00'` contra `jax_memory_test`: 0 filas (de
  4.198 filas totales en la tabla).

Verificado leyendo directamente el filesystem y con un SELECT de solo
lectura contra `jax_memory_test` (nunca `jax_memory`).

## VERDAD OPERACIONAL pendiente

- **Re-medir el peor caso de `GET /api/pipelines`** cuando exista
  `idx_events_pipeline_tipo` ON `jacobs_events (pipeline_id, event_type)` (plan
  J, R20, lo crea el repo jax): con él las filas examinadas deberían bajar de
  11.050 a las 1.050 devueltas. Hasta medirlo, los p95 de 147 ms (c=25) y 451
  ms (c=50) son los vigentes.
- Estos números son contra un Jacobs falso instantáneo: miden la Mesa. Si
  cambia el esquema, el volumen de eventos o la infraestructura, caducan.
