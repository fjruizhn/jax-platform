# Carga: SQL_PIPELINES_DEL_USUARIO — la columna `visible` acota el costo por el LIMIT (2026-09-22)

Rama `feat/descartar-pipelines` (jax-platform). Task 4 de la spec
descartar-pipelines. Registrado por Mr. Hyde. Todas las corridas contra la
base de TEST de esta sesión (`jax_memory_test_<sufijo>`, ver
`backend/base_de_test.py`), **nunca** contra `jax_memory`.

## Historia completa (cuatro rondas sobre el MISMO problema)

Este documento se reescribió cuatro veces sobre la MISMA consulta. Se deja
la secuencia completa -- incluidos los errores -- porque cada ronda corrigió
algo específico de la anterior, y el error de cada una es la evidencia de
por qué la siguiente hacía falta:

1. **Fix round 1 (Ruling 13(e)):** aceptó un plan con `Using filesort`
   basado en una medición de 363 filas, 1,6x de diferencia a escala de
   décimas de milisegundo. **Error:** eso es ruido de medición, no
   evidencia -- y el argumento de fondo (`MAX_PIPELINES` acota el
   histórico) era falso: acota los pipelines CONCURRENTES, no el
   histórico completo (`status NOT IN (...)` incluye TODO lo terminado).
2. **Fix round 2 (Ruling 16):** `IGNORE INDEX (idx_pipelines_descartados,
   idx_pipelines_ocultos)` -- el plan volvía a ser determinista. **Error:**
   acoplaba el deploy de jax-platform a una migración de `jax` (jax#257)
   que estaba MERGEADA pero NO DESPLEGADA en producción -- `IGNORE INDEX`
   con un nombre que no existe es un error de MariaDB (1176), no un hint
   que se ignora. Y no cubría un caso extremo (tenant aislado, muchos
   descartados): ahí el plan elegía `idx_pipelines_status`, fuera de la
   lista de índices ignorados, con filesort igual.
3. **Fix round 3 (Ruling 17):** `FORCE INDEX (idx_jacobs_pipelines_duenio)`
   -- sin acoplamiento de deploy (ese índice YA estaba en producción,
   desplegado una semana antes que jax#257) y cubría el caso extremo.
   **Error:** el plan era determinista pero **NO estaba acotado por el
   LIMIT** -- con pocas filas vivas entre muchas descartadas, el motor
   tenía que recorrer casi el histórico completo del tenant antes de poder
   confirmar que no había más filas que devolver: medido 4,2-4,4 ms /
   ~5000 lecturas `Handler_read` con 5000 descartadas y 3 vivas. Documentado
   en su momento como "costo conocido, no resuelto".
4. **Fix round 4 (Ruling 18/19, LA DECISIÓN FINAL, este documento):** `jax`
   agrega una columna GENERADA `visible` (VIRTUAL, TINYINT(1),
   `status NOT IN ('discarded','hidden') AND owner_ack_at IS NOT NULL`) e
   `idx_pipelines_visibles (user_id, tenant_id, visible, created_at)`. Con
   `visible` DENTRO del índice, el rango que el motor recorre ya viene
   filtrado a las filas visibles -- el costo lo acota el LIMIT, no cuántas
   filas no-visibles haya. `SQL_PIPELINES_DEL_USUARIO` pasa a
   `FORCE INDEX (idx_pipelines_visibles)`, y las condiciones redundantes
   (`status NOT IN (...)`, `owner_ack_at IS NOT NULL`) se sacan del WHERE
   -- verificado con EXPLAIN que sacarlas no cambia el plan (mismo `key`/
   `key_len`/`rows`/`Extra` con o sin ellas).

   Acopla el deploy a la rama `feat/pipelines-visible` de `jax` (sin
   mergear a la fecha de este documento) -- MISMO tipo de riesgo que tuvo
   `IGNORE INDEX` en el fix round 2, no evitado esta vez porque no hay
   forma de acotar el costo por el LIMIT sin que la columna `visible`
   exista. Ver el runbook de despliegue (`docs/runbooks/despliegue.md`,
   sección "Caso concreto: descartar/recuperar/ocultar pipelines"): el
   orden es obligatorio, `jax` con `visible` antes que jax-platform.

## Método

- **Dónde viven las mediciones:**
  - `backend/tests/test_pipelines_descarte.py` -- las pruebas PERMANENTES
    de la propiedad (`EXPLAIN` + `Handler_read` reales), parte del piso de
    CI normal, en las TRES formas que pidió el controlador.
  - `backend/tests/test_carga_indice_pipelines_del_usuario.py` -- la
    medición de p50/p95 + Handler_read comparando las TRES decisiones
    (natural, `FORCE idx_jacobs_pipelines_duenio` del round 3,
    `FORCE idx_pipelines_visibles` del round 4), gateada por
    `JAX_MEDIR_INDICE_PIPELINES=1` (siembra hasta 5000+ filas, no corre en
    cada CI).
  - Divergencia deliberada respecto de `loadtest/*.py` (los demás scripts
    de carga de este repo): esos son procesos standalone con su propia
    conexión a MariaDB, correcto para ellos porque miden HTTP de punta a
    punta con un backend real levantado aparte. La regla de esta sesión es
    más estricta ("DB tests ONLY through pytest ... Never query MariaDB
    outside pytest"), y esta medición es SQL puro, así que corre como test
    de pytest.
- **Comando exacto** de la medición de p50/p95 (se salta por default):

  ```
  JAX_REPO_PATH=<checkout de jax CON la columna visible> JAX_MEDIR_INDICE_PIPELINES=1 \
    backend/.venv/bin/python -m pytest -q -s \
    backend/tests/test_carga_indice_pipelines_del_usuario.py
  ```

  Mientras `feat/pipelines-visible` no esté mergeada a `jax` master,
  `JAX_REPO_PATH` tiene que apuntar a un checkout que SÍ la tenga
  (`/home/fruiz/worktrees/jax-visible`, commit `7ba2312`, al momento de
  esta medición).
- **`Handler_read`, no sólo `EXPLAIN`:** el número que decide es lo que el
  motor leyó DE VERDAD (`FLUSH STATUS` -> consulta real -> `SHOW SESSION
  STATUS LIKE 'Handler_read%'`), no la estimación de `rows` de `EXPLAIN` --
  mismo mecanismo que usa `jax` para probar la misma propiedad del lado de
  Jacobs (`jax/tests/test_jacobs_descarte_db.py::CostoAcotadoPorVisibleDBTest`).
  Los tres pasos van en la MISMA conexión/cursor: `Handler_read%` es un
  contador de SESIÓN.
- **Las TRES formas que pidió el controlador**, cada una en su propio
  tenant, filas insertadas con `cur.executemany` (5000 INSERT individuales
  tardan minutos):
  - **(a) "historial largo":** 5000 pipelines visibles (`completed`) + 50
    descartados.
  - **(b) "muchos descartados":** 5000 descartados + 3 vivos.
  - **(c) "hijos sin ack" (nueva en esta ronda):** 5000 filas con
    `owner_ack_at=NULL` (hijos de Ada que Jacobs ya devolvió pero la Mesa
    todavía no reconoció, T6-5a) + 3 vivos con ack. `visible` los excluye
    igual que a los descartados -- su definición incluye
    `owner_ack_at IS NOT NULL`.

## Resultados -- `Handler_read` (la propiedad real, medida dos veces)

| Forma | Filas devueltas | natural (sin hint) | FORCE `idx_jacobs_pipelines_duenio` (round 3) | FORCE `idx_pipelines_visibles` (round 4, FINAL) |
|---|---|---|---|---|
| (a) historial largo | 50 | 50 | 50 | **50** |
| (b) muchos descartados | 3 | 9 | **5004** | **4** |
| (c) hijos sin ack | 3 | 5004 | 5004 | **4** |

Medido dos veces (corridas separadas), mismo patrón las dos:

- **(a)** las tres variantes leen exactamente las 50 filas del LIMIT --
  con 5000 visibles al frente del rango (`created_at DESC`), hasta el plan
  "malo" del round 3 encuentra el LIMIT casi de inmediato. Es la forma
  donde la decisión del round 3 y la del round 4 empatan.
- **(b) y (c)** son la evidencia: `FORCE idx_jacobs_pipelines_duenio` (la
  decisión del round 3) lee **5004** filas para devolver 3 -- prácticamente
  el tenant entero -- en las DOS formas donde las visibles son pocas y
  están mezcladas con miles de no-visibles. `FORCE idx_pipelines_visibles`
  (la decisión final) lee **4** -- las 3 filas más 1 lectura de arranque
  del índice, sin importar cuántas no-visibles haya alrededor.

## Resultados -- tiempo (p50/p95, 15 muestras, complementario a Handler_read)

| Forma | natural p50 (ms) | FORCE duenio p50 (ms) | FORCE visibles p50 (ms, FINAL) |
|---|---|---|---|
| (a) historial largo | 0,33-0,36 | 0,31-0,33 | 0,27-0,37 |
| (b) muchos descartados | 0,10-0,11 | **4,12-5,18** | **0,09-0,12** |
| (c) hijos sin ack | **4,40-4,42** | **3,78-4,40** | **0,07-0,08** |

Confirma lo mismo que `Handler_read` a escala de tiempo: la decisión final
es 30-60x más rápida que la del round 3 en las formas (b) y (c), y
equivalente en la (a).

## Mutación verificada (revertida después de confirmarla)

Reemplazado `FORCE INDEX (idx_pipelines_visibles) ... visible = 1` por
`FORCE INDEX (idx_jacobs_pipelines_duenio) ... status NOT IN (...) AND
owner_ack_at IS NOT NULL` en `api/pipelines.py`:
`test_muchos_descartados_el_motor_no_lee_las_descartadas` (formas "muchos
descartados" e "hijos sin ack") cae con
`5004 lecturas Handler_read para 3 filas devueltas`. La forma "historial
largo" NO cae (ver el porqué arriba) -- coherente con la tabla de
resultados, no un test que no discrimina nada.

## Decisión sobre las condiciones redundantes en el WHERE

`visible` ya incluye `status NOT IN ('discarded','hidden')` y
`owner_ack_at IS NOT NULL` en su propia definición (columna GENERADA,
`jax/jacobs/store.py`). Verificado con `EXPLAIN` que agregar esas dos
condiciones DE NUEVO en el WHERE de `SQL_PIPELINES_DEL_USUARIO` no cambia
el plan (mismo `key`, `key_len`, `rows`, `Extra` con o sin ellas) --
decisión: sacarlas. Mantenerlas sería una segunda fuente de la misma regla
que puede desincronizarse sola de la definición de `visible` (Regla
Absoluta) sin ganar nada medible.

## VERDAD OPERACIONAL pendiente

- Estos números son de consultas SQL puras contra la base de test, sin el
  costo de FastAPI/auth/serialización que sí mide `docs/carga-historial-2026-09-18.md`
  para el mismo endpoint (`GET /api/pipelines`) con Jacobs falso y un
  backend real. Esta medición es específica a la DECISIÓN de índice, no un
  reemplazo de esa carga end-to-end.
- **Acoplamiento de deploy activo, sin resolver:** `idx_pipelines_visibles`
  no existe en producción hoy -- vive en una rama de `jax` sin mergear.
  `FORCE INDEX` con ese nombre revienta con 1176 si jax-platform se
  despliega antes. Ver el runbook de despliegue para el orden obligatorio.
- Si cambia el esquema de `jacobs_pipelines`, sus índices, o el volumen
  típico de datos, este número caduca y hay que volver a medir (LAS CUATRO
  DEL RENDIMIENTO, política de vigencia).

En memoria de Jairo Urbina.
