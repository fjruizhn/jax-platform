# Carga: SQL_PIPELINES_DEL_USUARIO — plan con IGNORE INDEX vs plan natural (2026-09-22)

Rama `feat/descartar-pipelines` (jax-platform). Task 4 de la spec
descartar-pipelines, fix round 2, Ruling 16 del controlador. Registrado por
Mr. Hyde. Todas las corridas contra la base de TEST de esta sesión
(`jax_memory_test_<sufijo>`, ver `backend/base_de_test.py`), **nunca**
contra `jax_memory`.

## Por qué se mide esto

El fix round 1 de esta misma tarea (Ruling 13(e)) había aceptado que
`SQL_PIPELINES_DEL_USUARIO` (la lista principal de `GET /api/pipelines`)
usara un plan con `Using filesort` cuando el optimizador lo elegía, con la
justificación de que era más rápido y de que `MAX_PIPELINES` acotaba el
histórico. Las dos partes de esa aceptación estaban mal:

- **`MAX_PIPELINES` acota los pipelines CONCURRENTES, no el histórico.**
  `status NOT IN ('discarded','hidden')` incluye TODO lo terminado
  (`completed`/`failed`/`aborted`/`expired`), que no tiene techo -- crece
  con cada pipeline que el usuario corre a lo largo del tiempo.
- **La medición publicada (363 filas) no probaba nada.** 1,6x de diferencia
  a escala de décimas de milisegundo es ruido de medición, no una señal.

La decisión (Ruling 16): `SQL_PIPELINES_DEL_USUARIO` lleva
`IGNORE INDEX (idx_pipelines_descartados, idx_pipelines_ocultos)` -- el
plan vuelve a ser DETERMINISTA (range scan en orden por
`idx_jacobs_pipelines_duenio`, sin filesort, que corta en el `LIMIT`) sin
depender de las estadísticas de la tabla en un momento dado. Este documento
mide las DOS formas de dato que pidió el controlador para esa decisión.

## Método

- **Dónde vive la medición:** `backend/tests/test_carga_indice_pipelines_del_usuario.py`.
  Divergencia deliberada respecto de `loadtest/*.py` (los demás scripts de
  carga de este repo): esos son procesos standalone con su propia conexión
  a MariaDB, correcto para ellos porque miden HTTP de punta a punta con un
  backend levantado aparte. La regla de esta sesión es más estricta ("DB
  tests ONLY through pytest ... Never query MariaDB outside pytest"), y
  esta medición es SQL puro (`EXPLAIN` + tiempo de
  `cursor.execute`/`fetchall`), así que corre como test de pytest, con el
  mismo aislamiento (`base_de_test`) que el resto de la suite.
- **Comando exacto** (se salta por default -- siembra miles de filas, no es
  parte del piso de CI normal):

  ```
  JAX_REPO_PATH=<checkout de jax en master> JAX_MEDIR_INDICE_PIPELINES=1 \
    backend/.venv/bin/python -m pytest -q -s \
    backend/tests/test_carga_indice_pipelines_del_usuario.py
  ```

- **Qué se compara:** el texto EXACTO de `SQL_PIPELINES_DEL_USUARIO` tal
  como queda en `api/pipelines.py` tras Ruling 16 (`IGNORE INDEX`) contra
  el mismo texto SIN esa cláusula (`SQL_NATURAL`, copia literal de cómo
  estaba antes de este fix, congelada en el archivo de test -- si el SQL de
  producción cambia después, esta comparación sigue midiendo lo que dice
  comparar).
- **Muestras:** 15 ejecuciones por variante y forma (`n=15`, ≥10 que pidió
  el controlador), mismo `cursor` reusado, sin reconexión entre muestras.
  `ANALYZE TABLE jacobs_pipelines` corre una vez, después de sembrar cada
  forma y antes de medirla -- sin esto el plan puede depender de qué otros
  tests corrieron antes en la sesión (estadísticas persistentes de InnoDB).
- **Las DOS formas del controlador**, cada una en su propio tenant (para no
  mezclar datos entre formas), filas insertadas con `cur.executemany` (no
  una por una -- 5000 INSERT individuales tardan minutos):
  - **(a) "historial largo":** 5000 pipelines `completed` + 50 `discarded`.
  - **(b) "muchos descartados":** 60 `discarded` + 3 `completed` -- la
    forma que ya había mostrado `Using filesort` en la ronda anterior.
- **Repetido 3 veces** (corridas separadas del comando de arriba, no 3
  sembrados en la misma corrida) para confirmar que el plan y el orden de
  magnitud de los tiempos son estables, no una casualidad de una corrida.

## Resultados

### (a) Historial largo — 5000 terminados + 50 descartados

| Corrida | EXPLAIN natural | EXPLAIN IGNORE INDEX | natural p50 / p95 (ms) | IGNORE p50 / p95 (ms) |
|---|---|---|---|---|
| 1 | `idx_jacobs_pipelines_duenio`, range, sin filesort | `idx_jacobs_pipelines_duenio`, range, sin filesort | 0,3406 / 0,3766 | 0,3220 / 0,3329 |
| 2 | `idx_jacobs_pipelines_duenio`, range, sin filesort | `idx_jacobs_pipelines_duenio`, range, sin filesort | 0,3487 / 0,4762 | 0,4043 / 0,5375 |
| 3 (canónica) | `idx_jacobs_pipelines_duenio`, range, sin filesort | `idx_jacobs_pipelines_duenio`, range, sin filesort | 0,2883 / 0,3684 | 0,2769 / 0,3446 |

**Con 5000 filas, el optimizador YA elegía `idx_jacobs_pipelines_duenio`
por su cuenta**, con o sin `IGNORE INDEX` -- el `EXPLAIN` es idéntico en
las dos variantes en las 3 corridas. Los tiempos empatan dentro del ruido
de medición (diferencias de centésimas de milisegundo, sin un ganador
consistente entre corridas). `IGNORE INDEX` no cambia nada acá porque el
optimizador YA hacía lo correcto a esta escala -- lo que cambia es que deja
de DEPENDER de que lo siga haciendo.

### (b) Muchos descartados — 60 descartados + 3 vivos

| Corrida | EXPLAIN natural | EXPLAIN IGNORE INDEX | natural p50 / p95 (ms) | IGNORE p50 / p95 (ms) |
|---|---|---|---|---|
| 1 | `idx_pipelines_descartados`, range, **Using filesort** | `idx_jacobs_pipelines_duenio`, ref, sin filesort | 0,1502 / 0,1917 | 0,2959 / 0,3114 |
| 2 | `idx_pipelines_descartados`, range, **Using filesort** | `idx_jacobs_pipelines_duenio`, ref, sin filesort | 0,1126 / 0,1265 | 0,3384 / 0,3950 |
| 3 (canónica) | `idx_pipelines_descartados`, range, **Using filesort** | `idx_jacobs_pipelines_duenio`, ref, sin filesort | 0,1001 / 0,1210 | 0,2675 / 0,3142 |

**A esta escala (63 filas totales), el plan natural CON filesort sigue
siendo más rápido** que forzar `idx_jacobs_pipelines_duenio` -- consistente
en las 3 corridas, entre 2,5x y 3x en p50. Esto confirma lo que ya medía la
ronda anterior (aunque con un número de muestras y un método más flojos):
el filesort de MUY POCAS filas (acotadas por cuántos pipelines vivos tiene
el tenant) es barato, y a esta escala ganarle a la latencia de red/protocolo
del propio `cursor.execute` no alcanza para compensar leer 3 filas extra
por el índice de dueño.

## Lectura: por qué se decide igual con estos números

Ninguna de las dos formas medidas muestra que el plan natural sea
CATASTRÓFICO -- ni la (a) (donde el optimizador ya elige bien solo) ni la
(b) (donde el filesort es barato porque el total de filas es chico). **La
razón de `IGNORE INDEX` no es ganar estos dos benchmarks -- es la garantía
que ninguno de los dos mide directamente:** un plan con filesort tiene que
MATERIALIZAR y ORDENAR el conjunto completo de filas no-descartadas del
tenant ANTES de aplicar el `LIMIT`; ese conjunto no tiene cota (crece con el
histórico), y ninguna de las dos formas de esta medición lo estira lo
suficiente como para que el costo del filesort se vuelva visible frente al
costo fijo de una consulta chica. Con `IGNORE INDEX`, el costo está SIEMPRE
acotado por el `LIMIT` (un range scan en orden que corta apenas junta las
filas de la página), sin importar cuánto crezca el histórico ni qué
estadísticas tenga la tabla en un momento dado -- la propiedad que Ruling
16 pidió, y que ninguna medición de latencia sobre datos chicos puede
desmentir ni confirmar por sí sola.

## Hallazgo adicional, fuera de las dos formas pedidas (reportado, no resuelto acá)

Con una forma MÁS extrema que las dos pedidas por el controlador -- 5000
`discarded` y 3 vivos, en un tenant AISLADO (sin otros tenants en la
tabla) -- ni el plan natural NI el que lleva `IGNORE INDEX (idx_pipelines_
descartados, idx_pipelines_ocultos)` evitan el filesort: los dos eligen
`idx_pipelines_status` (que no está en la lista de índices ignorados) con
`Using filesort`. `idx_pipelines_status` no tiene `user_id`/`tenant_id`, así
que su costo escala con el total de pipelines "vivos" de TODOS los tenants
de la tabla, no sólo el del que pide la página -- en una tabla de
producción real, con miles de tenants activos, ese índice no debería ganar
nunca (medido en la ronda anterior con un fondo de 300-3000 filas
repartidas en 30-300 tenants: en ese caso SIEMPRE ganó un índice acotado
por tenant). Esta forma extra no es una de las dos que pidió Ruling 16 --
se reporta como hallazgo, no se resuelve en este fix: ampliar la lista de
`IGNORE INDEX` a `idx_pipelines_status` sería una decisión de diseño nueva,
fuera del alcance literal de lo que se pidió esta ronda.

## VERDAD OPERACIONAL pendiente

- Estos números son de consultas SQL puras contra la base de test, sin el
  costo de FastAPI/auth/serialización que sí mide `docs/carga-historial-2026-09-18.md`
  para el mismo endpoint (`GET /api/pipelines`) con Jacobs falso y un
  backend real. Esta medición es específica a la DECISIÓN de índice, no un
  reemplazo de esa carga end-to-end.
- Si cambia el esquema de `jacobs_pipelines`, sus índices, o el volumen
  típico de datos, este número caduca y hay que volver a medir (LAS CUATRO
  DEL RENDIMIENTO, política de vigencia).

En memoria de Jairo Urbina.
