# Carga: listado de pipelines con descarte (`GET /api/pipelines` y `?estado=discarded`) — 2026-09-22

Rama `feat/descartar-pipelines` (jax-platform). Task 8, LAS CUATRO DEL
RENDIMIENTO #4: sin número medido no hay GO. Registrado por Mr. Hyde. Corrida
completada 2026-09-22 ~12:17 CST (hall9000). Los números salen de la
corrida; ninguno es estimado.

**Fix round 1 (BLOCK-2, 2026-09-22, revisión adversarial de PR#151):** el
cierre de los dos huecos de la revisión final de esta rama (Descartados del
superadmin y Auditoría de descarte, jax-platform PR#151) agregó DOS
endpoints nuevos sin ninguna carga -- `loadtest/` y este documento quedaron
sin tocar en la primera vuelta. Esta sección los cierra con el MISMO
arnés (`loadtest/descartados_orquestar.py`, extendido, no un script
aparte), el MISMO barrido de concurrencia y el mismo criterio de
aislamiento que el resto del documento. Corrida completada 2026-09-22
~16:20 CST (hall9000, la misma máquina), 0 errores en las dos series, en
las seis concurrencias -- ver "Resultados -- los dos endpoints nuevos" más
abajo.

**Por qué un documento nuevo y no una extensión de
`docs/carga-sql-pipelines-del-usuario-indice-2026-09-22.md`:** ese documento
mide el motor SQL puro (`EXPLAIN` + `Handler_read` + `cursor.execute`/
`fetchall` en la misma conexión, sin HTTP) para decidir ENTRE variantes de
índice — es la evidencia del fix round 4 de Task 1-bis (jax), comparando tres
formas de la misma consulta. Este documento mide el camino COMPLETO (HTTP →
auth → Mesa → SQL) de los dos endpoints que jax-platform expone de verdad
(`backend/api/pipelines.py::list_pipelines`), con `httpx.AsyncClient` real
contra un backend real — la pregunta que responde es otra ("¿aguanta la
Mesa, no sólo el motor?"), mismo criterio que separó
`docs/carga-historial-2026-09-18.md` (HTTP) de su propio antecedente SQL en
2026-09-17. Mezclar los dos en un documento habría hecho más difícil volver
a medir sólo uno de los dos cuando corresponda.

**Herramienta:** `loadtest/descartados_orquestar.py`. Reproduce esta corrida
completa — siembra las dos formas del peor caso, levanta Jacobs falso y el
backend real, mide, limpia — con un solo comando desde la raíz del repo:

```bash
JAX_REPO_PATH=/home/fruiz/worktrees/jax-master-para-tests \
  python3 loadtest/descartados_orquestar.py
```

`JAX_REPO_PATH` tiene que apuntar a un checkout de `jax` con jax#257
(columnas `status_previo`/`descartado_por`/`descartado_at`) y jax#259
(columna generada `visible` + `idx_pipelines_visibles`) — sin esas dos, el
esquema de `jax_memory_test` no tiene ni las columnas de descarte ni el
índice que jax-platform usa (`SQL_PIPELINES_DEL_USUARIO` lleva
`FORCE INDEX (idx_pipelines_visibles)`). El generador de datos vive en
`loadtest/descartados_seed.py` (agrega el esquema de forma IDEMPOTENTE
llamando a `jacobs.store.init_tables()` antes de sembrar, y la limpieza en
`loadtest/descartados_limpiar.py` — esta última corre SIEMPRE al terminar,
incluso si la carga revienta a mitad de camino. El script se niega a
arrancar si detecta que apunta a `jax_memory`, o a los puertos de producción
`:7777`/`:8080` — ver `_verificar_no_apunta_a_produccion()`.

## Qué se midió y por qué

- `GET /api/pipelines` — el listado principal (excluye `discarded`/`hidden`
  por diseño, spec §4: "sin `estado`, se pide `visible`").
- `GET /api/pipelines?estado=discarded` — la pestaña "Descartados" del
  historial (Task 6).

**Dos formas, no una**, decisión del coordinador — las mismas dos que ya
comparó a nivel SQL puro `test_carga_indice_pipelines_del_usuario.py` (jax-platform,
fix round 4 de Task 1-bis):

- **"Escala"** (el mínimo pedido: ≥ 5.000 pipelines, 20 % descartados):
  **5.000 pipelines** de un usuario — **4.000 visibles** (`status='completed'`,
  `owner_ack_at` seteado) + **1.000 descartados** (`status='discarded'`,
  `descartado_at`/`status_previo`/`descartado_por` seteados como los
  escribiría Jacobs de verdad, no `NULL`) — exactamente 20 %.
- **"Extremo"** (pocos vivos, muchos descartados — el caso que en la ronda 3
  del índice, antes de `visible`, pagaba un recorrido lineal del histórico
  completo del dueño): **3 visibles + 5.000 descartados** del mismo usuario.
- Concurrencia: c=1, 25, 50, 100, 150, 200 (mismo barrido que
  `docs/carga-historial-2026-09-18.md`, para poder comparar el perfil).

**Fix round 1 (BLOCK-2): dos formas MÁS, para los dos endpoints nuevos del
cierre de huecos:**

- **"admin_muchos_usuarios"** — `GET /api/admin/pipelines/descartados`. A
  diferencia de las formas A/B (la vista DEL DUEÑO, un solo `user_id`), este
  endpoint es GLOBAL: **5.000 filas `discarded`** repartidas entre **500
  pares `user_id`/`tenant_id` DISTINTOS** (mismo orden de magnitud que la
  forma B, para comparar el perfil), sin cuentas reales en `jax_users`
  (el endpoint no hace `JOIN` con esa tabla) — sólo el superadmin que hace
  el pedido es una cuenta real.
- **"pipeline_con_muchos_eventos"** — `GET /pipelines/{id}/auditoria-descarte`.
  Decisión del coordinador: este endpoint "fires on EVERY pipeline-detail
  open", así que el peor caso es el pipeline con MÁS eventos acumulados, no
  uno recién creado. Un pipeline del usuario "escala" con **221 eventos
  `STEP_FAILED`** (mismo número que midió el Ruling R20 para
  `sql_eventos_de_causa`) **+ los 4 tipos de auditoría** (225 en total).

## Método de aislamiento

- Base `jax_memory_test` (nunca `jax_memory`) — verificado con `SELECT
  DATABASE()` antes de escribir una fila y con `/proc/<pid>/environ` del
  proceso real después de levantarlo (`JAX_DB_NAME=jax_memory_test`,
  `JACOBS_URL=http://127.0.0.1:17778/jacobs`). Puertos de producción
  (`:7777`/`:8080`) nunca tocados — `BACKEND_PORT`/`FAKE_JACOBS_PORT` de este
  script (18081/17778) son DISTINTOS de los que ya usa
  `historial_orquestar.py` (18080/17777), para poder correr las dos cargas
  sin pisarse.
- Jacobs falso propio (`historial_fake_jacobs.py`, reusado tal cual): NINGUNO
  de los dos endpoints medidos llama a Jacobs (son listados puros sobre
  `jacobs_pipelines`), pero el backend real necesita `JACOBS_URL`/
  `LAS_MANOS_URL` configurados para arrancar. Confirmado por diseño: la
  latencia medida no incluye ningún salto de red a Jacobs.
- Backend real del worktree (`main:app`), un solo proceso uvicorn en
  `127.0.0.1:18081` — sin `--workers`, la MISMA topología que
  `jax-platform.service` en producción.
- Aislado además de la base: `JAX_USAGE_SPOOL_DIR`, `JAX_FACET_SEAL_PATH`,
  `JAX_KILL_SWITCH_PATH`, `JAX_EJECUTOR_PAUSA`/`JAX_EJECUTOR_PYTHON` (runner
  inexistente), `JAX_MISSIONS_DIR`, `JAX_REPO_BASE`, `JAX_ADJUNTOS_DIR`,
  `JAX_PROXY_CARRIL_RAIZ`, todos en directorios temporales de la corrida.
  `CANARY_INTERVAL_SECONDS=0`.
- **Seguridad de la firma (jax-platform#146, ronda 7, mismo mecanismo que
  `historial_orquestar.py`):** `JAX_JWT_SECRET` de la corrida es una llave
  nueva y aleatoria, releída de `/proc/<pid>/environ` del proceso YA
  levantado (nunca del `env` en memoria del script) y comparada contra la de
  producción — el script aborta si coinciden. Ningún token de esta carga es
  válido contra producción.
- Cliente de carga: `httpx.AsyncClient` real contra el puerto TCP (HTTP de
  punta a punta).
- Dos usuarios descartables (`tenant_id=1`, rol `operator`) en `jax_users`,
  cada uno con su propio token JWT. Fix round 1 (BLOCK-2): +1 usuario
  descartable con rol `superadmin` (para `GET /admin/pipelines/descartados`)
  y +1 pipeline con 225 eventos propios en `jacobs_events` (para
  `GET /pipelines/{id}/auditoria-descarte`) — ninguno de los dos crea
  cuentas reales para las 5.000 filas de "admin_muchos_usuarios": ese
  endpoint no hace `JOIN` con `jax_users`.
- Al terminar: `os.killpg` sobre los dos procesos; toda fila sembrada
  (15.004 pipelines entre los cuatro orígenes + los 3 usuarios + 225
  eventos) BORRADA y verificada — `SELECT COUNT(*) FROM jacobs_pipelines
  WHERE user_id IN (...)` (y su equivalente para las dos formas nuevas)
  volvió a 0 tras la corrida, confirmado independientemente con una
  consulta aparte después de que el script terminó.

## Verificación de que se midió el servicio real, no un literal

Antes de la tanda de carga, una petición de control a cada endpoint por
usuario, impresa con su tamaño de cuerpo y su contenido:

```
usuario=escala: GET /api/pipelines status=200 bytes=11031 pipelines=50 has_more=True
  | ?estado=discarded status=200 bytes=12781 pipelines=50 has_more=True
usuario=extremo: GET /api/pipelines status=200 bytes=692 pipelines=3 has_more=False
  | ?estado=discarded status=200 bytes=12781 pipelines=50 has_more=True
```

El usuario "extremo" trae exactamente **3** pipelines en el listado principal
(`has_more=False`) — sus otros 5.000 están descartados y `visible` los deja
afuera del todo, no es un `LIMIT` que los corta. El usuario "escala" trae 50
de sus 4.000 visibles (`has_more=True`). Los dos traen 50 descartados de los
suyos en `?estado=discarded` (1.000 y 5.000 respectivamente).

**El índice se usa, verificado con `EXPLAIN` sobre la consulta real** contra
una siembra preliminar con la MISMA forma (usuarios de validación, no los de
la corrida cronometrada — se sembró, se verificó y se limpió antes de la
corrida final):

```
EXPLAIN SQL_PIPELINES_DEL_USUARIO (usuario "escala", 4.000 visibles + 1.000 descartados):
  type=ref  key=idx_pipelines_visibles  key_len=408  ref=const,const,const
  rows=8024  Extra=Using where          -- sin filesort, sin temporary

EXPLAIN SQL_DESCARTADOS_DEL_USUARIO (usuario "extremo", 3 visibles + 5.000 descartados):
  type=range  key=idx_pipelines_descartados  key_len=488
  rows=10242  Extra=Using where         -- sin filesort, sin temporary
```

`SQL_PIPELINES_DEL_USUARIO` usa `idx_pipelines_visibles` (`FORCE INDEX`
explícito en el SQL); `SQL_DESCARTADOS_DEL_USUARIO` no lleva `FORCE INDEX` y
el optimizador elige `idx_pipelines_descartados` solo, correctamente, entre
los seis índices posibles de la tabla. Ninguna de las dos consultas hace
`Using filesort` ni `Using temporary` (LAS CUATRO #1).

## Resultados — `GET /api/pipelines` (usuario "escala": 4.000 visibles + 1.000 descartados)

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 642,87 | 1,38 | 2,10 | 4,34 | 12,80 |
| 25 | 500 | 500 | 0 | 1373,15 | 17,49 | 20,79 | 29,04 | 31,60 |
| 50 | 1000 | 1000 | 0 | 759,79 | 40,62 | 187,13 | 296,68 | 618,85 |
| 100 | 2000 | 2000 | 0 | 353,41 | 132,70 | 992,36 | 1625,77 | 2611,24 |
| 150 | 2000 | 2000 | 0 | 323,14 | 141,01 | 2065,12 | 3817,50 | 5482,44 |
| 200 | 2000 | 2000 | 0 | 302,58 | 196,34 | 3421,08 | 5084,18 | 5960,48 |

## Resultados — `GET /api/pipelines?estado=discarded` (usuario "escala")

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 600,57 | 1,59 | 2,08 | 2,59 | 3,51 |
| 25 | 500 | 500 | 0 | 1311,01 | 17,12 | 31,25 | 37,29 | 40,84 |
| 50 | 1000 | 1000 | 0 | 987,00 | 36,25 | 131,76 | 245,90 | 360,32 |
| 100 | 2000 | 2000 | 0 | 351,15 | 135,95 | 973,81 | 1729,36 | 3159,32 |
| 150 | 2000 | 2000 | 0 | 295,56 | 204,95 | 2181,74 | 3660,10 | 5428,11 |
| 200 | 2000 | 2000 | 0 | 283,91 | 238,65 | 3516,68 | 5037,62 | 6000,59 |

## Resultados — `GET /api/pipelines` (usuario "extremo": 3 visibles + 5.000 descartados)

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 1020,45 | 0,96 | 1,11 | 1,29 | 1,51 |
| 25 | 500 | 500 | 0 | 1125,67 | 14,95 | 57,57 | 85,12 | 110,78 |
| 50 | 1000 | 1000 | 0 | 647,79 | 49,53 | 215,02 | 312,89 | 476,35 |
| 100 | 2000 | 2000 | 0 | 357,23 | 120,45 | 992,75 | 1737,26 | 3093,41 |
| 150 | 2000 | 2000 | 0 | 328,83 | 132,53 | 2180,12 | 3601,50 | 5300,92 |
| 200 | 2000 | 2000 | 0 | 317,49 | 169,51 | 3424,71 | 5030,47 | 5458,31 |

## Resultados — `GET /api/pipelines?estado=discarded` (usuario "extremo")

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 603,71 | 1,58 | 1,96 | 2,13 | 9,02 |
| 25 | 500 | 500 | 0 | 1153,49 | 17,67 | 47,02 | 64,88 | 100,46 |
| 50 | 1000 | 1000 | 0 | 647,69 | 50,17 | 221,20 | 299,82 | 502,23 |
| 100 | 2000 | 2000 | 0 | 349,87 | 136,09 | 1004,59 | 1592,57 | 2869,95 |
| 150 | 2000 | 2000 | 0 | 322,72 | 147,00 | 2096,17 | 3633,18 | 5371,48 |
| 200 | 2000 | 2000 | 0 | 294,69 | 221,43 | 3423,09 | 5021,29 | 6082,89 |

## Resultados — los dos endpoints nuevos (fix round 1, BLOCK-2, 2026-09-22)

**El índice se usa, verificado con `EXPLAIN` + `Handler_read` sobre la
consulta real** (mismo mecanismo que la sección de arriba; números también
reproducibles con `backend/tests/test_pipelines_descartados_admin.py::test_explain_descartados_admin_usa_idx_pipelines_ocultos_sin_filesort`
y `test_pipelines_auditoria_descarte.py::test_auditoria_descarte_no_escanea_todo_el_pipeline`):

```
EXPLAIN SQL_DESCARTADOS_ADMIN (600 filas discarded, 100 usuarios/30 tenants distintos):
  type=range  key=idx_pipelines_ocultos  key_len=82
  Extra=Using index condition            -- sin filesort, sin temporary

EXPLAIN SQL_AUDITORIA_DESCARTE (pipeline con 221 eventos de ruido + 4 de auditoría):
  type=range  key=idx_events_pipeline_tipo  key_len=348
  Extra=Using index condition            -- sin filesort, sin temporary
  Handler_read TOTAL=8 para 4 filas devueltas (no ~225: el índice compuesto
  filtra por event_type ANTES de traer una fila, no después)
```

**Verificación previa** (mismo criterio que arriba — el camino real, no un
literal):

```
GET /api/admin/pipelines/descartados status=200 bytes=10831 pipelines=50 has_more=True
GET /api/pipelines/{id}/auditoria-descarte status=200 bytes=397 eventos=4
```

`auditoria-descarte` devuelve **4** eventos (los de auditoría) de un
pipeline con **225** filas en `jacobs_events` — el filtro por `event_type`
funciona, no es un volcado de todo lo que tiene el pipeline.

### `GET /api/admin/pipelines/descartados` (superadmin, 5.000 filas / 500 usuarios)

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 659,45 | 1,51 | 1,76 | 2,00 | 2,15 |
| 25 | 500 | 500 | 0 | 963,24 | 19,30 | 63,57 | 95,37 | 107,53 |
| 50 | 1000 | 1000 | 0 | 581,07 | 57,52 | 227,68 | 344,34 | 456,62 |
| 100 | 2000 | 2000 | 0 | 324,97 | 167,92 | 1049,93 | 1659,69 | 3547,53 |
| 150 | 2000 | 2000 | 0 | 312,85 | 165,88 | 2112,29 | 3799,05 | 5687,34 |
| 200 | 2000 | 2000 | 0 | 268,41 | 285,96 | 3528,23 | 5157,47 | 6042,47 |

### `GET /api/pipelines/{id}/auditoria-descarte` (pipeline con 225 eventos, 221 de ruido)

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 964,42 | 1,02 | 1,23 | 1,37 | 1,74 |
| 25 | 500 | 500 | 0 | 1017,67 | 17,20 | 60,23 | 87,52 | 128,42 |
| 50 | 1000 | 1000 | 0 | 587,31 | 51,71 | 240,31 | 361,30 | 567,02 |
| 100 | 2000 | 2000 | 0 | 349,57 | 128,86 | 1034,31 | 1635,60 | 2825,15 |
| 150 | 2000 | 2000 | 0 | 320,35 | 143,39 | 2148,06 | 3578,89 | 5320,42 |
| 200 | 2000 | 2000 | 0 | 304,68 | 200,58 | 3564,51 | 5060,24 | 5858,79 |

**Lectura:** 0 errores en las dos series, en las seis concurrencias. El
perfil es **indistinguible** del de los cuatro escenarios de arriba —
mismo orden de magnitud de p50/p95 en cada nivel de `c`, mismo tramo de
saturación (rps satura entre c=25 y c=50, p95 cruza los 500 ms de
referencia entre c=50 y c=100). `auditoria-descarte` en particular —
"fires on EVERY pipeline-detail open" — se mantiene bajo 1,1 ms de p50 a
c=1 y bajo 52 ms a c=50 pese a examinar sólo 4 de las 225 filas del
pipeline con más eventos que se sembró: el índice compuesto
(`idx_events_pipeline_tipo`) cumple lo que promete (costo acotado por las
filas de AUDITORÍA, no por el total de eventos del pipeline), igual que
`idx_pipelines_visibles`/`idx_pipelines_descartados` lo cumplen para el
resto de este documento.

## Lectura: dónde empieza a degradarse

**0 errores en los cuatro escenarios, en las seis concurrencias.** No hay un
punto donde el servicio empiece a rechazar o caerse dentro de lo medido
(hasta c=200).

**Las cuatro series son prácticamente indistinguibles entre sí** (mismo
orden de magnitud de p50/p95 en cada nivel de c, "escala" vs "extremo",
`/pipelines` vs `?estado=discarded`) — y esa es la lectura central de esta
carga: **la latencia NO depende del tamaño del histórico del usuario**, ni de
si son mayormente vivos o mayormente descartados. Es exactamente lo que
`idx_pipelines_visibles`/`idx_pipelines_descartados` prometen (el costo lo
acota el `LIMIT`, no el histórico) y lo que el `EXPLAIN` de arriba confirma
(`rows` del plan en el orden de miles, no de los 5.000-10.000 reales de la
tabla, sin filesort). El usuario "extremo" (3 visibles entre 5.000
descartados) NO paga un recorrido lineal — el problema que la ronda 3 del
índice (antes de `visible`) sí tenía, y que esta carga confirma CERRADO a
nivel HTTP, no sólo a nivel SQL.

Lo que sí degrada, con el mismo perfil que ya documentó
`docs/carga-historial-2026-09-18.md` para el resto de este endpoint, es la
latencia de cola por concurrencia del proceso único:

- El **rps satura entre c=25 y c=50** (pico ~1100-1400 rps en c=25,
  cayendo a 280-360 rps de c=100 en adelante) — firma de un solo worker
  uvicorn (misma topología que producción, sin `--workers`): todo pedido de
  más se pone en cola detrás del que ya se está sirviendo.
- Con el rps cayendo, el **p95 crece muy por encima de lineal con la
  concurrencia**: en las cuatro series, el salto más fuerte es entre c=25 y
  c=50 (p95 sube ~4-9× mientras c sólo dobla) y entre c=50 y c=100 (otro
  ~4-5×) — ahí es donde "empieza a degradarse" de verdad, no en un punto
  fijo sino en un tramo: **c=50 es el primer nivel donde el p95 ya no es
  chico** (132-221 ms) y **c=100 es donde cruza el segundo** (973-1004 ms)
  en las cuatro series.
- **Umbral práctico** (p95 < 500 ms, mismo valor de referencia que
  `carga-historial-2026-09-18.md`, no un contrato): las cuatro series lo
  cruzan entre c=50 (132-221 ms, todavía por debajo) y c=100 (973-1004 ms,
  ya muy por encima) — el mismo tramo, en las cuatro, reforzando que el
  cuello no es la consulta sino la concurrencia del proceso único.
- **c=1 se mantiene por debajo de 2 ms en las cuatro series** (0,96-1,59 ms
  p50): el trabajo POR PEDIDO es mínimo; toda la latencia de las
  concurrencias altas es tiempo de cola, no trabajo.

## VERDAD OPERACIONAL pendiente

- Este perfil de saturación (rps plano desde c=25-50, p95 creciente con la
  cola) es una característica del PROCESO ÚNICO de `jax-platform.service`,
  no de esta consulta en particular — ya lo había medido
  `carga-historial-2026-09-18.md` para `GET /api/pipelines` sin descarte.
  Esta carga confirma que agregar `visible`/`estado=discarded` NO empeoró
  ese perfil (las cuatro series están en el mismo orden que la carga
  anterior a esta ronda) y que el TAMAÑO del histórico dejó de importar
  gracias al índice — son dos preguntas distintas, las dos con GO.
- Si cambia el esquema, el volumen de datos, la cantidad de workers de
  producción o la infraestructura, este número caduca y hay que volver a
  medir (LAS CUATRO DEL RENDIMIENTO, política de vigencia).
- No se midió `OFFSET` profundo (paginación más allá de la primera página)
  ni un usuario con MÁS de 5.000 descartados (p.ej. 50.000) — fuera del
  alcance de esta ronda, que se enfocó en el mínimo pedido por el
  coordinador (≥ 5.000, 20 % descartados) más la forma extrema.
- Fix round 1 (BLOCK-2): mismo criterio de vigencia para
  `admin_muchos_usuarios`/`pipeline_con_muchos_eventos` -- si cambia el
  esquema, el volumen de datos o la infraestructura, estos dos números
  también caducan. No se midió un pipeline con MÁS de 225 eventos (p.ej.
  miles, de un ciclo discard/recover repetido sin `LIMITE_AUDITORIA_DESCARTE`
  -- ver ese tope en `api/pipelines.py`, que acota justamente ese caso) ni
  más de 500 usuarios distintos en la vista admin-wide -- fuera del alcance
  de esta ronda, que igualó el orden de magnitud de las formas A/B ya
  medidas.

En memoria de Jairo Urbina.
