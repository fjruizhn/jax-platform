# Carga: SQL_PIPELINES_DEL_USUARIO — plan con FORCE INDEX vs plan natural (2026-09-22)

Rama `feat/descartar-pipelines` (jax-platform). Task 4 de la spec
descartar-pipelines. Registrado por Mr. Hyde. Todas las corridas contra la
base de TEST de esta sesión (`jax_memory_test_<sufijo>`, ver
`backend/base_de_test.py`), **nunca** contra `jax_memory`.

**Historial de esta decisión** (para no perder el porqué, tres rondas
seguidas sobre la MISMA consulta):

1. **Fix round 1 (Ruling 13(e)):** aceptó un plan con `Using filesort` a
   partir de una medición floja (363 filas, 1,6x, ruido de medición) y un
   argumento falso (`MAX_PIPELINES` acota los pipelines CONCURRENTES, no el
   histórico -- `status NOT IN ('discarded','hidden')` incluye TODO lo
   terminado, que no tiene techo).
2. **Fix round 2 (Ruling 16):** reemplazó esa aceptación por
   `IGNORE INDEX (idx_pipelines_descartados, idx_pipelines_ocultos)` -- el
   plan volvía a ser determinista, pero con dos problemas medidos después:
   acoplaba el deploy de jax-platform a una migración de `jax` (jax#257)
   MERGEADA pero NO DESPLEGADA en producción (`IGNORE INDEX` con un nombre
   de índice que no existe es un ERROR de MariaDB, 1176, no un hint que se
   ignora), y no cubría un caso extremo (ver abajo), que elegía
   `idx_pipelines_status` -- fuera de la lista de índices ignorados -- con
   filesort igual.
3. **Fix round 3 (Ruling 17, ESTE documento):** reemplaza `IGNORE INDEX`
   por `FORCE INDEX (idx_jacobs_pipelines_duenio)`. Ese índice lo crea
   TAMBIÉN el `init_tables()` de `jax` (no `db/migrations.py` de este
   repo), pero de una migración de una semana ANTES de jax#257 (Ruling
   T6-6, 2026-09-15, la Task 7 del historial de pipelines) que SÍ está
   desplegada en producción hoy -- sin el acoplamiento de deploy que tenía
   `IGNORE INDEX`, y nombrando el índice correcto DIRECTO en vez de una
   lista de exclusiones que podía quedar corta (como quedó corta con
   `idx_pipelines_status`).

Este documento mide las TRES formas de dato que pidió el controlador contra
la decisión final (`FORCE INDEX`), y dice ADEMÁS lo que ese cambio de
decisión no resuelve.

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
- **Comando exacto** (se salta por default -- siembra hasta 5000+ filas, no
  es parte del piso de CI normal):

  ```
  JAX_REPO_PATH=<checkout de jax en master> JAX_MEDIR_INDICE_PIPELINES=1 \
    backend/.venv/bin/python -m pytest -q -s \
    backend/tests/test_carga_indice_pipelines_del_usuario.py
  ```

- **Qué se compara:** el texto EXACTO de `SQL_PIPELINES_DEL_USUARIO` tal
  como queda en `api/pipelines.py` tras Ruling 17 (`FORCE INDEX`) contra el
  mismo texto SIN esa cláusula (`SQL_NATURAL`, copia literal de cómo estaba
  antes de cualquiera de los tres fixes, congelada en el archivo de test --
  si el SQL de producción cambia después, esta comparación sigue midiendo
  lo que dice comparar).
- **Muestras:** 15 ejecuciones por variante y forma (`n=15`, ≥10 que pidió
  el controlador), mismo `cursor` reusado, sin reconexión entre muestras.
  `ANALYZE TABLE jacobs_pipelines` corre una vez, después de sembrar cada
  forma y antes de medirla -- sin esto el plan puede depender de qué otros
  tests corrieron antes en la sesión (estadísticas persistentes de InnoDB).
- **Las TRES formas** (las dos de Ruling 16 más la extrema que ese mismo
  fix destapó), cada una en su propio tenant (para no mezclar datos entre
  formas), filas insertadas con `cur.executemany` (no una por una -- 5000
  INSERT individuales tardan minutos):
  - **(a) "historial largo":** 5000 pipelines `completed` + 50 `discarded`.
  - **(b) "muchos descartados":** 60 `discarded` + 3 `completed`.
  - **(c) "extremo" (nueva en esta ronda):** 5000 `discarded` + 3
    `completed`, en un tenant AISLADO (sin fondo de otros tenants) -- la
    forma que eligió `idx_pipelines_status` con filesort en el fix round 2.
- **Repetido 4 veces** (corridas separadas del comando de arriba, no 4
  sembrados en la misma corrida) para confirmar que el plan y el orden de
  magnitud de los tiempos son estables, no una casualidad de una corrida.

## Resultados

### (a) Historial largo — 5000 terminados + 50 descartados

| Corrida | EXPLAIN natural | EXPLAIN FORCE INDEX | natural p50 / p95 (ms) | FORCE p50 / p95 (ms) |
|---|---|---|---|---|
| 1 | `idx_jacobs_pipelines_duenio`, sin filesort | `idx_jacobs_pipelines_duenio`, sin filesort | 0,3814 / 0,5136 | 0,2469 / 0,3174 |
| 2 | `idx_jacobs_pipelines_duenio`, sin filesort | `idx_jacobs_pipelines_duenio`, sin filesort | 0,2682 / 0,3018 | 0,2408 / 0,2609 |
| 3 | `idx_jacobs_pipelines_duenio`, sin filesort | `idx_jacobs_pipelines_duenio`, sin filesort | 0,3374 / 0,3779 | 0,3116 / 0,3185 |
| 4 (canónica) | `idx_jacobs_pipelines_duenio`, sin filesort | `idx_jacobs_pipelines_duenio`, sin filesort | 0,2883 / 0,3684 | 0,2769 / 0,3446 |

**El optimizador YA elegía `idx_jacobs_pipelines_duenio` por su cuenta a
esta escala**, con o sin el hint -- el `EXPLAIN` es idéntico en las dos
variantes, en las 4 corridas. Los tiempos empatan dentro del ruido de
medición.

### (b) Muchos descartados — 60 descartados + 3 vivos

| Corrida | EXPLAIN natural | EXPLAIN FORCE INDEX | natural p50 / p95 (ms) | FORCE p50 / p95 (ms) |
|---|---|---|---|---|
| 1 | `idx_jacobs_pipelines_duenio`, sin filesort | `idx_jacobs_pipelines_duenio`, sin filesort | 0,1515 / 0,2062 | 0,2914 / 0,3170 |
| 2 | `idx_pipelines_descartados`, **con filesort** | `idx_jacobs_pipelines_duenio`, sin filesort | 0,1082 / 0,1279 | 0,2575 / 0,2676 |
| 3 | `idx_jacobs_pipelines_duenio`, sin filesort | `idx_jacobs_pipelines_duenio`, sin filesort | 0,2466 / 0,2739 | 0,2583 / 0,3023 |
| 4 (canónica) | `idx_pipelines_descartados`, **con filesort** | `idx_jacobs_pipelines_duenio`, sin filesort | 0,1460 / 0,1824 | 0,2907 / 0,3114 |

**A esta escala (63 filas totales) el plan natural es al menos tan rápido,
a veces más** -- y el plan NATURAL en sí mismo VARÍA entre corridas (a
veces elige `idx_jacobs_pipelines_duenio` solo, a veces
`idx_pipelines_descartados` con filesort barato): la tabla es tan chica que
el optimizador está genuinamente indeciso, y con estadísticas persistentes
de InnoDB que no se recalculan en cada corrida, cuál gana depende de qué
pasó antes en la sesión. Es exactamente el problema que motivó forzar el
índice en primer lugar -- acá se ve en carne propia.

### (c) Extremo — 5000 descartados + 3 vivos, tenant aislado

| Corrida | EXPLAIN natural | EXPLAIN FORCE INDEX | natural p50 / p95 (ms) | FORCE p50 / p95 (ms) |
|---|---|---|---|---|
| 1 | `idx_pipelines_status`, **con filesort** | `idx_jacobs_pipelines_duenio`, sin filesort | 0,1055 / 0,1385 | **4,2570 / 4,6353** |
| 2 | `idx_pipelines_status`, **con filesort** | `idx_jacobs_pipelines_duenio`, sin filesort | 0,1099 / 0,1486 | **4,3882 / 5,0738** |
| 3 | `idx_pipelines_status`, **con filesort** | `idx_jacobs_pipelines_duenio`, sin filesort | 0,0893 / 0,1258 | **4,2540 / 4,3362** |
| 4 (canónica) | `idx_pipelines_status`, **con filesort** | `idx_jacobs_pipelines_duenio`, sin filesort | 0,1292 / 0,1723 | **4,2540 / 4,6353** |

**`FORCE INDEX` cumple el contrato que pidió Ruling 17** (siempre
`idx_jacobs_pipelines_duenio`, nunca filesort) **pero es 30-45x más lento
que el plan natural en esta forma**, consistente en las 4 corridas (~4,2-
4,4 ms contra ~0,09-0,13 ms). La causa, medida no supuesta: el `LIMIT` por
default (`LISTA_PIPELINES_MAX = 50`) es MAYOR que el total de filas "vivas"
de este tenant (3). Un range scan por `idx_jacobs_pipelines_duenio` no
puede saber de antemano que sólo hay 3 filas que van a pasar el filtro de
`status` -- tiene que recorrer las 5003 filas del tenant hasta el final del
rango para confirmar que no hay más, porque el `LIMIT` nunca se satisface
antes. `idx_pipelines_status` (el plan natural acá) evita ese recorrido
completo porque empuja el filtro de status DENTRO del índice y encuentra
las 3 filas sin tener que examinar las 5000 descartadas -- el costo del
filesort sobre esas 3 filas es insignificante comparado con eso.

Verificado que NO es un artefacto del orden de inserción (`created_at` de
los vivos vs los descartados): repetida la medición con los vivos MÁS
RECIENTES que los descartados (en vez de más viejos), el resultado es el
mismo -- `rows` estimado en el `EXPLAIN` de `FORCE INDEX` sigue en ~4700-
5300 (prácticamente el tenant completo) y el tiempo sigue en ~4,2 ms. La
causa es el `LIMIT` sin satisfacer, no el orden de los datos.

## Lectura: la decisión final, con las tres formas ya medidas

Ninguna de las tres formas por sí sola desmiente la garantía que pidió
Ruling 17 (nombrar el índice correcto DIRECTO, sin la lista de exclusiones
que se quedaba corta, y sin acoplar el deploy a una migración de `jax` que
hoy no está en producción) -- **pero la forma (c) muestra que esa garantía
tiene un costo real, medido, no hipotético:** cuando el histórico de
descartados de un tenant es mucho más grande que su cantidad de pipelines
vivos (exactamente el escenario que la propia spec anticipa, "van a ser
muchos en el tiempo"), `FORCE INDEX` fuerza un recorrido completo del
histórico del tenant en vez de un salto directo por status. El plan natural
de la forma (c) -- el que `FORCE INDEX` reemplaza -- era 30-45x más rápido
ahí.

**Esto no invalida Ruling 17** (el problema que resolvía -- el
acoplamiento de deploy con `IGNORE INDEX` -- es real y más grave que una
consulta lenta: un 500 en producción, no una demora) **pero es un costo
que queda anotado, no escondido.** Ninguna de las tres formas medidas hoy
representa "un usuario típico" de memoria -- son los extremos que pidió el
controlador a propósito. El costo real depende de qué tan sesgada esté la
distribución vivos/descartados de un tenant real, dato que esta ronda no
tiene (la funcionalidad de descartar recién se está construyendo, no hay
todavía uso real en producción para medir esa distribución).

## VERDAD OPERACIONAL pendiente

- Estos números son de consultas SQL puras contra la base de test, sin el
  costo de FastAPI/auth/serialización que sí mide `docs/carga-historial-2026-09-18.md`
  para el mismo endpoint (`GET /api/pipelines`) con Jacobs falso y un
  backend real. Esta medición es específica a la DECISIÓN de índice, no un
  reemplazo de esa carga end-to-end.
- La forma (c) queda como un costo conocido y no resuelto: si en producción
  aparecen tenants con una relación descartados:vivos muy alta (miles a
  pocos), esta medición dice que `GET /api/pipelines` para ESE tenant va a
  tardar milisegundos de más (no segundos -- 4-5 ms medido, no
  catastrófico) por página pedida. No se optimizó más allá de esto en esta
  ronda: es una decisión de diseño (¿un índice compuesto con status?
  ¿limitar cuántos "vivos" puede tener un tenant antes de forzar el
  descarte de los más viejos?) fuera del alcance de Ruling 17.
- Si cambia el esquema de `jacobs_pipelines`, sus índices, o el volumen
  típico de datos, este número caduca y hay que volver a medir (LAS CUATRO
  DEL RENDIMIENTO, política de vigencia).

En memoria de Jairo Urbina.
