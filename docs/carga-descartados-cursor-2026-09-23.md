# Carga: descartados intercalados, FORCE INDEX y paginación por cursor — 2026-09-23

Registrado por Mr. Hyde, hall9000, 2026-09-23 (~07:55–08:30 CST). Sigue a
`docs/carga-descartados-offset-2026-09-23.md` (jax-platform#157), que dejó dos
cosas abiertas: el plan inestable de `SQL_DESCARTADOS_DEL_USUARIO` con usuarios
INTERCALADOS ("inferido del mecanismo, NO medido") y la paginación por cursor
(propuesta, no implementada). Los números salen de las corridas; lo calculado
se dice.

## Veredicto

1. **El plan malo, medido con usuarios intercalados, cuesta en proporción al
   total GLOBAL de descartados.** Con 55.003 descartados de 502 usuarios
   barajados en el tiempo, `idx_pipelines_ocultos` hace leer al usuario con 3
   descartados **55.004 filas (p95 55–63 ms) en vez de 4 (p95 0,12–0,18 ms)**;
   al usuario típico (100) 28.635 filas para la primera página (p95 31–37 ms
   contra 0,36 ms), y la última página del usuario pesado (5.000) 55.004 filas
   (p95 61–63 ms contra 5 ms). No es el +1.000 de la siembra en bloques de #157:
   el plan malo recorre TODOS los descartados cada vez que el usuario no llena
   la página con los suyos.
2. **Arreglo aplicado:** `FORCE INDEX (idx_pipelines_descartados)` en la
   consulta del dueño (offset y cursor). En esta siembra el optimizador eligió
   el índice correcto sin hint las dos veces; el hint no se justifica por esta
   corrida sino por #157 (3 de 8 offsets con el plan global, mismo SQL y
   datos) y por el daño de arriba cuando ocurre.
3. **Cursor `(descartado_at, pipeline_id)` en los dos listados:** la página por
   cursor lee **52 filas en cualquier profundidad** (Handler_read) y devuelve la
   misma página que `offset` en las 30 comparaciones (20 SQL + 10 HTTP,
   dos corridas). Por HTTP, la última página del usuario pesado baja de p95
   7,7–10,3 ms a 2,1–2,3 ms a c=1; la del admin (55.003 globales) de
   **59–61 ms a 1,9–2,0 ms a c=1 y de 334–414 ms / 81–101 rps a 64–68 ms /
   908–926 rps a c=25**.
4. `offset` sigue funcionando (expandir; contraer es otra decisión). La
   interfaz manda el cursor y cae a `offset` sólo si la respuesta no lo trae
   (backend viejo durante un despliegue).

## Qué cambió en el código

- `backend/api/paginacion_descartados.py` (nuevo): cursor opaco (base64url de
  `[descartado_at, pipeline_id]`, float con ida y vuelta exacta), validación
  que falla cerrado (422 `cursor_invalido`; `cursor`+`offset` → 422
  `cursor_y_offset`), predicado y orden total
  `ORDER BY descartado_at DESC, pipeline_id DESC`.
  NULL: `descartado_at` es `DOUBLE NULL` sin restricción; MariaDB los ordena al
  final en `DESC`, y el predicado los incluye (`OR descartado_at IS NULL`, y
  rama propia cuando el cursor mismo cae en una fila sin fecha). El
  `IS NULL` extra no cambió el plan: sigue siendo range sobre el mismo índice,
  52 lecturas.
- `GET /api/pipelines?estado=discarded` y `GET /api/admin/pipelines/descartados`:
  parámetro `cursor`, respuesta con `cursor_siguiente` (null en la última
  página). `cursor` en la lista principal → 422 `cursor_solo_descartados`.
- Índices: ninguno nuevo. InnoDB agrega la PK al final de cada índice
  secundario, así que los dos existentes ya dan el orden total sin filesort
  (verificado con EXPLAIN en los tests).
- Frontend: `HistorialContenido.jsx` y `AdminPipelinesOcultos.jsx` (pestaña
  Descartados) guardan `cursor_siguiente` y "Cargar más" lo manda. Sin texto
  nuevo (i18n y tema sin cambios). La pestaña Ocultos sigue por offset.

## A. Plan por índice con descartados intercalados (SQL solo)

Arnés: `loadtest/descartados_intercalados_medir.py sql <etiqueta>`. Base
`jax_memory_test` (MariaDB 12.3.3, 127.0.0.1:3308), verificada con
`SELECT DATABASE()` antes de escribir; esquema de jax asegurado con
`descartados_seed._asegurar_esquema` (checkout de jax master `80af0c5`). Siembra:
50.000 descartados de 500 usuarios sin cuenta + usuario "pesado" (5.000) +
"liviano" (3), dueños **barajados con semilla fija** y `descartado_at`
decreciente (intercalado real), más las 16.485 filas previas de la base (0
descartadas). `ANALYZE TABLE` después de sembrar. Por variante: EXPLAIN,
`ANALYZE` (`r_rows`), Handler_read real de la sesión y p50/p95 de 50
ejecuciones. "offset_*" = SQL de master con el hint indicado; "cursor_*" = el
SQL nuevo de la aplicación para la misma página. Limpieza verificada por el
script: 0 filas sembradas y 0 usuarios restantes, `jacobs_pipelines` = 16.485
antes y después, en las 4 corridas (2 SQL + 2 HTTP).

p95 en ms; r1 / r2. Handler_read y r_rows fueron idénticos en las dos corridas.

| usuario (descartados) | offset | variante | índice | r_rows | Handler_read | p95 r1 / r2 |
|---|---|---|---|---|---|---|
| liviano (3) | 0 | sin hint | descartados | 3 | 4 | 0,12 / 0,18 |
| | | FORCE descartados | descartados | 3 | 4 | 0,16 / 0,12 |
| | | FORCE ocultos | **ocultos** | **55.003** | **55.004** | **54,98 / 63,14** |
| típico (100) | 0 | sin hint | descartados | 51 | 51 | 0,36 / 0,36 |
| | | FORCE ocultos | **ocultos** | **28.635** | **28.635** | **30,63 / 36,74** |
| | 50 (última) | FORCE descartados | descartados | 100 | 101 | 0,42 / 0,41 |
| | | FORCE ocultos | ocultos | 55.003 | 55.004 | 75,71 / 71,12 |
| | | cursor, FORCE descartados | descartados | 50 | 52 | 0,38 / 0,36 |
| | | cursor, FORCE ocultos | ocultos | 27.018 | 27.020 | 31,66 / 27,63 |
| pesado (5.000) | 0 | FORCE descartados | descartados | 51 | 51 | 0,38 / 0,35 |
| | | FORCE ocultos | ocultos | 545 | 545 | 0,76 / 0,78 |
| | 1000 | FORCE descartados | descartados | 1.051 | 1.051 | 1,29 / 1,34 |
| | | FORCE ocultos | ocultos | 11.617 | 11.617 | 11,79 / 15,79 |
| | | cursor, FORCE descartados | descartados | 51 | 52 | 0,36 / 0,44 |
| | 2500 | FORCE descartados | descartados | 2.551 | 2.551 | 2,88 / 2,93 |
| | | FORCE ocultos | ocultos | 28.016 | 28.016 | 31,06 / 27,52 |
| | | cursor, FORCE descartados | descartados | 51 | 52 | 0,47 / 0,38 |
| | 4950 (última) | sin hint | descartados | 5.000 | 5.001 | 5,74 / 4,76 |
| | | FORCE descartados | descartados | 5.000 | 5.001 | 5,28 / 5,14 |
| | | FORCE ocultos | ocultos | 55.003 | 55.004 | 63,44 / 60,56 |
| | | cursor, FORCE descartados | descartados | 50 | 52 | 0,37 / 0,36 |
| | | cursor, FORCE ocultos | ocultos | 553 | 555 | 0,78 / 0,83 |

Lectura:

- Con el índice global, el costo es ≈ (filas que el usuario necesita) ×
  (total global / descartados del usuario), con techo en el total global: el
  pesado tiene 1/11 de los descartados y paga ~11× (1.051 → 11.617); el típico
  (1/550) y el liviano (3 filas, nunca llena la página) recorren los 55.003.
- **El cursor NO arregla el plan malo por sí solo**: con `idx_pipelines_ocultos`
  forzado, la última página del usuario típico por cursor sigue leyendo 27.020
  filas. Por eso van las dos cosas juntas (FORCE + cursor): el cursor acota el
  costo por la profundidad, el FORCE por los demás usuarios.
- Sin hint el optimizador eligió `idx_pipelines_descartados` en los 16 puntos
  de estas dos corridas. La inestabilidad de #157 no se reprodujo con esta
  forma de datos; eso NO la descarta (fue con otra distribución) y el costo de
  que ocurra es el de la tabla.
- "misma página" = sí en todas las variantes (las 70 filas de las dos corridas).

## E. Carga HTTP: página profunda por offset vs cursor (código nuevo)

Arnés: `loadtest/descartados_intercalados_medir.py http <etiqueta>`, misma
siembra intercalada. Backend REAL de este checkout (`main:app`, un uvicorn,
127.0.0.1:18081; Jacobs falso en 17778), verificado en
`/proc/<pid>/environ` (`JAX_DB_NAME=jax_memory_test`) y `/proc/<pid>/cwd`
(el worktree de esta rama); JWT propio de la corrida comparado contra el de
producción. c=1 con n=200, c=25 con n=500; 0 errores y sólo 200 en todas las
tandas (el script aborta si no). El cursor de cada punto es el
`cursor_siguiente` real que devolvió la página anterior por offset; "igual" =
mismos ids y mismo `has_more` que la página por offset.

p95 en ms, rps a c=25; r1 / r2.

| serie | offset | pedido | c=1 p95 | c=25 p95 | c=25 rps | igual |
|---|---|---|---|---|---|---|
| usuario pesado (5.000) | 0 | offset | 1,85 / 2,02 | 22,2 / 23,9 | 1257 / 1196 | — |
| | 2500 | offset | 7,68 / 5,90 | 39,3 / 41,1 | 1091 / 1047 | |
| | | **cursor** | 2,13 / 2,35 | 29,4 / 32,6 | 1298 / 1237 | sí / sí |
| | 4950 (última) | offset | 7,67 / 10,28 | 48,7 / 48,1 | 801 / 807 | |
| | | **cursor** | 2,09 / 2,31 | 30,8 / 47,7 | 1270 / 1118 | sí / sí |
| admin (55.003 globales) | 0 | offset | 2,35 / 1,58 | 45,8 / 62,7 | 1126 / 931 | — |
| | 2500 | offset | 4,71 / 4,42 | 31,3 / 56,8 | 1100 / 921 | |
| | | **cursor** | 2,09 / 3,80 | 64,5 / 66,0 | 936 / 921 | sí / sí |
| | 25000 | offset | 28,01 / 31,64 | 202,6 / 158,6 | 169 / 222 | |
| | | **cursor** | 1,90 / 1,91 | 63,5 / 65,3 | 928 / 939 | sí / sí |
| | 54953 (última) | offset | 61,03 / 59,09 | 414,2 / 334,0 | 81 / 101 | |
| | | **cursor** | 1,87 / 1,99 | 64,5 / 68,2 | 926 / 908 | sí / sí |

Lectura: por cursor la latencia no depende de la profundidad (el admin da
~1,9 ms a c=1 en la primera página y en la última). A c=25 el admin por
cursor queda en el mismo p95 que su propia primera página (45–68 ms, ~920–1.130
rps): ese piso es el del endpoint de superadmin con un solo proceso uvicorn,
no de la consulta. Ruido entre corridas a c=25: saltos aislados de hasta
~17 ms (pesado cursor 4950: 30,8 contra 47,7) sin tendencia.

Recorrido completo con "ver más" (calculado, no medido): por offset
Σ(offset+51) ≈ N²/100 lecturas (55.003 → ~30 millones); por cursor 52 × N/50
(~57.000).

## Tests

- `backend/tests/test_pipelines_descartados_cursor.py`: 29 (19 puros, 10 con
  DB). **12 en rojo contra el código de master** (los otros 17 prueban el
  módulo nuevo y pasan con él aunque la API sea la vieja). Mutaciones
  verificadas en rojo: quitar `OR descartado_at IS NULL` (2 fallan: los
  recorridos cursor == offset), `pipeline_id <=` en el desempate (4 fallan:
  recorridos y cotas de Handler_read). Suite completa con DB: 2695 passed /
  2 skipped (local).
- Vitest: +3 (1011 → 1014). Los dos "con cursor_siguiente, Cargar más manda
  ESE cursor" en rojo contra master; "volver a la pestaña pide la primera
  página sin el cursor viejo" es guarda de regresión (pasa también en master).
  Los tests de offset existentes quedan como respaldo sin `cursor_siguiente`.
- Pisos de CI: pytest con DB 2640 → 2669, sin DB 1675 → 1694, vitest 1011 → 1014.

## Lo que NO se midió / quedó abierto

- La lista principal `GET /api/pipelines` (sin `estado`) y
  `/api/admin/pipelines/ocultos` siguen por offset (misma forma lineal, fuera
  de este cambio).
- El admin no lleva `FORCE INDEX`: en las cuatro corridas y en los tests eligió
  `idx_pipelines_ocultos` sin hint. No se midió si puede flipar a
  `idx_pipelines_status`.
- c ≥ 50 (el cuello ahí es el proceso único de uvicorn, #157).
- Cuántos descartados tiene hoy el usuario más cargado en producción (no se
  consultó `jax_memory`).
- Contraer `offset` (retirarlo de los dos listados) — decisión aparte.

## Reproducir

```bash
JAX_REPO_PATH=/ruta/a/checkout/de/jax \
  python3 loadtest/descartados_intercalados_medir.py sql r1
JAX_REPO_PATH=... python3 loadtest/descartados_intercalados_medir.py http r1
# tamaños: CARGA_INT_OTROS, CARGA_INT_USUARIOS, CARGA_INT_PESADO, CARGA_INT_LIVIANO
```

## Vigencia

VERDAD OPERACIONAL de 2026-09-23 sobre `jax_memory_test` en hall9000. Caduca si
cambian el esquema, los índices, el volumen o la distribución de descartados,
o la cantidad de workers de producción.

En memoria de Jairo Urbina.
