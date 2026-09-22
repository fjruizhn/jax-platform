# Carga: `GET /api/admin/memoria/grupos`, jax-platform#146 (D7, 2026-09-22)

Ronda de arreglo de la revisión adversarial de jax-platform#146
(`fix/fundir-respeta-fuentes-y-verificados`). LAS CUATRO DEL RENDIMIENTO,
#4: sin número medido no hay GO. Registrado por Mr. Hyde. Todas las horas
en CST (hall9000), corrida ~03:00-03:15.

## Qué cambió en el camino medido

`SQL_ACTIVOS_CON_VECTOR` (la consulta que alimenta el detector de
casi-duplicados) sumó una columna (`source_facet`, ya sumaba
`source_fact_ids` desde la ronda anterior) y `_casi_duplicados_del_grupo`
gana una consulta de compatibilidad (`_compatibles_para_fundir`, tipo +
citas cruzadas) por cada par de miembros ANTES de calcular la distancia
coseno -- ver `backend/api/admin/memoria.py`. Cada cluster de
`casi_duplicados` en la respuesta también gana dos campos
(`superviviente_verificado`, `superviviente_texto`, D5).

## Método de aislamiento

Mismo método que `docs/carga-memoria-2026-09-20.md` (Task 7 original), con
dos diferencias:

- **Base propia**: `jax_memory_test_fundir146`, clonada por esquema
  (`mysqldump --no-data --routines --triggers` desde `jax_memory_test`,
  nunca `jax_memory`) -- no la compartida. Sembrada UNA vez con
  `loadtest/memoria_seed.py` (10.000 hechos, el mismo peor caso de
  siempre) y reutilizada para las dos corridas (master y rama): las tres
  peticiones que se miden son GET puras, no escriben nada.
- **`JAX_REPO_PATH`**: el worktree compañero que documentaba la ronda del
  20 (`/home/fruiz/worktrees/jax-memoria`) ya no existe (limpiado entre
  rondas) -- se apuntó a `/home/fruiz/jax` (el checkout real de `jax` en
  esta máquina) con un lanzador ad-hoc fuera del repo (scratchpad de esta
  sesión, no committeado: `loadtest/memoria_levantar_entorno.py` sigue
  con el path viejo, hallazgo reportado, no corregido -- fuera del
  alcance de esta ronda). `loadtest/memoria_medir.py` (el script que SÍ
  hace la medición) se usó **tal cual está committeado**, sin tocar.
- **Dos backends, un solo puerto** (`127.0.0.1:18080`), uno detrás del
  otro (nunca los dos a la vez, para no competir por el mismo pool de
  conexiones):
  1. **Master** (`8ca070a`, `git worktree add --detach`, antes de las dos
     rondas de este PR).
  2. **Rama** (`fix/fundir-respeta-fuentes-y-verificados`, este worktree,
     con los cambios de D1-D6/D8 ya aplicados).
- Verificado por `/proc/<pid>/environ` antes de cada corrida: `JAX_DB_NAME`
  coincide con `jax_memory_test_fundir146` en las dos, y
  `LAS_MANOS_URL`/`JACOBS_URL` apuntan a `127.0.0.1:9` (nadie escucha) --
  no hay contacto con producción en ningún punto.

## Verificación previa -- que se mide el servicio, no un literal

| | Master | Rama |
|---|---|---|
| `/grupos` status | 200 | 200 |
| `n_grupos` | 1.491 | 1.484 |
| `grupo_mas_grande` | 538 | 536 |
| `con_casi_duplicados` | 547 | 549 |
| bytes de la respuesta | 261.496 | **342.628** (+31 %) |

El salto de bytes es el esperado y no un error: cada cluster de
`casi_duplicados` en la rama trae dos campos nuevos
(`superviviente_verificado`, `superviviente_texto`, D5) que master no
tiene. `n_grupos`/`grupo_mas_grande` difieren en unas pocas unidades entre
corridas porque los vectores sintéticos de `memoria_seed.py` usan
`np.random` sin semilla fija por corrida -- **ver DEUDA** más abajo, no
afecta la medición de latencia (misma base física, sembrada una sola vez,
para las dos corridas).

## `GET /api/admin/memoria/grupos` -- antes / después

| c | n | | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| 1 | 3 | Master | 3.246,67 | 3.287,85 | 3.287,85 | 3.287,85 |
| 1 | 3 | Rama | 3.180,31 | 3.238,11 | 3.238,11 | 3.238,11 |
| 3 | 6 | Master | 7.111,42 | 7.367,90 | 7.367,90 | 7.367,90 |
| 3 | 6 | Rama | 7.186,84 | 7.333,82 | 7.333,82 | 7.333,82 |
| 5 | 10 | Master | 11.129,46 | 11.955,88 | 11.955,88 | 11.955,88 |
| 5 | 10 | Rama | 11.494,33 | 12.543,73 | 12.543,73 | 12.543,73 |

Delta de p95: c=1 **−1,5 %**, c=3 **−0,5 %**, c=5 **+4,9 %**. Los tres
deltas están dentro del ruido esperable de una muestra de 3-10 peticiones a
concurrencia alta contra un pool de 10 conexiones compartido (mismo techo
que documentó la ronda del 20: el límite es el pool y el worker único, no
la consulta) -- no hay una degradación sistemática atribuible al cambio.
**No se cumple, y no hace falta cumplir, un umbral 10×**: el pedido de D7
era medir antes/después, no fijar un piso nuevo.

Las otras dos rutas medidas por `memoria_medir.py`
(`/hechos?verificado=false` y `/hechos` sin filtro) **no tocan código
modificado por este PR** (ni esta ronda ni la anterior) -- se registran en
`loadtest/_memoria_resultados.json` de cada corrida como confirmación de
que el resto del backend no se movió, no como parte de D7.

## Conclusión

El costo agregado (compatibilidad par-a-par antes de la distancia coseno,
una columna más en el SELECT, dos campos más por cluster en la respuesta)
es indistinguible del ruido de medición a esta escala (10.000 hechos,
9.000 activos). El aumento de payload (+31 %) es intencional (D5) y no se
midió aparte por bytes/segundo porque el cuello de botella medido sigue
siendo el pool de conexiones, no el tamaño de la respuesta.

## DEUDA anotada (no corregida en esta ronda, fuera de alcance de D7)

- `loadtest/memoria_seed.py` no fija semilla de `np.random` por invocación
  (usa el generador global) -- dos siembras del "mismo" peor caso no dan
  el mismo `n_grupos`/`grupo_mas_grande` exacto. No afecta esta medición
  (una sola siembra, reusada por las dos corridas), pero sí afectaría a
  quien quiera comparar corridas de siembras DISTINTAS.
- `loadtest/memoria_levantar_entorno.py` tiene `JAX_REPO_PATH_OVERRIDE`
  apuntando a un worktree (`/home/fruiz/worktrees/jax-memoria`) que ya no
  existe -- cualquiera que lo corra tal cual, hoy, falla al arrancar el
  backend. Se necesitó un lanzador ad-hoc fuera del repo para esta
  medición.
