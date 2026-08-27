# fix: el TOPE de tokens de salida lo declara el catálogo, no el código

**Rama:** `fix/model-max-output-tokens` (desde `master` @ `6800a32`)
**Fecha:** 2026-08-27
**Estado:** staged (`git add -A`), sin commitear — el hook `block-subagent-git-write.sh`
bloquea commits de subagente. No se desplegó, no se reinició nada, no se mergeó.

---

## 1. El bug

Segunda mitad del incidente de thot. El PR #17 (`6800a32`) arregló el **NOMBRE**
del parámetro de límite de salida (`model.max_tokens_param`). Con el nombre
correcto, la misma API rechazó el **VALOR**:

```
HTTP 400: "max_tokens is too large: 131072. This model supports at most
128000 completion tokens, whereas you provided 131072."
```

`backend/api/chat.py::_call_openai_compat` mandaba `131072` fijo (constante
`_MAX_OUTPUT_TOKENS`). Ese valor era universal mientras todos los modelos del
camino openai-compat lo aceptaran. Dejó de serlo.

**`context_window` NO sirve para derivarlo** — verificado contra `jax_memory`
(SELECT de solo lectura, sin escrituras):

| provider | model_id | context_window | tope de completion real |
|---|---|---|---|
| openai | gpt-5.6-terra | 1050000 | **128000** |
| deepseek | deepseek-v4-flash | 1000000 | 131072 |
| moonshot | kimi-k3 | 1048576 | 131072 |
| zhipu | glm-5.3 | **NULL** | 131072 |

La ventana total (entrada+salida) y el tope de completion son hechos distintos:
para `gpt-5.6-terra` difieren en un factor de ~8, y `glm-5.3` ni siquiera tiene
`context_window` poblado. Una fórmula habría sido inventar el dato.

## 2. El fix

Columna nueva `model.max_output_tokens INT NULL`, leída al despachar, exactamente
como `max_tokens_param`. Son un **par**: uno dice CÓMO se llama el parámetro, el
otro QUÉ VALOR admite.

### Cambios por archivo

**`backend/db/migrations.py`**
- `CREATE TABLE model`: columna `max_output_tokens INT NULL` justo debajo de
  `max_tokens_param`, con comentario que explica por qué NO es `context_window`.
- `_COLUMNS`: entrada `("model", "max_output_tokens", "ALTER TABLE model ADD
  COLUMN max_output_tokens INT NULL")` — migración idempotente, no `ALTER` a mano.
- `_MODEL_MAX_OUTPUT_TOKENS_SEED`: los 4 modelos del camino openai-compat.
- `_seed_model_max_output_tokens(cur)`: `UPDATE ... WHERE ... AND
  max_output_tokens IS NULL` (idempotente, no pisa un valor manual — mismo guard
  que `_seed_model_max_tokens_param` / `_seed_http_facet_allowed_callers`).
- `run_migrations()`: llamada después de `_seed_model_max_tokens_param(cur)`.

**`backend/facet_resolver.py`**
- `ResolvedFacet.max_output_tokens: int | None`, con el mismo bloque de
  "DIVERGENCIA DELIBERADA DE LOS ESPEJOS" que el campo anterior (este archivo
  está replicado en 3 codebases; el campo existe solo en la copia de
  jax-platform y su ausencia en las otras dos no es drift).
- `_query_facet`: `m.max_output_tokens` sumado al SELECT del JOIN que **ya
  existía** contra `model`. Cero queries nuevas, cero fuentes de verdad nuevas.

**`backend/api/chat.py`**
- Constante `_MAX_OUTPUT_TOKENS = 131072` **eliminada**; su comentario (el del
  bug de `017ba2f`: sin límite explícito un modelo de razonamiento se come el
  budget y trunca) se conservó explicando por qué el límite se sigue mandando
  siempre, y por qué el valor ahora sale de la fila.
- `_max_output_tokens_value(model, max_output_tokens) -> int`: el lector.
- `_call_openai_compat(...)`: parámetro nuevo `max_output_tokens: int | None`,
  **sin default** (un llamador que lo olvide falla con `TypeError`, no manda un
  request mudo). Ambos lectores corren ANTES de armar el body.
- `_invoke_facet`: pasa `f.max_output_tokens`.

**`backend/api/admin/models.py`**
- `max_output_tokens` sumado a `_MODEL_COLUMNS`: NULL es el estado que rompe el
  dispatch de esa fila, y un superadmin tiene que poder VER el hueco antes de
  que una faceta se caiga — no enterarse cuando se cae.

### NULL falla RUIDOSO (requisito 3)

Mismo trato exacto que `max_tokens_param`:

- `logger.error(...)` con el `UPDATE` completo (el 502 que ve el usuario trunca
  a 200 chars; el operador necesita el comando entero en el log).
- `raise ModelDispatchConfigError(...)` — se reusó la excepción del PR anterior,
  no se creó una clase nueva: es la misma clase de falla ("el catálogo no declara
  un dato que el dispatch necesita").
- **Sin default silencioso.** El mensaje además advierte explícitamente contra el
  atajo equivocado: *"NO es context_window, que es la ventana total
  entrada+salida y suele ser mucho mayor"* — el error que un operador apurado
  cometería.
- La falla corta **antes** de la llamada saliente: un modelo sin sembrar no gasta
  una llamada ni tokens para descubrir lo que el catálogo debería decir.

### Seeds (requisito 4)

| provider | model_id | max_output_tokens | facet |
|---|---|---|---|
| openai | gpt-5.6-terra | **128000** | thot |
| deepseek | deepseek-v4-flash | 131072 | jekyll |
| zhipu | glm-5.3 | 131072 | ada |
| moonshot | kimi-k3 | 131072 | kimi |

Los últimos tres van con `131072` **a propósito**: es exactamente el valor que el
código mandaba fijo y con el que funcionan hoy, así que sembrarlo es **cambio de
comportamiento CERO** para jekyll y ada. Sembrar solo el de thot los tumbaría
(NULL → fallo ruidoso). Los 4 `(provider_id, model_id)` se confirmaron presentes
en `jax_memory` por SELECT de solo lectura, así que el seed va a matchear las 4
filas en el deploy.

---

## 3. Verificación de migración desde cero (requisito 2)

Método: DROP de la columna en `jax_memory_test`, `run_migrations()` con el código
de esta rama, SELECT confirmando. **`jax_memory` (producción) no se tocó** — el
script tiene un `assert` duro sobre `SELECT DATABASE()` que aborta si no es la DB
de test.

Output real:

```
DB objetivo: jax_memory_test

=== 1. ANTES: estado inicial ===
columna: (('max_output_tokens', 'int', 'YES', 'NULL'),)

=== 2. DROP de la columna (simula DB reconstruida desde cero) ===
columna tras el DROP: ()   <- vacio = no existe

=== 3. run_migrations() con el codigo de esta rama ===
run_migrations() OK

=== 4. DESPUES: la columna existe con la forma correcta ===
  COLUMN_NAME=max_output_tokens  DATA_TYPE=int  IS_NULLABLE=YES  COLUMN_DEFAULT='NULL'

=== 5. DESPUES: filas sembradas (y las que quedan NULL a proposito) ===
  provider   model_id              context_window       max_tokens_param   max_output_tokens
  deepseek   deepseek-v4-flash             128000             max_tokens              131072
  moonshot   kimi-k3                         None             max_tokens              131072
  openai     gpt-5.5                         None                   None                None

=== 6. Idempotencia: segunda corrida de run_migrations() ===
segunda run_migrations() OK (sin error)
  deepseek   deepseek-v4-flash                 131072
  moonshot   kimi-k3                           131072
  openai     gpt-5.5                             None

=== 7. Ninguna fila sembrable quedo NULL ===
  filas del seed que siguen NULL: (ninguna)
```

Lecturas del output:
- La columna se crea **con la forma pedida**: `int`, nullable, **sin DEFAULT**
  (MariaDB reporta "sin DEFAULT" en una columna nullable como el literal `'NULL'`).
- El seed puebla las filas que existen; `gpt-5.5` (el modelo ANTERIOR de thot,
  todavía en el catálogo, fuera de la lista verificada) queda **NULL** — no se
  adivina por proveedor.
- Segunda corrida sin error y sin cambios: idempotente.
- `jax_memory_test` no tiene filas para `gpt-5.6-terra` ni `glm-5.3` (su catálogo
  es más chico que el de producción); el seed las saltea sin fallar, igual que
  hacía el del PR anterior.

---

## 4. Tests

### Nuevos: `backend/tests/test_model_max_output_tokens.py` (19 tests)

Lector:
- modelo con tope propio (128000, el caso de thot) → devuelve el suyo;
- modelo que acepta el tope viejo (131072, jekyll/ada) → lo conserva;
- **NULL → fallo ruidoso** nombrando el modelo, el `UPDATE` a correr, y la
  advertencia sobre `context_window`;
- NULL además **loguea ERROR** con el `UPDATE` (test con `caplog`);
- valores imposibles rechazados (`0`, `-1`, `"131072"`, `131072.0`, `True`).

Dispatch:
- thot manda **128000**, y se afirma que `131072` **no viaja** en ningún campo
  del body — reproducción directa del HTTP 400;
- jekyll manda **131072** bajo el nombre viejo: body byte-por-byte igual que
  antes de esta columna (guard de "cambio de comportamiento cero");
- NULL **nunca llega al proveedor** (`fake.calls == []`).

Migración/seed/plomería:
- columna existe tras `run_migrations()` con `int`/nullable/sin DEFAULT;
- seed puebla toda fila verificada presente;
- guard contra las dos regresiones probables: sembrar solo thot (deja jekyll/ada
  en NULL) o sembrar 128000 parejo (les recorta la salida);
- seed idempotente y no pisa un valor manual;
- modelos no verificados quedan NULL;
- el tope **no es derivable** de `context_window`;
- `resolve_facet("jekyll").max_output_tokens == 131072` — el dato llega del
  catálogo al despachador.

1 test se **saltea** en `jax_memory_test` (`gpt-5.6-terra` no existe en esa DB).

### Actualizados (call sites de `_call_openai_compat` / `ResolvedFacet`)

`test_chat_http_pooling.py`, `test_chat_resolved_version_capture.py` (×2),
`test_chat_usage_capture.py`, `test_chat_facet_validation.py`,
`test_admin_models_endpoints.py`, `test_model_max_tokens_param.py`.

### Suites de chat pedidas

```
$ .venv/bin/python -m pytest tests/test_chat_facet_validation.py \
    tests/test_chat_contract_wrapper.py tests/test_chat_resolved_version_capture.py \
    tests/test_chat_usage_capture.py tests/test_model_max_tokens_param.py -q
48 passed
```

### Suite completa

| | resultado |
|---|---|
| **Baseline** (`master`, antes) | `10 failed, 192 passed, 1 error` |
| **Después** (esta rama) | `10 failed, 210 passed, 1 skipped, 1 error` |

**Conjunto de fallos idéntico nombre por nombre** (10 failed + 1 error):

```
FAILED tests/test_facet_allowed_callers_migration.py::test_seed_sets_allowed_callers_for_the_4_http_facets
FAILED tests/test_facet_allowed_callers_migration.py::test_seed_does_not_overwrite_manual_value
FAILED tests/test_facet_allowed_callers_migration.py::test_seed_leaves_out_of_scope_facets_null
FAILED tests/test_facet_model_wiring.py::test_jax_local_system_prompt_states_resolved_model
FAILED tests/test_image_http_pooling.py::test_generate_image_registra_uso_con_costo_plano
FAILED tests/test_model_catalog_sync.py::test_sync_provider_models_upserts_openai_compatible_response
FAILED tests/test_model_catalog_sync.py::test_sync_provider_models_gemini_uses_models_key_and_strips_prefix
FAILED tests/test_model_catalog_sync.py::test_sync_provider_models_never_writes_facet_binding
FAILED tests/test_model_catalog_sync.py::test_sync_marks_missing_model_deprecated_after_three_consecutive_misses
FAILED tests/test_model_catalog_sync.py::test_enrich_from_models_dev_fills_metadata_without_touching_source
ERROR tests/test_websocket_isolation.py::test_reconnect_race_does_not_lose_subscription
```

Causa raíz conocida y ajena a este cambio: un pool de `aiomysql` reusado entre
event loops de asyncio. No se tocaron.

`192 → 210 passed (+18) + 1 skipped` = los 19 tests nuevos. Ningún test dejó de pasar.

---

## 5. Decisiones tomadas distinto / que vale registrar

1. **Se reusó `ModelDispatchConfigError` en vez de crear una excepción nueva.**
   Es literalmente la misma clase de falla que la del PR anterior ("el catálogo
   no declara un dato que el dispatch necesita"). Una excepción por columna
   habría obligado a cada llamador a atrapar N tipos para el mismo caso.

2. **Se eliminó la constante `_MAX_OUTPUT_TOKENS` en vez de dejarla como
   fallback.** Dejarla habría sido exactamente el default silencioso que el
   requisito 3 prohíbe: la próxima vez que alguien la usara "por las dudas",
   volvemos al incidente. Su comentario (el bug de `017ba2f`) se conservó donde
   importa, explicando por qué el límite se sigue mandando siempre.
   `tests/test_model_max_tokens_param.py` la importaba; ahora usa un
   `_LIMITE_DE_PRUEBA` local, porque esa suite es sobre el NOMBRE del parámetro
   y el valor le es indiferente.

3. **Validación de tipo además de NULL.** `max_tokens_param` tiene un ENUM que lo
   protege en la DB; `INT` no protege contra `0` ni negativos. Se rechazan valores
   no-enteros-positivos por la misma razón que el PR anterior rechaza un nombre
   fuera del ENUM: un límite inválido produce un modo de falla que se confunde con
   un error real del proveedor. Se excluye `bool` explícitamente (`True` es un
   `int` en Python y habría pasado como límite = 1).

4. **`kimi-k3` se siembra aunque no pase por `_call_openai_compat`.** Misma razón
   y misma nota que en el PR anterior: la columna describe el MODELO, no el
   despachador que lo usa. `las_manos/motor_registry/worker.py` (otro repo) queda
   intacto y todavía no lee la columna.

5. **`glm-5.3` tiene `context_window = NULL` en producción.** Hallazgo lateral del
   SELECT de verificación. No se arregló acá (fuera de alcance), pero refuerza el
   punto: aun si el tope de completion fuera derivable de la ventana — que no lo
   es — para este modelo no habría de dónde derivarlo. Candidato a DEUDA.md.

6. **La verificación contra `jax_memory` fue de SOLO LECTURA.** Un único SELECT
   para confirmar que los 4 `(provider_id, model_id)` del seed existen ahí (o sea,
   que el seed va a matchear las 4 filas en el deploy) y para confirmar el dato de
   `context_window`. Cero escrituras, cero DDL. El DROP/`run_migrations()` corrió
   solo contra `jax_memory_test`.

---

## 6. Qué falta (para el dueño)

1. **Commitear** — todo staged, el hook bloquea commits de subagente.
2. **Desplegar**: `run_migrations()` corre en el lifespan de la app, así que el
   restart de `jax-platform` crea la columna y siembra las 4 filas.
3. **Verificar thot en la Mesa web** después del deploy: es el modelo que el fix
   destraba.
