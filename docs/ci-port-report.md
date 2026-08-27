# CI de jax-platform: port del trigger + suite de tests en CI

Fecha: 2026-08-27
Rama: `ci/port-trigger-and-test-suite` (rebasada sobre `master` = 35105ae)

## Resumen

Dos cosas:

1. Portado el trigger nuevo desde `jax` (`branches: ["**"]` + `types` con
   `edited`) más el bloque `concurrency`.
2. Agregado un job que corre la suite del backend. Da señal real y verificada:
   70 tests, determinista, y se pone en rojo ante una regresión real (probado
   con mutación deliberada).

El scanner `no-fail-open-except` queda exactamente igual.

---

## 1. Trigger y concurrency

Antes:

```yaml
on:
  push:
    branches: [master]
  pull_request:
    branches: [master]
```

Ahora (idéntico a `jax/.github/workflows/policy.yml`):

```yaml
on:
  push:
    branches: ["**"]
  pull_request:
    branches: ["**"]
    types: [opened, synchronize, reopened, edited]
```

Razón, portada como comentario al YAML: con `branches: [master]`, cualquier PR
de una cadena apilada (rama cuya base NO es master) se quedaba sin CI. En `jax`
se descubrió en vivo con el PR #27, que tuvo 0 runs hasta retargetearlo a mano.
`edited` hace falta porque GitHub no dispara `pull_request` con los tipos
default cuando se retarget-ea un PR existente — sin eso, ni siquiera el retarget
que resuelve el problema dispara una corrida.

Añadido también el bloque `concurrency` con `cancel-in-progress: true`.

---

## 2. Qué descubrí probando el comportamiento real de la suite

Todo medido contra el árbol limpio de `master`, en un worktree aparte. Nada de
esto es supuesto. Las primeras mediciones fueron sobre 6800a32; al terminar,
`master` avanzó a 35105ae (PR #18) y se rebasó y volvió a medir todo.

| Escenario | Resultado |
|---|---|
| Suite tal cual, **con** MariaDB local | en 6800a32: `10 failed / 192 passed / 1 error` … y más tarde `12 failed / 190 passed / 1 error`. En 35105ae: `12 failed / 208 passed / 1 error` |
| Suite tal cual, **sin** MariaDB (lo que pasaría en el runner) | `3 failed / 61 passed / 138 errors` |
| Sin MariaDB **y** sin `/etc/jax/.env` (el runner de verdad) | **No colecta**: `28 errors during collection` |
| Modo `JAX_CI_NO_DB=1` (lo que se agrega acá) | `70 passed / 151 skipped / 0 failed / 0 errors` |

Hallazgos, en orden de importancia:

**a) El baseline "10 failures preexistentes" no es estable.** Corriendo el
MISMO árbol limpio con el conftest SIN modificar, primero dio 10 failures y un
rato después 12 — tres corridas seguidas de cada número. La causa es que la
suite comparte una DB mutable (`jax_memory_test`) y arrastra estado entre
corridas; mientras yo medía, la sesión principal corría tests con su propio
trabajo de migraciones sin commitear contra esa misma DB.

Esto invalida por construcción la opción "baseline versionado de failures
conocidos": el conjunto a comparar no es una constante, es función del estado
de una DB compartida. Un archivo de baseline habría empezado a mentir el mismo
día.

**b) Sin DB, la suite no es "un poco peor", es inservible**: 138 errores. Y los
138 salen de **un solo punto** — el fixture `client` de `tests/conftest.py:33`,
que levanta el lifespan de la app y ahí abre el pool. Los otros 3 fallos llegan
al pool directo con `get_pool()`. O sea: dos caminos, un mismo hecho
("este test necesita la DB"), ambos detectables mecánicamente.

**c) La dependencia de DB corta POR DENTRO de los archivos, no entre ellos.**
`test_chat_contract_wrapper.py` tiene 14 tests que pasan sin DB y 3 que no;
`test_model_max_tokens_param.py`, 4 y 9. Así que "correr solo los archivos sin
DB" no era separable de forma limpia — habría exigido la lista a mano que
justamente no queríamos.

**d) El runner necesita `JAX_JWT_SECRET` o no colecta nada.** `auth/jwt.py`
falla cerrado en import time (buen comportamiento) y tumba la colección entera
con 28 errores. Un dummy alcanza; el job no necesita ningún secret real.

**e) `pytest-asyncio` no está en `requirements.txt`** aunque `pytest.ini` lo
exige (`asyncio_mode = auto`). Se instala explícito en el workflow. Deuda
anotada: debería vivir en un `requirements-dev.txt`.

**f) La venv real es Python 3.14.4, no 3.12.** `systemd jax-platform` arranca
`backend/.venv/bin/uvicorn`, que es 3.14.4. El `CLAUDE.md` dice 3.12. El job de
tests se pinnea a 3.14 (lo que corre en producción y lo único que verifiqué);
el scanner queda en 3.12 como estaba, para no tocarlo.

---

## 3. El diseño elegido, y por qué descarté los otros

### Elegido: modo `JAX_CI_NO_DB=1`, dos reglas estructurales

Implementado en `backend/tests/conftest.py`. **No es una lista de tests
deseleccionados.** Son dos reglas, y las dos dicen lo mismo — "si el test
necesita la DB y no hay DB, saltá con motivo visible; si no la necesita, corré
y exigí el resultado":

- **Regla 1 (colección):** el test pide el fixture `client` → skip.
- **Regla 2 (ejecución):** el test llega a `aiomysql.create_pool()` por el
  camino que sea → skip. Se engancha en el cuello de botella real de conexión,
  así que no depende de cómo cada módulo importe `get_pool`.

La propiedad que importa: **se mantiene sola**. Un test nuevo que no toca la DB
queda cubierto por CI automáticamente, sin que nadie lo agregue a ninguna
lista. Uno que sí la toca se salta solo y aparece contado como skip.

Esto quedó demostrado por accidente durante la propia sesión: mientras yo
trabajaba, `master` avanzó con el PR #18, que agrega tests nuevos. Al rebasar,
el job pasó de 61 a **70** tests cubiertos sin tocar una sola línea de
configuración — los 9 tests nuevos sin DB entraron solos. El único ajuste fue
subir el piso de 61 a 70.

Verificaciones hechas:

- **Determinismo:** 7 corridas, siempre `70 passed / 151 skipped` (`61 / 141`
  antes del rebase). Idéntico con
  la DB apagada y con la DB encendida → el estado de la DB no puede alterar el
  veredicto del job.
- **Inocuidad:** con el conftest modificado y sin modificar, la corrida local
  normal (con DB) da exactamente lo mismo (`12 failed / 190 passed / 1 error`
  en ambos casos, tres corridas de cada uno). El cambio no altera el flujo de
  trabajo local.
- **No miente:** quité a mano el guard `uuid.UUID(task_id)` de `api/command.py`
  (el que frena el path traversal) y el job se puso en rojo con
  `1 failed`. Detecta regresión real. Archivo restaurado después.
- **Simulación fiel del runner:** venv limpia creada solo con
  `requirements.txt + pytest + pytest-asyncio`, sin `/etc/jax/.env`, sin DB, con
  `JAX_JWT_SECRET` dummy → `70 passed / 151 skipped`.

Además, un paso final de **piso de cobertura** (`JAX_CI_MIN_PASSED=70`) que
falla si el número de passed baja. Existe específicamente por el precedente del
scanner P10 en `jax`, que estuvo verde meses reportando "1 passed" sobre cero
archivos escaneados: si el conftest se rompe y todo termina saltado, o si
alguien convierte tests sin-DB en tests con-DB, el job se pone rojo en vez de
mentir en verde. Probado en ambos sentidos (piso respetado → exit 0; piso roto
→ exit 1).

### Descartado: servicio MariaDB en el runner (`services:`)

Era la opción más honesta en cobertura (100% de la suite) y la que más me
gustaba. La descarté porque **no pude verificarla desde esta máquina**, y
mandarla sin verificar es exactamente el modo de fallo que el proyecto viene
cerrando:

- `docker` está instalado pero no es usable por este usuario, así que no pude
  levantar un MariaDB local que imitara al service container.
- `jax_user` sólo tiene grants sobre `jax_memory` y `jax_memory_test`
  (`SHOW GRANTS` verificado); no puede crear una DB vacía de prueba.
- La única forma de conseguir una DB vacía era dropear `jax_memory_test`, que
  es compartida y estaba siendo usada en ese momento por la sesión principal.
  No lo hice.

Sin una DB vacía no podía responder la pregunta decisiva: **cuántos tests
fallan realmente contra un esquema recién creado por `run_migrations()`**. Y
sabiendo el hallazgo (a) — que el número de failures se mueve con el estado de
la DB — asumir "van a ser los mismos 10" habría sido justo la suposición que no
corresponde hacer. El job habría quedado permanentemente rojo, o verde con un
baseline inventado.

Lo que sí quedó verificado a favor de esa opción, para quien la retome: las 22
sentencias `CREATE TABLE` de `db/migrations.py` son todas
`IF NOT EXISTS`, y el lifespan de la app corre `run_migrations()` + `run_seed()`,
así que construir el esquema desde vacío es plausible. Falta medirlo.

### Descartado: baseline versionado de failures

Inviable por el hallazgo (a): el conjunto de failures no es estable ni
determinista, depende del estado de una DB compartida. Un baseline así habría
necesitado mantenimiento constante y habría normalizado "la suite está un poco
roja", que es el anti-patrón a evitar.

### Descartado: correr sólo los archivos sin DB

Inviable por el hallazgo (c): la dependencia corta por dentro de los archivos.
Habría requerido una lista de tests a mano.

---

## 4. Qué queda explícitamente SIN cubrir

Está declarado también en comentarios del YAML, para que se lea desde el propio
workflow y no sólo acá.

- **Los 151 tests que necesitan MariaDB** (~68% de la suite): endpoints admin,
  motores, pipelines, shadow validation, migraciones, seed, usage/pricing. Se
  saltan con motivo visible; `-rs` los lista uno por uno en el log.
- **Los 10-12 fallos preexistentes** por el pool de aiomysql reusado entre event
  loops. Fuera de alcance, no tocados. Siguen invisibles para CI porque los
  tests que los exhiben son justamente los que requieren DB.
- **El frontend**: no hay ningún job de frontend en este repo.
- **Todo lo que dependa de credenciales reales**: `/etc/jax/.env` no existe en
  el runner.
- **Que el job corra en Python 3.14 en GitHub Actions**: verifiqué la suite en
  3.14 localmente, pero no pude confirmar desde acá que
  `actions/setup-python@v5` sirva 3.14 en el runner. Es lo único del workflow
  que no pude verificar de forma directa; si fallara, es un cambio de una línea.

---

## 5. Archivos tocados

- `.github/workflows/policy.yml` — trigger, concurrency, job nuevo, doc de alcance.
- `backend/tests/conftest.py` — modo `JAX_CI_NO_DB=1` (inerte sin esa variable).
- `.gitignore` — `junit.xml`.
- `docs/ci-port-report.md` — este archivo.
