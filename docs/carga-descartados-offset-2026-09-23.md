# Carga: OFFSET profundo del listado de descartados — 2026-09-23

Registrado por Mr. Hyde, hall9000, 2026-09-23 (~06:50–07:25 CST). Cierra el
hueco declarado en `docs/carga-descartados-pipelines-2026-09-22.md`
("No se midió `OFFSET` profundo"). Todos los números salen de las corridas;
ninguno es estimado salvo donde se dice "calculado" o "no medido".

## Veredicto

**Sí, el costo crece LINEAL con el offset**, en los dos listados de
descartados que aceptan `offset`. El índice se usa (sin `Using filesort`,
sin `Using temporary`), pero `LIMIT 51 OFFSET m` lee y tira `m` entradas del
índice: **Handler_read = offset + 51, exacto**, en las tres corridas.

- Pendiente del SQL solo: **~1,2–1,3 µs por fila saltada** (0,28 ms en
  offset 0 → 5,7 ms en offset 4.950 → 65,5 ms en offset 49.950).
- Con el mínimo pedido (usuario "extremo", 5.000 descartados), la ÚLTIMA
  página cuesta a c=1 un **p95 de 9–10 ms contra 1,9–2,1 ms** de la primera
  (~5×), y a c=25 el rps baja de ~1.200–1.300 a ~770–800. Todavía cómodo.
- Con 50.000 descartados (corrida extra, fuera del mínimo), la última página:
  **p95 71 ms a c=1 (38× la primera)** y a c=25 **p95 365 ms, 92 rps** (contra
  32 ms y 1.260 rps en offset 0). El admin, con 56.000 descartados globales:
  p95 414 ms y 83 rps a c=25 en la última página.
- La interfaz pagina con "ver más" (`HistorialContenido.jsx` y
  `AdminPipelinesOcultos.jsx`, `limite=50`, `offset=lista.length`): recorrer
  un historial de N descartados cuesta **cuadrático** en total —
  Σ(offset+51) ≈ N²/100 lecturas; para N=50.000, ~25 millones de lecturas de
  índice (calculado, no medido).
- **La paginación por cursor (keyset) lee 52 filas en CUALQUIER página**
  (medido con el SQL prototipo, abajo): 0,28–0,43 ms constante, y devuelve
  exactamente la misma página que el `OFFSET` en los 40 puntos comparados (tres corridas).

**Hallazgo lateral, más serio que la pendiente: el plan de
`SQL_DESCARTADOS_DEL_USUARIO` NO es estable.** Con 50.000 descartados del
usuario (corrida r3), el optimizador eligió `idx_pipelines_ocultos`
(`status, descartado_at` — el índice GLOBAL del admin) en 3 de los 8 offsets
(100, 1000 y 5000) y `idx_pipelines_descartados` en los otros 5, sin cambiar
el SQL ni los datos. Con `idx_pipelines_ocultos` el motor recorre los
descartados de TODOS los usuarios y filtra por `user_id` después: en r3 eso
costó +1.000 lecturas (offset 100 → 1.151 en vez de 151; offset 5000 →
6.051 en vez de 5.051), que son exactamente los 1.000 descartados del otro
usuario sembrado, más recientes. En esta siembra los usuarios no están
intercalados; **con los descartados de muchos usuarios intercalados en el
tiempo (producción real), ese plan cuesta en proporción al total GLOBAL de
descartados, no al del usuario — inferido del mecanismo, NO medido.** Es la
misma clase de defecto que la fix round 3/4 de `auditoria-descarte` cerró
con `FORCE INDEX` (ver el documento del 22). No se corrigió aquí: esta rama
sólo mide.

## Método

- Arnés: `loadtest/descartados_offset_medir.py` (nuevo) — REUSA
  `descartados_orquestar.py` (importado: mismo `construir_env`, mismas
  barreras `_verificar_no_apunta_a_produccion`, JWT de la corrida releído de
  `/proc/<pid>/environ` y comparado contra el de producción, puertos
  18081/17778) y la misma siembra/limpieza (`descartados_seed.py` /
  `descartados_limpiar.py`). Único cambio a la siembra:
  `CARGA_N_DESCARTADOS_EXTREMO` (por defecto 5.000, el valor del 22).
- Base `jax_memory_test` (MariaDB 12.3.3, 127.0.0.1:3308), verificada con
  `SELECT DATABASE()` antes de escribir y en `/proc/<pid>/environ` del
  backend levantado. `jax_memory` no se tocó. Ningún servicio systemd tocado.
- Backend real del worktree (`main:app`, un solo uvicorn, misma topología que
  `jax-platform.service`), cliente `httpx.AsyncClient` por TCP.
- Venv de ejecución: `/srv/jax-prod/jax-platform/backend/.venv` (sólo
  ejecutar). Esquema de jax: checkout limpio de `jax` master `19686fe`.
- Formas sembradas (las del 22): "escala" (4.000 visibles + 1.000
  descartados), "extremo" (3 visibles + 5.000 descartados; 50.000 en r3),
  5.000 descartados de 500 usuarios para el admin, más las filas previas de
  `jax_memory_test` (16.485 pipelines, 0 descartados).
- Por offset (0, 100, 1000, 2500, 5000, 10000, 25000 y la ÚLTIMA página):
  1. petición de control (200, 50 pipelines, `has_more` correcto — la última
     da `has_more=False`, la carga no mide páginas vacías);
  2. SQL real, sin HTTP, en otra conexión: `EXPLAIN`, `ANALYZE` (plan
     ejecutado, `r_rows`), diferencia de `Handler_read_*` de la sesión
     (descontado el ruido del propio `SHOW STATUS`), y p50 de 30 ejecuciones;
  3. el SQL prototipo por cursor para la MISMA página (cursor = última fila
     de la página anterior), con las mismas medidas, y comparación de ids;
  4. HTTP a c=1 (n=200) y c=25 (n=500).
- Endpoints: `GET /api/pipelines?estado=discarded&limite=50&offset=X`
  (`SQL_DESCARTADOS_DEL_USUARIO`, `backend/api/pipelines.py`) y
  `GET /api/admin/pipelines/descartados?limite=50&offset=X`
  (`SQL_DESCARTADOS_ADMIN`, `backend/api/admin/pipelines_ocultos.py`).
- Tres corridas: r1 y r2 idénticas (ruido), r3 con 50.000 en "extremo".
  **0 errores en las tres.** Limpieza verificada por el script
  (`filas_restantes_de_la_siembra=0`) y aparte, después, con una consulta
  propia: `jax_memory_test.jacobs_pipelines` volvió a 16.485 filas y 0
  descartadas tras cada corrida.

Reproducir:

```bash
JAX_REPO_PATH=/ruta/a/checkout/de/jax \
  python3 loadtest/descartados_offset_medir.py r1
CARGA_N_DESCARTADOS_EXTREMO=50000 JAX_REPO_PATH=... \
  python3 loadtest/descartados_offset_medir.py r3_50k
```

## Tabla resumen (p95 en ms; rps a c=25)

| serie | offset | Handler_read | c=1 p95 r1 / r2 | c=25 p95 r1 / r2 | c=25 rps r1 / r2 |
|---|---|---|---|---|---|
| usuario extremo (5.000) | 0 | 51 | 2,07 / 1,91 | 33,1 / 30,4 | 1198 / 1296 |
| | 100 | 151 | 2,46 / 2,11 | 36,4 / 32,1 | 1170 / 1276 |
| | 1000 | 1051 | 3,85 / 3,68 | 34,9 / 58,7 | 1156 / 970 |
| | 2500 | 2551 | 6,51 / 6,57 | 47,4 / 37,2 | 968 / 1131 |
| | 4950 (última) | 5001 | 10,03 / 9,06 | 53,2 / 53,8 | 799 / 767 |
| admin (11.000 globales) | 0 | 51 | 1,97 / 1,90 | 61,0 / 60,9 | 988 / 977 |
| | 5000 | 5051 | 8,79 / 7,80 | 43,9 / 44,8 | 802 / 839 |
| | 10950 (última) | 11001 | 16,26 / 16,74 | 84,7 / 89,2 | 418 / 397 |

| r3: 50.000 del usuario / 56.000 globales | offset | Handler_read | c=1 p95 | c=25 p95 | c=25 rps |
|---|---|---|---|---|---|
| usuario extremo | 0 | 51 | 2,00 | 31,8 | 1260 |
| | 10000 | 10051 | 17,98 | 83,1 | 425 |
| | 25000 | 25051 | 38,75 | 183,8 | 179 |
| | 49950 (última) | 50001 | 71,09 | 365,4 | 92 |
| admin | 0 | 51 | 1,84 | 63,8 | 952 |
| | 25000 | 25051 | 36,65 | 174,1 | 197 |
| | 55950 (última) | 56001 | 74,05 | 414,5 | 83 |

Ruido entre r1 y r2: a c=1 los p95 difieren ≤ 1 ms; a c=25 hay saltos
aislados de hasta ~25 ms (p. ej. extremo offset 1000: 34,9 contra 58,7) sin
tendencia — la tendencia con el offset sí se repite en las dos.

## EXPLAIN / ANALYZE (consulta real)

```
SQL_DESCARTADOS_DEL_USUARIO, r1 y r2, todos los offsets:
  type=range key=idx_pipelines_descartados Extra="Using index condition; Using where"
  offset 0:    ANALYZE r_rows=51     Handler_read_key=1 + Handler_read_prev=50
  offset 4950: ANALYZE r_rows=5000   Handler_read_prev=5000
  -- sin filesort, sin temporary; r_rows = offset + 51

SQL_DESCARTADOS_DEL_USUARIO, r3 (50.000): el plan CAMBIA según el offset
  offset 0, 2500, 10000, 25000, 49950: key=idx_pipelines_descartados, Extra="Using where"
  offset 100, 1000, 5000:              key=idx_pipelines_ocultos,     Extra="Using where"
  offset 100 con ocultos: r_rows=1151 (esperado 151)  <- lee descartados de otro usuario

SQL_DESCARTADOS_ADMIN, todas las corridas:
  type=range key=idx_pipelines_ocultos Extra="Using index condition" o "Using where"
  offset 55950: r_rows=56000, Handler_read_prev=56000, SQL p50 67 ms

Prototipo keyset (misma página), cualquier offset:
  type=range, mismo índice que el plan elegido, Handler_read TOTAL=52, SQL p50 0,28-0,43 ms
```

`rows` del `EXPLAIN` (estimado) queda en 9.748–80.261 sin relación con el
offset: el que dice la verdad es `r_rows` de `ANALYZE`.

## Propuesta (NO implementada)

1. **Paginación por cursor (keyset)** en los dos listados. La respuesta
   agrega `cursor` = `(descartado_at, pipeline_id)` de la última fila; la
   página siguiente pide
   `... AND (descartado_at < %s OR (descartado_at = %s AND pipeline_id < %s))
   ORDER BY descartado_at DESC, pipeline_id DESC LIMIT %s`.
   `pipeline_id` desempata: `descartado_at` no es único, y hoy el `OFFSET`
   con empates tampoco garantiza un orden estable entre páginas.
2. **Índices: los que ya existen alcanzan.** InnoDB agrega la clave primaria
   (`pipeline_id`) al final de todo índice secundario, así que
   `idx_pipelines_descartados (user_id, tenant_id, status, descartado_at)` e
   `idx_pipelines_ocultos (status, descartado_at)` ya sirven el
   `ORDER BY descartado_at DESC, pipeline_id DESC` sin filesort — medido: el
   prototipo no hizo filesort y leyó 52 filas.
3. **`FORCE INDEX (idx_pipelines_descartados)`** en la consulta del usuario
   (hoy y con keyset), por el hallazgo de plan inestable de arriba — con
   keyset sobre `idx_pipelines_ocultos` y usuarios intercalados, el cursor
   tampoco acotaría el costo.
4. El `offset` puede quedarse como compatibilidad mientras la interfaz migra
   (expandir → migrar → contraer), con un tope explícito.

## Lo que NO se midió

- **El costo del plan con `idx_pipelines_ocultos` sobre descartados de muchos
  usuarios INTERCALADOS en el tiempo** (la forma de producción). Aquí cada
  usuario sembrado tiene sus descartados en un bloque contiguo de tiempo, así
  que el plan malo sólo pagó +1.000 lecturas. Es lo primero que habría que
  medir antes de decidir el `FORCE INDEX`.
- Cuántos descartados tiene hoy el usuario más cargado en producción (no se
  consultó `jax_memory`).
- c=50 y mayores (el barrido del 22 ya muestra que el cuello a partir de c≈50
  es el proceso único de uvicorn, no la consulta).
- El listado principal `GET /api/pipelines` (sin `estado`) con offset
  profundo: tiene la misma forma `LIMIT/OFFSET` sobre `idx_pipelines_visibles`
  y, por el mismo mecanismo, debe crecer igual — **no medido**.
- `/api/admin/pipelines/ocultos`: misma forma, no medido.
- El recorrido completo de "ver más" (el costo cuadrático está calculado, no
  medido).

## Vigencia

VERDAD OPERACIONAL de 2026-09-23 sobre `jax_memory_test` en hall9000. Caduca
si cambian el esquema, los índices, el volumen de descartados o la cantidad de
workers de producción.

## Resultados completos por corrida

Columnas: Handler_read total del SQL; `r_rows` de `ANALYZE`; p50 del SQL
solo; HTTP a c=1 y c=25 (p50/p95/p99 ms y rps); errores; lecturas y p50 del
prototipo keyset para la misma página y si devolvió la misma página.

### r1 (jacobs_pipelines=31490, discarded=11000)

| serie | offset | Handler_read | r_rows | SQL p50 ms | c=1 p50 | c=1 p95 | c=1 p99 | c=1 rps | c=25 p50 | c=25 p95 | c=25 p99 | c=25 rps | err | keyset HR | keyset SQL p50 ms | misma página |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| usuario_escala | 0 | 51 | 51.00 | 0,29 | 1,83 | 2,16 | 2,50 | 554,56 | 20,35 | 23,97 | 27,43 | 1184,38 | 0 | — | — | — |
| usuario_escala | 100 | 151 | 151.00 | 0,41 | 1,90 | 2,42 | 2,74 | 503,64 | 18,19 | 53,20 | 84,57 | 1038,86 | 0 | 52 | 0,34 | sí |
| usuario_escala | 950 | 1001 | 1000.00 | 1,25 | 3,04 | 4,01 | 4,30 | 312,36 | 19,85 | 42,50 | 62,84 | 1062,78 | 0 | 52 | 0,39 | sí |
| usuario_extremo | 0 | 51 | 51.00 | 0,34 | 1,76 | 2,07 | 2,48 | 554,54 | 18,59 | 33,09 | 43,35 | 1197,59 | 0 | — | — | — |
| usuario_extremo | 100 | 151 | 151.00 | 0,44 | 1,85 | 2,46 | 2,63 | 521,88 | 18,58 | 36,38 | 56,08 | 1170,11 | 0 | 52 | 0,37 | sí |
| usuario_extremo | 1000 | 1051 | 1051.00 | 1,19 | 2,90 | 3,85 | 4,20 | 326,54 | 19,17 | 34,93 | 47,03 | 1155,80 | 0 | 52 | 0,33 | sí |
| usuario_extremo | 2500 | 2551 | 2551.00 | 2,62 | 4,99 | 6,51 | 7,82 | 194,40 | 22,69 | 47,44 | 57,17 | 968,07 | 0 | 52 | 0,42 | sí |
| usuario_extremo | 4950 | 5001 | 5000.00 | 5,69 | 8,54 | 10,03 | 11,40 | 116,48 | 29,02 | 53,23 | 63,21 | 799,43 | 0 | 52 | 0,34 | sí |
| admin | 0 | 51 | 51.00 | 0,26 | 1,65 | 1,97 | 2,59 | 595,74 | 20,23 | 60,97 | 76,74 | 988,28 | 0 | — | — | — |
| admin | 100 | 151 | 151.00 | 0,33 | 1,58 | 1,87 | 2,15 | 608,63 | 19,83 | 54,49 | 94,95 | 988,88 | 0 | 52 | 0,29 | sí |
| admin | 1000 | 1051 | 1051.00 | 1,02 | 2,44 | 3,11 | 3,45 | 391,91 | 20,85 | 55,89 | 78,47 | 963,41 | 0 | 52 | 0,29 | sí |
| admin | 2500 | 2551 | 2551.00 | 2,33 | 4,66 | 5,58 | 6,09 | 211,18 | 21,80 | 31,08 | 35,37 | 1091,18 | 0 | 52 | 0,37 | sí |
| admin | 5000 | 5051 | 5051.00 | 4,34 | 8,11 | 8,79 | 9,55 | 122,25 | 29,38 | 43,85 | 53,64 | 801,80 | 0 | 52 | 0,28 | sí |
| admin | 10000 | 10051 | 10051.00 | 10,28 | 13,99 | 15,88 | 17,84 | 71,24 | 52,05 | 79,13 | 97,62 | 459,36 | 0 | 52 | 0,32 | sí |
| admin | 10950 | 11001 | 11000.00 | 9,62 | 14,52 | 16,26 | 18,04 | 68,26 | 57,69 | 84,66 | 101,23 | 417,68 | 0 | 52 | 0,34 | sí |

### r2 (jacobs_pipelines=31490, discarded=11000)

| serie | offset | Handler_read | r_rows | SQL p50 ms | c=1 p50 | c=1 p95 | c=1 p99 | c=1 rps | c=25 p50 | c=25 p95 | c=25 p99 | c=25 rps | err | keyset HR | keyset SQL p50 ms | misma página |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| usuario_escala | 0 | 51 | 51.00 | 0,29 | 1,55 | 1,76 | 2,13 | 626,52 | 19,72 | 22,78 | 26,22 | 1220,48 | 0 | — | — | — |
| usuario_escala | 100 | 151 | 151.00 | 0,40 | 1,96 | 2,34 | 3,07 | 493,78 | 17,43 | 32,62 | 39,36 | 1275,86 | 0 | 52 | 0,34 | sí |
| usuario_escala | 950 | 1001 | 1000.00 | 1,19 | 2,90 | 3,88 | 4,31 | 329,50 | 18,80 | 34,51 | 44,81 | 1181,48 | 0 | 52 | 0,32 | sí |
| usuario_extremo | 0 | 51 | 51.00 | 0,34 | 1,62 | 1,91 | 2,08 | 610,52 | 17,46 | 30,37 | 35,99 | 1296,43 | 0 | — | — | — |
| usuario_extremo | 100 | 151 | 151.00 | 0,38 | 1,78 | 2,11 | 2,51 | 545,01 | 17,26 | 32,13 | 38,30 | 1275,80 | 0 | 52 | 0,33 | sí |
| usuario_extremo | 1000 | 1051 | 1051.00 | 1,18 | 2,86 | 3,68 | 3,99 | 335,35 | 19,03 | 58,73 | 104,46 | 969,92 | 0 | 52 | 0,33 | sí |
| usuario_extremo | 2500 | 2551 | 2551.00 | 2,64 | 4,68 | 6,57 | 9,53 | 201,73 | 20,04 | 37,16 | 45,66 | 1131,33 | 0 | 52 | 0,34 | sí |
| usuario_extremo | 4950 | 5001 | 5000.00 | 4,82 | 7,44 | 9,06 | 9,96 | 131,51 | 29,86 | 53,76 | 62,71 | 767,18 | 0 | 52 | 0,31 | sí |
| admin | 0 | 51 | 51.00 | 0,25 | 1,49 | 1,90 | 2,12 | 654,21 | 19,26 | 60,87 | 86,38 | 977,13 | 0 | — | — | — |
| admin | 100 | 151 | 151.00 | 0,33 | 1,59 | 2,06 | 2,16 | 610,69 | 19,81 | 57,55 | 93,59 | 988,24 | 0 | 52 | 0,28 | sí |
| admin | 1000 | 1051 | 1051.00 | 1,09 | 2,47 | 2,95 | 3,49 | 392,14 | 21,49 | 67,21 | 121,04 | 860,38 | 0 | 52 | 0,36 | sí |
| admin | 2500 | 2551 | 2551.00 | 2,32 | 3,83 | 4,92 | 5,74 | 252,58 | 22,83 | 35,34 | 42,59 | 1034,33 | 0 | 52 | 0,37 | sí |
| admin | 5000 | 5051 | 5051.00 | 4,33 | 6,41 | 7,80 | 8,66 | 153,30 | 27,02 | 44,83 | 53,54 | 838,63 | 0 | 52 | 0,30 | sí |
| admin | 10000 | 10051 | 10051.00 | 9,80 | 12,42 | 14,12 | 15,52 | 80,14 | 46,83 | 74,17 | 94,90 | 501,25 | 0 | 52 | 0,30 | sí |
| admin | 10950 | 11001 | 11000.00 | 11,04 | 12,43 | 16,74 | 22,60 | 77,26 | 60,85 | 89,20 | 110,38 | 397,17 | 0 | 52 | 0,29 | sí |

### r3_50k (jacobs_pipelines=76490, discarded=56000)

| serie | offset | Handler_read | r_rows | SQL p50 ms | c=1 p50 | c=1 p95 | c=1 p99 | c=1 rps | c=25 p50 | c=25 p95 | c=25 p99 | c=25 rps | err | keyset HR | keyset SQL p50 ms | misma página |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| usuario_escala | 0 | 51 | 51.00 | 0,28 | 1,56 | 1,85 | 2,09 | 635,19 | 19,65 | 22,57 | 27,67 | 1225,37 | 0 | — | — | — |
| usuario_escala | 100 | 151 | 151.00 | 0,39 | 1,79 | 2,26 | 2,56 | 535,56 | 18,82 | 55,46 | 84,31 | 1025,67 | 0 | 52 | 0,34 | sí |
| usuario_escala | 950 | 1001 | 1000.00 | 1,26 | 2,80 | 3,51 | 3,99 | 341,36 | 18,12 | 31,48 | 39,45 | 1249,85 | 0 | 52 | 0,31 | sí |
| usuario_extremo | 0 | 51 | 51.00 | 0,28 | 1,68 | 2,00 | 2,11 | 584,24 | 17,60 | 31,77 | 40,29 | 1259,58 | 0 | — | — | — |
| usuario_extremo | 100 | 1151 | 1151.00 | 1,19 | 2,80 | 3,70 | 4,37 | 332,59 | 18,82 | 35,96 | 43,67 | 1179,70 | 0 | 52 | 0,35 | sí |
| usuario_extremo | 1000 | 2051 | 2051.00 | 2,08 | 4,40 | 5,78 | 6,58 | 220,84 | 21,67 | 45,03 | 53,43 | 1010,57 | 0 | 52 | 0,34 | sí |
| usuario_extremo | 2500 | 2551 | 2551.00 | 2,62 | 5,29 | 6,94 | 7,54 | 184,20 | 23,09 | 44,68 | 55,86 | 974,80 | 0 | 52 | 0,37 | sí |
| usuario_extremo | 5000 | 6051 | 6051.00 | 7,06 | 10,63 | 12,34 | 14,43 | 94,11 | 31,02 | 55,47 | 63,06 | 752,62 | 0 | 52 | 0,34 | sí |
| usuario_extremo | 10000 | 10051 | 10051.00 | 13,01 | 16,43 | 17,98 | 18,52 | 62,79 | 55,90 | 83,14 | 98,48 | 424,94 | 0 | 52 | 0,43 | sí |
| usuario_extremo | 25000 | 25051 | 25051.00 | 33,09 | 35,98 | 38,75 | 40,52 | 28,14 | 133,82 | 183,80 | 225,81 | 179,15 | 0 | 52 | 0,34 | sí |
| usuario_extremo | 49950 | 50001 | 50000.00 | 65,53 | 67,85 | 71,09 | 73,36 | 14,81 | 269,50 | 365,35 | 408,69 | 92,03 | 0 | 52 | 0,39 | sí |
| admin | 0 | 51 | 51.00 | 0,29 | 1,53 | 1,84 | 2,12 | 636,28 | 20,37 | 63,77 | 89,96 | 952,32 | 0 | — | — | — |
| admin | 100 | 151 | 151.00 | 0,35 | 1,62 | 2,00 | 2,64 | 597,09 | 19,30 | 24,61 | 28,50 | 1225,47 | 0 | 52 | 0,36 | sí |
| admin | 1000 | 1051 | 1051.00 | 1,16 | 2,62 | 3,21 | 3,65 | 371,93 | 21,29 | 40,20 | 57,51 | 1052,17 | 0 | 52 | 0,30 | sí |
| admin | 2500 | 2551 | 2551.00 | 2,42 | 4,85 | 6,15 | 6,91 | 200,39 | 22,81 | 32,95 | 37,98 | 1039,02 | 0 | 52 | 0,29 | sí |
| admin | 5000 | 5051 | 5051.00 | 5,29 | 9,41 | 10,69 | 11,37 | 107,02 | 29,08 | 47,81 | 54,78 | 815,45 | 0 | 52 | 0,30 | sí |
| admin | 10000 | 10051 | 10051.00 | 11,99 | 16,41 | 17,98 | 19,63 | 61,50 | 55,67 | 85,74 | 97,97 | 431,39 | 0 | 52 | 0,30 | sí |
| admin | 25000 | 25051 | 25051.00 | 30,42 | 33,77 | 36,65 | 37,92 | 30,04 | 122,84 | 174,12 | 206,58 | 196,99 | 0 | 52 | 0,39 | sí |
| admin | 55950 | 56001 | 56000.00 | 67,17 | 68,09 | 74,05 | 75,95 | 14,66 | 293,74 | 414,52 | 478,39 | 82,54 | 0 | 52 | 0,34 | sí |

En memoria de Jairo Urbina.
