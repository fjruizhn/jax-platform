# Fix: los 2 tests de `test_chat_conv_uuid_eviction.py` que fallaban solo en el runner

Fecha: 2026-08-27
Rama: `ci/port-trigger-and-test-suite` (sobre `c6db3e8`)
Archivo tocado: `backend/tests/test_chat_conv_uuid_eviction.py` (único)

## TL;DR

La hipótesis de partida (**"dependen de estado que prepara otro test que en CI
queda skipeado"**) es **falsa, y quedó descartada con evidencia**.

La causa real es otra: los dos tests dependían de que la máquina que los corre
tenga **el repo `~/jax` clonado al lado**. No de otro test, no del orden, no de
las reglas de skip del modo `JAX_CI_NO_DB=1`.

El arreglo sigue siendo del lado de los tests, no del CI: ahora el test parchea
también `chat.MemoryDB`, o sea es dueño de **todo** el estado del que depende
`_get_conv_uuid()`, en vez de heredar un pedazo del entorno.

---

## 1. Descarte de la hipótesis "dependencia entre tests"

Primer experimento, el más barato: correr **solo ese archivo**, aislado, con la
misma variable que usa el job.

```
$ JAX_CI_NO_DB=1 JAX_JWT_SECRET=dummy python -m pytest tests/test_chat_conv_uuid_eviction.py -v
3 passed
```

Pasan aislados. Si dependieran de estado dejado por otro test, correrlos solos
sería el escenario que MÁS los rompe, y es el que los deja verdes. Además no
hay `pytest-randomly` instalado (`plugins: anyio, asyncio`), así que tampoco
hay un orden aleatorio que explique nada.

Con eso la hipótesis queda descartada: el `assert [] == [...]` no viene de
"faltó lo que preparaba otro test".

## 2. La causa raíz real, probada

`api/chat.py` importa `MemoryDB` de **otro repo**, opcional y por ruta absoluta
del home:

```python
sys.path.insert(0, os.path.expanduser("~/jax"))
try:
    from jax.memory.db import MemoryDB
except Exception:
    MemoryDB = None
```

Y el primer guard de `_ensure_memory()` es justamente ese:

```python
async def _ensure_memory() -> bool:
    global _memory, _memory_ready
    if MemoryDB is None:
        return False                      # <-- corta ACÁ
    if _memory_ready and _memory and _memory.is_connected:
        return True
    ...
```

Los tests parcheaban `_memory` y `_memory_ready`, **pero no `MemoryDB`**. En
esta máquina `~/jax` existe, así que `MemoryDB` es una clase, el guard no corta
y todo funciona. En un runner de GitHub Actions solo está checkouteado
`jax-platform`: `~/jax` no existe → `MemoryDB is None` → `_ensure_memory()`
devuelve `False` **antes** de mirar los parches del test → `_get_conv_uuid()`
devuelve `None` sin tocar nada → la caché queda vacía.

Eso da exactamente los dos síntomas reportados: `assert [] == ['1:None', ...]`
y `assert None == 'conv-1'`. Y explica por qué el tercer test del archivo
(`test_concurrent_evictions...`) sí pasaba en CI: ese no llama a
`_get_conv_uuid()`, escribe `_conv_uuids` a mano y llama al evictor directo, así
que nunca pasa por `_ensure_memory()`.

### Evidencia A — el mecanismo, aislado del test runner

Mismo árbol, mismo intérprete, lo único que cambia es `HOME`:

```
HOME=/home/fruiz            (esta máquina)
  MemoryDB = <class 'jax.memory.db.MemoryDB'>
  _get_conv_uuid -> conv-1

HOME=<dir vacío>            (equivalente al runner: sin ~/jax)
  MemoryDB = None
  _get_conv_uuid -> None
```

### Evidencia B — reproducción exacta del runner, en local

Suite completa, `JAX_CI_NO_DB=1`, con `HOME` apuntando a un directorio vacío:

```
FAILED tests/test_chat_conv_uuid_eviction.py::test_evicts_the_least_recently_used_conversation_past_the_cap
FAILED tests/test_chat_conv_uuid_eviction.py::test_a_cache_hit_does_not_start_a_new_conversation_or_evict_anything
2 failed, 68 passed, 151 skipped
```

`2 failed / 68 passed / 151 skipped` — los mismos números, los mismos dos
nombres, las mismas dos aserciones que reportó GitHub Actions. La ausencia de
`~/jax` es la diferencia completa entre local y el runner. No hace falta ninguna
otra variable para explicar el rojo.

### Por qué la "simulación fiel del runner" del port anterior no lo detectó

`docs/ci-port-report.md` sí simuló el runner: venv limpia, sin `/etc/jax/.env`,
sin DB, con `JAX_JWT_SECRET` dummy → `70 passed`. Lo que esa simulación **no**
variaba era `HOME`, y por lo tanto seguía viendo `~/jax` de esta máquina. La
simulación cubría dependencias declaradas (paquetes, env, DB) pero no una
dependencia **implícita de filesystem del home del usuario**. Anotado como
lección: al simular un runner limpio, `HOME` también es parte del entorno.

## 3. Qué cambié

Un solo archivo: `backend/tests/test_chat_conv_uuid_eviction.py`.

- Nuevo fixture `fake_memory` que deja `api.chat` con memoria falsa pero
  **completa**: parchea `MemoryDB` (el que faltaba), `_memory` y `_memory_ready`
  vía `monkeypatch` (restauración automática), y limpia `_conv_uuids` al entrar
  **y** al salir. El comentario del fixture documenta por qué los tres parches
  hacen falta y no dos.
- Los tres tests toman ese fixture y pierden su preámbulo duplicado y sus
  `try/finally` a mano (el fixture ya garantiza la limpieza aunque el test
  falle). `MAX_TRACKED_CONVERSATIONS` se sigue parcheando por test, porque el
  valor es parte de lo que cada test quiere probar.

Lo que **no** cambié, a propósito:

- **Ninguna aserción.** Están idénticas, incluidas las de orden de la LRU y la
  de que se cierra `conv-2` antes de descartarla. La cobertura es la misma o
  mayor: antes, en cualquier máquina sin `~/jax`, esos asserts no probaban la
  caché LRU sino la presencia de un repo vecino.
- **Nada del CI.** Ni el workflow, ni `conftest.py`, ni `JAX_CI_MIN_PASSED`, ni
  skips/xfails. Las dos reglas estructurales del modo sin-DB quedan intactas: no
  eran la causa y no tocaba acomodarlas.
- **Nada de producción.** `api/chat.py` no se tocó: que `MemoryDB is None`
  degrade el chat a "sin memoria" es el comportamiento deliberado y documentado.

## 4. Verificación

Intérprete: `backend/.venv/bin/python` (3.14.4, el de producción).
"Sim runner" = `HOME` apuntando a un directorio vacío, o sea sin `~/jax`.

### Los 2 tests, aislados — las 4 combinaciones

| # | Escenario | Antes | Después |
|---|---|---|---|
| A | archivo aislado, `JAX_CI_NO_DB=1`, HOME real | 3 passed | **3 passed** |
| B | archivo aislado, sin `JAX_CI_NO_DB`, HOME real | 3 passed | **3 passed** |
| C | archivo aislado, `JAX_CI_NO_DB=1`, sim runner | **2 failed** / 1 passed | **3 passed** |
| D | archivo aislado, sin `JAX_CI_NO_DB`, sim runner | **2 failed** / 1 passed | **3 passed** |

### Dentro de la suite completa

| # | Escenario | Antes | Después |
|---|---|---|---|
| E | suite completa, con DB, HOME real | 10 failed / 210 passed / 1 skipped / 1 error | **10 failed / 210 passed / 1 skipped / 1 error** |
| F | suite completa, `JAX_CI_NO_DB=1`, HOME real | 70 passed / 151 skipped | **70 passed / 151 skipped** |
| G | suite completa, `JAX_CI_NO_DB=1`, **sim runner** = el job | **2 failed / 68 passed** / 151 skipped | **70 passed / 151 skipped / 0 failed** |
| H | scanner P10 (`test_no_fail_open_except.py`) | 1 passed | **1 passed** |

**Comparación por NOMBRES, no por número** (E): los `FAILED`/`ERROR` de la suite
con DB se volcaron ordenados antes y después del cambio y se diffearon. Diff
**vacío** — el mismo conjunto de 10 failures + 1 error preexistentes, ninguno
nuevo, ninguno enmascarado. (Se comparan nombres justamente porque el número no
es estable: depende del estado de `jax_memory_test`, que es compartida.)

**Determinismo de (G):** 3 corridas consecutivas, `70 passed / 151 skipped` las
tres. Sin `pytest-randomly` instalado, el orden no varía.

### Piso de cobertura

`JAX_CI_MIN_PASSED=70` queda como está y ahora **se cumple de verdad en el
runner**. Vale notar que antes de este fix el runner daba 68 passed: el paso de
piso también habría fallado, no solo los 2 tests. Con el fix, 70.

## 5. Cabo suelto (no accionado, para decidir)

`api/chat.py` hace `sys.path.insert(0, os.path.expanduser("~/jax"))` en import
time: acopla el backend al layout del home de un usuario concreto. Funciona en
hall9000 y se degrada elegante en cualquier otro lado, así que no es un bug —
pero es la razón por la que un test pudo depender del filesystem sin que se
notara. Convertirlo en una env var (`JAX_CORE_PATH`) sería la forma de que esa
dependencia sea explícita. Fuera de alcance de este fix; queda anotado.
