# Carga: `GET /api/admin/memoria/grupos`, jax-platform#146 (D7, 2026-09-22)

Ronda de arreglo de la revisión adversarial de jax-platform#146
(`fix/fundir-respeta-fuentes-y-verificados`). LAS CUATRO DEL RENDIMIENTO,
#4: sin número medido no hay GO. Registrado por Mr. Hyde. Todas las horas
en CST (hall9000).

> **CORRECCIÓN (revisión adversarial de jax-platform PR 146, tercera
> vuelta, MAJOR 3).** La primera versión de este documento (corrida
> ~03:00-03:15) atribuía la diferencia de `n_grupos` entre master y rama a
> que `loadtest/memoria_seed.py` no fijaba semilla de `np.random` -- **es
> falso**: `memoria_seed.py:170` fija `np.random.default_rng(20260920)`,
> semilla constante. Esa medición además usó `/home/fruiz/jax` a mano como
> `JAX_REPO_PATH`, algo que el propio lanzador PROHÍBE por comentario
> (`loadtest/memoria_levantar_entorno.py:50`, antes de esta corrección). Se
> descarta esa corrida entera y se remide abajo, con el lanzador arreglado
> y sin tocar `/home/fruiz/jax` ni `/srv/jax-prod`. El error queda escrito
> acá, no borrado -- para que quien vuelva a este documento sepa que esa
> primera lectura estaba mal, y por qué.

## Qué cambió en el camino medido

`SQL_ACTIVOS_CON_VECTOR` (la consulta que alimenta el detector de
casi-duplicados) sumó una columna (`source_facet`) y perdió otra
(`source_fact_ids`, que ahora se resuelve aparte -- ver `SQL_CITAS` más
abajo). `_casi_duplicados_del_grupo` gana una consulta de compatibilidad
(`_compatibles_para_fundir`, tipo + cierre transitivo de citas) por cada
par de miembros ANTES de calcular la distancia coseno, y una revalidación
final por componente (MAJOR 1b) -- ver `backend/api/admin/memoria.py`.
Cada cluster de `casi_duplicados` en la respuesta también gana dos campos
(`superviviente_verificado`, `superviviente_texto`, D5), y
`agrupar_por_tema()` suma una consulta nueva por request: `SQL_CITAS`
(`SELECT id, source_fact_ids FROM facts WHERE source_fact_ids IS NOT
NULL`), full scan sin índice útil (ver
`test_sql_citas_no_tiene_indice_util_pero_el_costo_es_chico`,
`backend/tests/test_memoria_grupos.py`) para construir el cierre
transitivo de citas UNA vez por request.

## Método de aislamiento

Mismo método que `docs/carga-memoria-2026-09-20.md` (Task 7 original):

- **Base propia**: `jax_memory_test_fundir146c`, clonada por esquema
  (`mysqldump --no-data --routines --triggers` desde `jax_memory_test`,
  nunca `jax_memory`) -- no la compartida. Sembrada UNA vez con
  `loadtest/memoria_seed.py` (10.000 hechos, semilla fija
  `np.random.default_rng(20260920)`) y reutilizada para las dos corridas
  (master y rama): las tres peticiones que se miden son GET puras, no
  escriben nada.
- **`JAX_REPO_PATH` -- arreglado (M5).** `loadtest/memoria_levantar_
  entorno.py` clona `jax` a un checkout PROPIO, dentro de su propio
  `RUN_DIR` (`_asegurar_checkout_de_jax`, nuevo en esta ronda) -- nunca
  `/home/fruiz/jax` ni `/srv/jax-prod/jax` (el comentario en la línea 50
  del archivo lo prohibía desde antes; la corrida anterior de este
  documento lo violó y quedó descartada, ver el aviso de arriba). El
  comando es EXACTAMENTE el que corre cualquiera que repita esto -- no hubo
  lanzador ad-hoc fuera del repo en esta vuelta.
- **Dos backends, un solo puerto** (`127.0.0.1:18080`), uno detrás del
  otro (nunca los dos a la vez, para no competir por el mismo pool de
  conexiones):
  1. **Master** (`8ca070a`, `git worktree add --detach /tmp/jax-platform-
     master-146c 8ca070a`, el estado de origin/master antes de las tres
     rondas de este PR). Como esa revisión todavía tiene el
     `JAX_REPO_PATH_OVERRIDE` viejo (roto), se copió el
     `memoria_levantar_entorno.py` YA CORREGIDO de la rama a ese worktree
     detached antes de arrancar -- el worktree es descartable (no es una
     rama real, no se commitea nada ahí) y esto no toca origin/master.
  2. **Rama** (`fix/fundir-respeta-fuentes-y-verificados`, este worktree,
     con los cambios de D1-D8/M1-M6 ya aplicados, sin commitear todavía en
     el momento de medir).
- Verificado por `/proc/<pid>/environ` antes de cada corrida (lo hace el
  propio lanzador, `_verificar_no_apunta_a_produccion`): `JAX_DB_NAME`
  coincide con `jax_memory_test_fundir146c` en las dos, y
  `LAS_MANOS_URL`/`JACOBS_URL` apuntan a `127.0.0.1:9` (nadie escucha) --
  no hay contacto con producción en ningún punto.

### Comando exacto (reproducible)

```bash
# 1. Clonar el esquema (una vez):
mysqldump -h 127.0.0.1 -P 3308 -u"$JAX_DB_USER" --no-data --routines --triggers \
  jax_memory_test | mysql -h 127.0.0.1 -P 3308 -u"$JAX_DB_USER" jax_memory_test_fundir146c

# 2. Por cada revisión a medir (master o rama), desde SU checkout:
python3 loadtest/memoria_levantar_entorno.py jax_memory_test_fundir146c '<password>'

# 3. Sembrar (una sola vez, reusado por las dos corridas):
python3 loadtest/memoria_seed.py jax_memory_test_fundir146c /tmp/seed_result.json

# 4. Medir:
python3 loadtest/memoria_medir.py jax_memory_test_fundir146c http://127.0.0.1:18080
```

**Dónde queda el JSON de resultados.** `loadtest/memoria_medir.py` escribe
`loadtest/_memoria_resultados.json` (dentro del checkout desde el que
corre) -- no está en `.gitignore`, pero por convención de este repo **no
se commitea** (es un output de medición, no código ni doc): las dos
corridas de esta vuelta se copiaron al scratchpad de la sesión
(`_resultados_master146c.json`/`_resultados_branch146c.json`) y sus
números quedan transcritos abajo; el JSON crudo no viaja con el PR, igual
que no viajó en la ronda del 20 (`docs/carga-memoria-2026-09-20.md` tampoco
adjunta el suyo).

## Verificación previa -- que se mide el servicio, no un literal

| | Master (`8ca070a`) | Rama |
|---|---|---|
| `/grupos` status | 200 | 200 |
| `n_grupos` | 1.418 | 1.400 |
| `grupo_mas_grande` | 541 | 541 |
| `con_casi_duplicados` | 534 | 534 |
| bytes de la respuesta | 254.202 | **331.313** (+30,3 %) |

El salto de bytes es el esperado y no un error: cada cluster de
`casi_duplicados` en la rama trae dos campos nuevos
(`superviviente_verificado`, `superviviente_texto`, D5) que master no
tiene.

**La diferencia de `n_grupos` (1.418 vs 1.400) NO es un efecto del código
de esta ronda.** Es la MISMA causa que ya documentó
`docs/carga-memoria-grupos-n1-2026-09-20.md` §6: la búsqueda de vecinos
usa el índice HNSW, que es **aproximado** -- dos corridas seguidas, con el
mismo código y los mismos datos, ya daban números de grupo distintos antes
de esta ronda (`docs/carga-memoria-grupos-n1-2026-09-20.md` midió 1.596 a
1.606 en el mismo día, sobre el mismo código). `grupo_mas_grande` y
`con_casi_duplicados` SÍ coinciden exactos entre las dos corridas de esta
vuelta (541 y 534 en las dos) -- consistente con que la variación es ruido
del índice aproximado en los bordes, no una regresión sistemática de la
rama.

## `GET /api/admin/memoria/grupos` -- antes / después

| c | n | | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|
| 1 | 3 | Master | 3.218,86 | 3.244,32 | 3.244,32 | 3.244,32 |
| 1 | 3 | Rama | 3.264,02 | 3.279,93 | 3.279,93 | 3.279,93 |
| 3 | 6 | Master | 7.222,48 | 7.476,31 | 7.476,31 | 7.476,31 |
| 3 | 6 | Rama | 6.279,96 | 7.413,48 | 7.413,48 | 7.413,48 |
| 5 | 10 | Master | 11.562,25 | 12.518,14 | 12.518,14 | 12.518,14 |
| 5 | 10 | Rama | 11.185,52 | 12.873,28 | 12.873,28 | 12.873,28 |

Delta de p95: c=1 **+1,1 %**, c=3 **−0,8 %**, c=5 **+2,8 %**. Los tres
deltas están dentro del ruido esperable de una muestra de 3-10 peticiones a
concurrencia alta contra un pool de 10 conexiones compartido (mismo techo
que documentó la ronda del 20: el límite es el pool y el worker único, no
la consulta) -- no hay una degradación sistemática atribuible al cambio.
El costo nuevo (`SQL_CITAS`, full scan sin índice, más la revalidación
pairwise por componente) no se distingue del ruido de medición a esta
escala. **No se cumple, y no hace falta cumplir, un umbral 10×**: el
pedido de D7 era medir antes/después, no fijar un piso nuevo.

Las otras dos rutas medidas por `memoria_medir.py`
(`/hechos?verificado=false` y `/hechos` sin filtro) **no tocan código
modificado por este PR** (ninguna de las tres rondas) -- se registran en
`loadtest/_memoria_resultados.json` de cada corrida como confirmación de
que el resto del backend no se movió, no como parte de D7. Los números
(bytes idénticos, p95 dentro del mismo rango que master) están en los JSON
copiados al scratchpad de la sesión, no se transcriben acá por no ser el
objeto de esta medición.

## Conclusión

El costo agregado (`SQL_CITAS` de más por request, compatibilidad par a
par con cierre transitivo antes de la distancia coseno, revalidación final
por componente, una columna más y una menos en el SELECT, dos campos más
por cluster en la respuesta) es indistinguible del ruido de medición a
esta escala (10.000 hechos, 9.000 activos). El aumento de payload (+30,3 %)
es intencional (D5) y no se midió aparte por bytes/segundo porque el
cuello de botella medido sigue siendo el pool de conexiones, no el tamaño
de la respuesta.

> **⚠️ CORRECCIÓN (revisión adversarial de jax-platform PR 146, RONDA 4,
> MAJOR A, 2026-09-22 tarde).** Todo lo de arriba (§"Qué cambió en el
> camino medido" en adelante) medía una base de carga **sin una sola fila
> con `source_fact_ids`** -- `loadtest/memoria_seed.py` nunca poblaba ese
> campo, así que `SQL_CITAS` devolvía **0 filas** en las dos corridas.
> "Medido... son pocas" (comentario junto a `SQL_CITAS`,
> `backend/api/admin/memoria.py`) y "no se distingue del ruido" (más
> arriba) eran, con esa base, **verdades vacías**: no había nada que
> distinguir del ruido porque no había carga real que medir. Corregido acá,
> no borrado -- para que quien vuelva a este documento sepa qué pasó y por
> qué la sección de arriba ya no alcanza.
>
> **Qué se sembró de nuevo.** La base de test de esta sesión estaba vacía
> (0 `facts`, medido con una consulta ad-hoc **vía pytest**, nunca contra
> `jax_memory`) -- sin señal organica que dar una proporción real, se usó
> el piso del brief: **10 % del total en cita plana** (1.000 de 10.000,
> cada una citando a OTRO hecho al azar) **+ 200 cadenas de 2do/3er orden**
> (base → S2 cita a base → S3 cita a S2, 400 filas más) -- el peor caso que
> pedía el brief, para ejercitar el BFS de `_cierre_transitivo_de_citas` a
> más de un salto. Total: **1.400 de 10.000 facts (14 %) con
> `source_fact_ids` no nulo** en la base "con síntesis". Ver
> `loadtest/memoria_seed.py::FRACCION_SINTESIS`/`N_CADENAS_SINTESIS`
> (overridables por entorno -- `MEMORIA_SEED_FRACCION_SINTESIS=0` y
> `MEMORIA_SEED_N_CADENAS_SINTESIS=0` reproducen la base VIEJA, sin
> síntesis, para la comparación).
>
> **Método.** Dos bases propias, clonadas por esquema desde
> `jax_memory_test` (nunca `jax_memory`, nunca la compartida):
> `jax_memory_test_fundir146_r4a` (SIN síntesis) y
> `jax_memory_test_fundir146_r4b` (CON síntesis, 14 %) -- las dos con
> 10.000 `facts`, mismo generador, misma semilla de `numpy`
> (`np.random.default_rng(20260920)`), un solo backend detrás de la otra
> contra el MISMO puerto (`127.0.0.1:18080`), commit de `jax` resuelto y
> registrado en `info.json` (MINOR 4, esta misma ronda):
> `52e2599e19b5a8fcc7728a23a51b071731d5a0c5`. Rama medida: HEAD de este PR
> (`cbe19ef` + los arreglos de esta ronda, sin commitear todavía en el
> momento de medir). Verificado por `/proc/<pid>/environ` antes de cada
> corrida, igual que la vuelta anterior. Las dos bases y el backend se
> borraron al terminar de medir (`DROP DATABASE`, vía un script propio que
> reusa el mismo patrón de credenciales que ya usan
> `loadtest/memoria_seed.py`/`memoria_levantar_entorno.py` -- `sudo -n cat
> /etc/jax/.env` DENTRO de Python, nunca sourceado en la terminal).
>
> **`GET /api/admin/memoria/grupos` -- con y sin síntesis:**
>
> | c | n | | p50 ms | p95 ms |
> |---|---|---|---|---|
> | 1 | 3 | Sin síntesis | 3.162,63 | 3.205,37 |
> | 1 | 3 | Con síntesis (14 %) | 3.021,47 | 3.130,33 |
> | 3 | 6 | Sin síntesis | 6.527,46 | 7.222,09 |
> | 3 | 6 | Con síntesis (14 %) | 6.643,75 | 7.011,31 |
> | 5 | 10 | Sin síntesis | 10.692,80 | 12.027,09 |
> | 5 | 10 | Con síntesis (14 %) | 11.252,88 | 11.880,87 |
>
> Delta de p95 con síntesis vs. sin: c=1 **−2,3 %**, c=3 **−2,9 %**, c=5
> **−1,2 %** -- las tres NEGATIVAS (más rápido con síntesis, no más lento),
> dentro del ruido de una muestra de 3-10 peticiones contra un pool de 10
> conexiones. **Ahora sí hay 1.400 filas con `source_fact_ids` que
> `SQL_CITAS` recorre en cada request** (antes, 0) y el efecto sigue sin
> distinguirse del ruido -- esta vez con una base que de verdad ejercita el
> camino que se quería medir.
>
> **`POST /api/admin/memoria/hechos/fundir` -- con y sin síntesis (nuevo en
> esta ronda; la vuelta anterior no lo medía).** 200 llamadas SECUENCIALES
> (no concurrentes -- cada fundir muta filas y necesita un cluster propio
> sin fundir todavía; 512-782 clusters disponibles según la base, de sobra
> para 200), una por cada cluster de `casi_duplicados` que devolvía
> `GET /grupos` en ese momento, con el `superviviente_id`/`absorbidos`
> exactos que el propio backend proponía:
>
> | | Sin síntesis | Con síntesis (14 %) |
> |---|---|---|
> | ok / intentados | 200 / 200 | 200 / 200 |
> | p50 ms | 7,60 | 12,20 |
> | p95 ms | 18,43 | 19,12 |
> | max ms | 36,92 | 35,35 |
>
> p50 sube 60,5 % (7,6 → 12,2 ms) -- el `SQL_CITAS` de más (`fundir_hechos`
> lo corre UNA vez por request, dentro de la transacción con `FOR UPDATE`,
> antes de decidir compatibilidad) pesa proporcionalmente más sobre un
> endpoint que YA era rápido. p95 sube apenas 3,8 % (18,43 → 19,12 ms). En
> términos absolutos, los dos casos siguen en el orden de **milisegundos
> de un solo dígito a low-teens** -- muy por debajo de cualquier percepción
> humana de demora en un botón que Fernando aprieta a mano, no en un lazo
> automatizado.
>
> **Decisión (punto 3 del brief: "si el costo es material, filtrar
> SQL_CITAS por source_facet, o acotar el cierre a los ids candidatos --
> elige con número").** Con los números de arriba, el costo **NO es
> material**: `/grupos` no muestra degradación medible ni con 14 % de
> síntesis y cadenas de 2do/3er orden, y `/fundir` sigue en milisegundos de
> un dígito a low-teens con el mismo escenario. No se optimiza `SQL_CITAS`
> esta ronda -- añadir un índice sobre `source_facet` (que ni siquiera está
> en la consulta hoy) o acotar el cierre a los ids candidatos sería
> complejidad sin beneficio medido, exactamente lo que LAS CUATRO DEL
> RENDIMIENTO #2 (cache) pide evitar: no se cachea/optimiza lo que no se
> midió caro. Si la proporción real de síntesis en producción creciera muy
> por encima del 14 % medido acá, esta sección es la que hay que volver a
> correr -- no una intuición nueva.
>
> **Qué NO se remidió en esta corrección.** `/hechos?verificado=false` y
> `/hechos` sin filtro (no tocados por este PR, igual que la vuelta
> anterior) -- se corrieron igual como parte del script (`memoria_medir.py`
> mide las tres rutas siempre), pero no se transcriben acá por no ser el
> objeto de esta medición; quedaron en los JSON de la sesión (borrados al
> cerrar, igual que las bases).
