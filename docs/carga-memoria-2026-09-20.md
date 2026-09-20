# Carga: pantalla de Memoria, peor caso a 10.000 hechos (2026-09-20)

Rama `feat/memoria-admin` (jax-platform), Task 7 del plan
`docs/superpowers/plans/2026-09-20-memoria-admin.md`. LAS CUATRO DEL
RENDIMIENTO, #4: sin número medido no hay GO. Registrado por Mr. Hyde.
Todas las horas en CST (hall9000), corrida ~10:30-10:45. Los números salen
de las corridas; ninguno es estimado.

## Método de aislamiento

- **Base propia, NUNCA la compartida.** `jax_memory_test_carga_memoria`,
  clonada por esquema (`mysqldump --no-data --routines --triggers` desde
  `jax_memory_test`, nunca `jax_memory`) — no `jax_memory_test` a secas: esa
  base la usan en paralelo otros worktrees corriendo su propia suite, y
  dejarle 10.000 facts sembrados de forma permanente le habría roto
  cualquier test que cuente filas. El grant de `jax_user` cubre el patrón
  `jax\_memory\_test\_%`, así que el nombre calificó sin pedir un grant
  nuevo (verificado con `SHOW GRANTS`).
- El clon trae el esquema YA migrado (Task 1/2 del plan: `verified_by`,
  `superseded_by_user`, `idx_facts_revision` — verificado con `SHOW COLUMNS`
  / `SHOW INDEX` antes de sembrar).
- Backend del worktree, un solo proceso uvicorn, en `127.0.0.1:18080`
  (`loadtest/memoria_levantar_entorno.py`). `JAX_REPO_PATH` apunta al
  worktree compañero `/home/fruiz/worktrees/jax-memoria` (Task 1/2 del plan
  viven ahí; **no** se tocó ese worktree, sólo se leyó como dependencia de
  import, igual que `JAX_REPO_PATH` en cualquier despliegue).
  `LAS_MANOS_URL`/`JACOBS_URL` apuntan a `127.0.0.1:9` (nadie escucha):
  producción (`:7777`/`:8080`) sin tocar, y esta pantalla no los necesita.
  `JAX_OLLAMA_URL` sí apunta al Ollama real (`localhost:11434`) para que
  "corregir" funcione de verdad si Fernando lo prueba desde el navegador —
  es una llamada de lectura al servicio de inferencia, no una escritura a
  producción.
- **Verificado por `/proc/<pid>/environ` ANTES de sembrar y de cargar**
  (pid 1559963):

  ```
  JAX_DB_NAME=jax_memory_test_carga_memoria
  LAS_MANOS_URL=http://127.0.0.1:9
  JACOBS_URL=http://127.0.0.1:9/jacobs
  JAX_REPO_PATH=/home/fruiz/worktrees/jax-memoria
  JAX_OLLAMA_URL=http://localhost:11434
  ```

- Superadmin sembrado por el propio arranque del backend
  (`db/seed.run_seed()`, vía `JAX_SEED_ADMIN_PASSWORD`): `tenant_id=1`,
  `user_id=1`, `role=superadmin`. Verificado con `SELECT` de sólo lectura
  contra la base nueva.
- Cliente `httpx.AsyncClient` real (HTTP de punta a punta, nunca
  `ASGITransport`), JWT firmado con el mismo `JAX_JWT_SECRET` de
  `/etc/jax/.env` para `user_id=1`.
- Herramientas commiteadas: `loadtest/memoria_seed.py` (siembra),
  `loadtest/memoria_levantar_entorno.py` (arranca el backend, **no lo
  mata** — a propósito, ver Parte D del encargo), `loadtest/memoria_medir.py`
  (mide contra el backend ya arriba).

## 1. El peor caso sembrado (`loadtest/memoria_seed.py`)

10.000 hechos, no los 116 de hoy:

| | |
|---|---|
| Total | 10.000 |
| No verificados (cola de revisión) | 9.000 (90 %, mismo sesgo que hoy: "115 de 116 esperan revisión") |
| Verificados | 1.000 |
| Superados (`superseded_by` a otro hecho) | 500 |
| Vencidos (pasado) | 500 |
| Con vencimiento futuro (aún vigentes) | 200 |
| Usuarios | 1 usuario "pesado" con 30 % de los hechos + 39 más |
| Clusters temáticos sembrados | 510 (1 mega-grupo de 600 + 20 cerca del tope de 90 para casi-duplicados + cola larga de 2-30) |

**Embeddings sintéticos pero coherentes** (`JAX_OLLAMA_URL` es inválido a
propósito en los tests de esta casa — bge-m3 real no está disponible ahí; en
este backend persistente sí lo está, pero sembrar 10.000 vectores reales vía
HTTP habría sido la parte más lenta de la siembra por lejos). Cada hecho
activo es `normalize(centroide·√(1-t) + azar·√t)` en la esfera unidad de
1024 dimensiones — la misma construcción que ya usa
`tests/test_memoria_grupos.py` para sus 3 casi-duplicados reales, generalizada
a escala. Verificado a mano antes de sembrar (5 muestras):

| | distancia coseno |
|---|---|
| centroide → miembro "tight" (t=0.01-0.04, casi-duplicado) | 0,008 – 0,018 |
| centroide → miembro "loose" (t=0.05-0.20, mismo tema) | 0,04 – 0,10 |
| tight ↔ tight (par) | 0,025 |
| loose ↔ loose (par) | 0,141 |
| cluster ↔ cluster (centroides al azar) | 1,008 |

Todo por debajo de `CORRECTION_DISTANCE_THRESHOLD=0,25` (mismo tema) o de
`DUP_DISTANCE_THRESHOLD=0,05` (casi-duplicado exacto) queda donde tiene que
quedar, y el salto a ~1,0 entre clusters separa "mismo tema" de "temas
distintos" con margen de sobra. Confirmado contra el endpoint real: el grupo
más grande detectado por `agrupar_por_tema()` trae 541 de los 600 sembrados
(59 quedaron marcados vencidos/superados por el overlay y salen de la
consulta, correcto) y 529 de los 1.419 grupos resultantes traen
`casi_duplicados` no vacío.

Siembra: 10.000 filas insertadas en 4,3 s (lotes de 250, `executemany`).

## 2. Verificación previa — que se mide el servicio, no un literal

Antecedente de esta casa (2026-09-16): un p95 de 2,99 ms resultó ser FastAPI
devolviendo un literal. Antes de medir, las tres respuestas se comprobaron
completas:

| Endpoint | status | bytes | filas devueltas | `total` / grupos |
|---|---|---|---|---|
| `GET /hechos?verificado=false&limite=500` | 200 | 159.384 | 500, **todas** `verificado=false` | total=8.000 |
| `GET /hechos?limite=500` (sin filtro) | 200 | 160.035 | 500 | total=9.000 |
| `GET /grupos` | 200 | 258.518-261.358 | 1.419-1.429 grupos, el mayor con 541 hechos | 529-… con `casi_duplicados` |

Las tres devuelven datos reales a escala del peor caso, no una lista vacía
ni un 4xx rápido.

## 3. `GET /api/admin/memoria/hechos?verificado=false&limite=500` — cola de revisión, usa `idx_facts_revision`

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 100,27 | 9,78 | 10,97 | 18,24 | 19,48 |
| 25 | 1000 | 1000 | 0 | 141,38 | 159,63 | 278,94 | 333,12 | 409,29 |
| 50 | 1000 | 1000 | 0 | 141,62 | 263,26 | 858,48 | 1.115,41 | 1.694,36 |
| 100 | 1000 | 1000 | 0 | 140,61 | 470,34 | 1.853,20 | 2.473,93 | 3.774,18 |

## 4. `GET /api/admin/memoria/hechos?limite=500` — SIN filtrar, el que decide el índice

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 66,23 | 14,65 | 18,02 | 19,33 | 26,75 |
| 25 | 1000 | 1000 | 0 | 132,19 | 181,12 | 281,06 | 329,91 | 420,84 |
| 50 | 1000 | 1000 | 0 | 136,87 | 324,75 | 718,51 | 1.019,87 | 1.485,82 |
| 100 | 1000 | 1000 | 0 | 135,74 | 622,13 | 1.624,84 | 2.319,62 | 3.167,79 |

Lectura: a c=1 el camino sin filtrar es ~50 % más lento que el filtrado
(14,65 ms vs 9,78 ms) — el costo del filesort se ve, pero es chico. A
concurrencia alta, los dos caminos convergen (incluso el sin filtrar queda
por *debajo* del filtrado a c=50/100): el techo de los dos es el mismo —
~135-141 rps con **un solo worker uvicorn y un pool de 10 conexiones**
(`db/connection.py`), no la consulta. La degradación real empieza en
c=25 en los dos casos por igual (el salto de rps de c=1 a c=25 ya satura;
c=50 y c=100 no suben el rps, sólo la cola).

## 5. `GET /api/admin/memoria/grupos` — agrupamiento por tema

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 3 | 0 | 0,14 | 7.169,69 | 7.202,67 | 7.202,67 | 7.202,67 |
| 3 | 6 | 6 | 0 | 0,16 | 17.180,60 | 19.515,38 | 19.515,38 | 19.515,38 |
| 5 | 10 | 10 | 0 | 0,15 | 32.708,76 | 34.718,50 | 34.718,50 | 34.718,50 |

Niveles de concurrencia chicos **a propósito**: una sola llamada ya tarda
~7,2 s a 9.000 hechos activos. A c=3 el p50 por-request ya se **más que
duplica** (17,2 s) y a c=5 se **más que cuadruplica** (32,7 s) — no hay
escalón limpio de "empieza a degradar", degrada desde la primera
concurrencia mayor a 1. Con `_CONCURRENCIA_VECINOS=6` por request contra un
pool de 10 conexiones compartido por TODO el backend, dos requests
simultáneas ya piden hasta 12 conexiones sobre un pool de 10: se pisan.

## 6. `EXPLAIN` a volumen (10.000 filas, ~6.668-9.000 activas), no sobre la tabla vacía

```
SQL_LISTAR, ARGS_EJEMPLO (verificado=false, limite=20):
  type=range key=idx_facts_revision rows=6668 Extra=Using where
  -- usa el índice, SIN filesort, SIN temporary.

SQL_LISTAR, sin filtro (verificado=NULL, limite=500):
  type=range key=idx_facts_active rows=6668
  Extra=Using index condition; Using where; Using filesort
  -- NO usa idx_facts_revision (is_verified deja de filtrar): cae al
  -- índice de superseded_by y ordena en memoria.

SQL_CONTAR, verificado=false:
  type=ALL key=NULL rows=6668 Extra=Using where
  -- full scan, sin importar el filtro (ver hallazgo más abajo).

SQL_CONTAR, sin filtro:
  type=ALL key=NULL rows=6668 Extra=Using where
  -- idéntico: el COUNT(*) escanea la tabla completa siempre.

SQL_ACTIVOS_CON_VECTOR (semilla de /grupos):
  type=ALL key=NULL rows=6668 Extra=Using where
  -- full scan también: trae TODOS los activos con su vector antes de
  -- buscarles vecinos uno por uno.

SQL_VECINOS, ARGS_VECINOS_EJEMPLO (K=8, consulta real por-hecho):
  type=index key=idx_embedding_bge_m3 rows=8 Extra=Using where
  -- SÍ usa el índice HNSW, por-consulta. El costo de /grupos no está en
  -- esta consulta individual: está en correrla ~9.000 veces.
```

## 7. Conclusión sobre el índice — la pregunta que abría el encargo

**No hace falta un índice nuevo para el camino sin filtrar, con la evidencia
de hoy (10.000 filas).** El filesort existe (confirmado por `EXPLAIN`), pero
a este volumen MariaDB lo resuelve con un `ORDER BY ... LIMIT` acotado
(no un sort completo de las ~9.000 filas candidatas) y el costo medido es
chico: ~5 ms de diferencia a c=1 (14,65 ms vs 9,78 ms) y, a concurrencia
alta, el camino sin filtrar **no es más lento** que el que usa
`idx_facts_revision` — los dos convergen al mismo techo (~135-141 rps), que
es el pool de conexiones y el worker único, no el plan de la consulta. Es
una VERDAD OPERACIONAL medida a 10.000 filas, no una proyección: si el
volumen de `facts` crece un orden de magnitud más (100.000+), el filesort
deja de caber en el buffer de ordenamiento y el costo per-query deja de ser
plano — ahí sí habría que remedir antes de descartar el índice de nuevo.

**Dos hallazgos que SÍ importan y quedan fuera del alcance de esta tarea**
(Task 7 es medir, no arreglar — se reportan, no se tocan):

1. **`SQL_CONTAR` hace `type=ALL` (full scan) siempre**, con o sin filtro,
   sobre una tabla que incluye una columna `VECTOR(1024)` de ~4 KB por fila.
   A 10.000 filas el costo total (LISTAR+CONTAR) sigue en milisegundos
   simples; a un volumen mayor este `COUNT(*)` sin cobertura de índice es el
   próximo candidato a doler, no el `ORDER BY`.
2. **`GET /grupos` no escala con la concurrencia, y tampoco escala bien con
   el volumen por sí sola:** hace 1 (`SQL_ACTIVOS_CON_VECTOR`, full scan) +
   N (`SQL_VECINOS`, una por hecho activo — 9.000 a este volumen) consultas
   por request. Cada `SQL_VECINOS` individual usa el índice HNSW
   correctamente (confirmado por `EXPLAIN`, rows=8), pero el patrón de
   "una consulta por fila" es lo que explica los ~7,2 s de una sola llamada
   y el colapso a partir de 3 llamadas simultáneas (el semáforo interno de 6
   contra un pool de 10 conexiones COMPARTIDO con el resto del backend). A
   116 hechos (la escala de hoy en producción) esto es invisible (~15
   consultas). A 9.000 activos, no.

## VERDAD OPERACIONAL pendiente

- Estos números son de un backend con un solo worker uvicorn, pool de 10
  conexiones — la misma topología de `jax-platform.service`. Si cambia el
  número de workers o el tamaño del pool en despliegue, hay que remedir.
- El hallazgo de `/grupos` es de volumen (9.000 activos), no sólo de
  concurrencia: aunque nadie la llamara dos veces a la vez, una sola carga
  de página ya tarda ~7 s a este volumen. Vale la pena que quede escrito acá
  aunque el arreglo (por ejemplo, traer los vecinos con una sola consulta
  set-based en vez de N) no sea parte de esta tarea.
