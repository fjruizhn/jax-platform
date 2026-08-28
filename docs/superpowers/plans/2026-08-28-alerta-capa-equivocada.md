# La alerta afirma la capa equivocada — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que la alerta de un facet caído nombre la causa accionable
(`provider_error`, `config_error`) en vez de `probe_error` — eliminando la
escritura redundante de `probe_facet`, protegida por un test de política que
la vuelve mecánica.

**Architecture:** `probe_facet` deja de registrar `probe_error`, porque
`_invoke_facet` —un envoltorio total— ya registró el evento clasificado
antes de re-lanzar. `probe_after_rebind` sigue registrándolo: ahí sí es el
único evento. La propiedad de la que esto depende ("`_invoke_facet` nunca
lanza sin registrar") deja de ser tácita y pasa a estar protegida por un
test de política sobre el AST, que corre en CI.

**Tech Stack:** Python 3.14 (`backend/.venv`, la que corre en producción),
pytest, `ast` de la stdlib, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-28-alerta-capa-equivocada-design.md`

## Global Constraints

- **Repo:** todo el cambio es en `jax-platform`. `jax` no se toca.
- **Orden no negociable: Task 1 ANTES de Task 2.** Primero el guard,
  después la eliminación. Si el guard entrara después, habría una ventana
  en la que la opción B depende de una propiedad no protegida.
- **El test de política corre en CI desde la task que lo introduce**, no al
  final, y se verifica **rompiéndolo con el job real** — no localmente
  (octava lección de método, `jax/CONTEXT.md` §9).
- **Ninguna capa se vuelve fail-open.** `_invoke_facet` sigue registrando
  lo que clasifica y re-lanzando. `probe_after_rebind` sigue produciendo un
  evento cuando falla. Sólo se elimina la escritura de `probe_facet`, que
  está probado que nunca aporta (spec §4).
- **No se toca:** `MAX(ts)` ni la máquina de estados del lector (repo
  `jax`), `record_facet_health`, el `except` de `probe_after_rebind`, ni el
  texto de la alerta.
- **Piso de cobertura:** `JAX_CI_MIN_PASSED` en
  `.github/workflows/policy.yml` está hoy en `108`. Línea base medida:
  `JAX_CI_NO_DB=1 .venv/bin/python -m pytest -q` → **108 passed, 151
  skipped**. Cada task que cambie el conteo actualiza el piso **y** deja
  escrito en el comentario del YAML por qué cambió, siguiendo el formato de
  las subidas anteriores.
- **"Expected" incumplido → PARÁ y reportá.** Si una salida no coincide con
  el `Expected` del step, no ajustes el código para que coincida: reportá
  la discrepancia. El brief de una ronda anterior tenía una predicción
  incorrecta y el implementador modificó código de producción para
  satisfacerla.
- **Comandos:** todo desde `/home/fruiz/jax-platform/backend`. El intérprete
  es `./.venv/bin/python` (3.14). Para la suite completa con DB:
  `set -a && . /etc/jax/.env && set +a && export JAX_DB_NAME=jax_memory_test`.

## File Structure

| Archivo | Responsabilidad | Task |
|---|---|---|
| `backend/tests/test_policy_invoke_facet_envoltorio.py` | **Crear.** Test de política: `_invoke_facet` es un envoltorio total. Contiene el analizador AST (función pura sobre un string de código) y su auto-verificación con las 9 mutaciones. | 1 |
| `.github/workflows/policy.yml` | **Modificar.** Job nuevo `invoke-facet-envoltorio` + piso de cobertura. | 1, 2 |
| `backend/jax_engine/facet_canary.py:96-102` | **Modificar.** `probe_facet` deja de registrar `probe_error`. | 2 |
| `backend/tests/test_facet_canary.py:172-182` | **Modificar.** El test que hoy afirma que `probe_facet` registra pasa a afirmar que NO registra. | 2 |

**Por qué el analizador vive en el archivo de test y no en un módulo
aparte:** mismo patrón que `tests/test_no_fail_open_except.py`, el otro test
de política del repo — el analizador es una función pura y su auto-test vive
al lado. No es código de producción y no debe ser importable desde `api/`.

---

## Task 1 — El guard: test de política + job de CI

**Files:**
- Create: `backend/tests/test_policy_invoke_facet_envoltorio.py`
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces:
  ```python
  def violaciones(codigo: str) -> list[str]
  # Recibe el CODIGO FUENTE como string (no un path) para que los tests de
  # mutacion no tengan que escribir archivos. Devuelve [] si _invoke_facet
  # es un envoltorio total; una lista de motivos legibles si no.
  ```
  Task 2 no lo consume — sólo depende de que el job exista y esté verde.

- [ ] **Step 1: Escribir el test de política completo**

Crear `backend/tests/test_policy_invoke_facet_envoltorio.py` con este
contenido exacto:

```python
"""Politica: `_invoke_facet` (api/chat.py) es un ENVOLTORIO TOTAL.

Que protege, y por que existe
-----------------------------
`probe_facet` (jax_engine/facet_canary.py) NO registra `probe_error`
cuando `_invoke_facet` lanza, porque `_invoke_facet` ya registro el evento
clasificado (`provider_error`, `config_error`, ...) antes de re-lanzar.
Esa decision -- opcion B del diseno 2026-08-28 -- depende por completo de
una propiedad estructural: **cuando `_invoke_facet` lanza, SIEMPRE
escribio antes**.

Sin este test esa propiedad seria TACITA, y una garantia tacita es peor
que la duplicacion que reemplaza: cuando se rompe, no avisa. Se romperia
en silencio el dia que alguien agregue una linea fuera del `try`, un
decorador, o quite el `except Exception` generico.

No verifica que hoy este bien: DETECTA LA MUTACION. Los tests de abajo
mutan el codigo a proposito de 9 formas y exigen que cada una sea
detectada. Dos de esas 9 (quitar el handler generico, y un archivo que no
parsea) salieron de atacar ESTE TEST, no el codigo: la primera version las
dejaba pasar en verde. Por eso el handler generico como ULTIMO handler es
un requisito del diseno y no una preferencia de estilo -- sin el, una
excepcion no prevista escapa sin registrar, que es exactamente la
propiedad que este archivo existe para proteger.

Que NO cubre (limite declarado, no se amplia a proposito)
--------------------------------------------------------
Es estatico sobre `_invoke_facet` y solo sobre ella. NO verifica que
`_invoke_facet_dispatch` -- la que hace el trabajo real -- no tenga su
propio fail-open adentro. Eso lo cubren `test_no_fail_open_except.py`
(scanner P10, en CI sobre todo el arbol) y `test_facet_health_outcomes.py`
(comportamiento de los puntos de retorno reales). Un guard que intenta
cubrir dos funciones distintas termina cubriendo mal las dos.

Tampoco verifica que cada handler registre el outcome CORRECTO (que
ModelDispatchConfigError escriba config_error y no ok); eso es
comportamiento y lo cubren los tests de outcomes.

Ver docs/superpowers/specs/2026-08-28-alerta-capa-equivocada-design.md
"""
from __future__ import annotations

import ast
from pathlib import Path

FUNCION = "_invoke_facet"
REGISTRO = "record_facet_health"
ARCHIVO = Path(__file__).resolve().parents[1] / "api" / "chat.py"


def _nombre_llamada(call: ast.Call) -> str | None:
    return getattr(call.func, "id", getattr(call.func, "attr", None))


def _registra(nodo: ast.AST) -> bool:
    return any(isinstance(c, ast.Call) and _nombre_llamada(c) == REGISTRO
               for c in ast.walk(nodo))


def violaciones(codigo: str) -> list[str]:
    """Devuelve [] si _invoke_facet es un envoltorio total; motivos si no."""
    try:
        arbol = ast.parse(codigo)
    except SyntaxError as e:
        return [f"el archivo no parsea ({e.msg}, linea {e.lineno}): "
                f"el test no puede verificar nada"]

    fn = next((n for n in ast.walk(arbol)
               if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
               and n.name == FUNCION), None)
    if fn is None:
        return [f"{FUNCION} no existe (renombrada?): la politica no aplica "
                f"a nada y eso ya es una violacion"]

    v: list[str] = []
    if fn.decorator_list:
        v.append(f"tiene {len(fn.decorator_list)} decorador(es): pueden "
                 f"lanzar antes de que el cuerpo corra, sin registrar")

    cuerpo = list(fn.body)
    if (cuerpo and isinstance(cuerpo[0], ast.Expr)
            and isinstance(cuerpo[0].value, ast.Constant)
            and isinstance(cuerpo[0].value.value, str)):
        cuerpo.pop(0)                                  # docstring

    tries = [s for s in cuerpo if isinstance(s, ast.Try)]
    if len(tries) != 1:
        v.append(f"el cuerpo tiene {len(tries)} bloques try en el nivel "
                 f"superior, se espera exactamente 1")
        return v

    t = tries[0]
    i = cuerpo.index(t)
    if i != 0:
        v.append(f"hay {i} sentencia(s) ANTES del try: pueden lanzar sin "
                 f"registrar")

    for s in cuerpo[i + 1:]:
        if not (isinstance(s, ast.Return) or _registra(s)):
            v.append(f"sentencia en linea {s.lineno} DESPUES del try que no "
                     f"registra ni es return")

    for h in t.handlers:
        etiq = (getattr(h.type, "id", getattr(h.type, "attr", "?"))
                if h.type else "bare")
        if not _registra(h):
            v.append(f"handler {etiq} (linea {h.lineno}) NO registra antes "
                     f"de propagar")
        if not any(isinstance(c, ast.Raise) for c in ast.walk(h)):
            v.append(f"handler {etiq} (linea {h.lineno}) NO re-lanza: seria "
                     f"fail-open")

    ultimo = t.handlers[-1] if t.handlers else None
    generico = ultimo is not None and (
        ultimo.type is None or getattr(ultimo.type, "id", None) == "Exception")
    if not generico:
        v.append("no hay `except Exception` (ni bare) como ULTIMO handler: "
                 "una excepcion no prevista escaparia sin registrar")
    return v


# --------------------------------------------------------------------------
# El caso real
# --------------------------------------------------------------------------

def test_el_codigo_real_cumple_la_politica():
    assert violaciones(ARCHIVO.read_text()) == []


# --------------------------------------------------------------------------
# Auto-verificacion: las 9 mutaciones tienen que ser DETECTADAS.
# Sin estos tests, `violaciones()` podria devolver [] siempre y el guard
# seria un no-op verde -- el patron exacto del scanner P10 verde sobre cero
# archivos (CONTEXT.md, primera leccion de metodo).
# --------------------------------------------------------------------------

_BASE = '''
async def _invoke_facet(facet, config, user_id, message, ctx=None, *, source="chat"):
    """docstring"""
    try:
        texto, usage, outcome = await _invoke_facet_dispatch(facet, config)
    except ModelDispatchConfigError as e:
        await record_facet_health(facet, OUTCOME_CONFIG_ERROR, source, str(e))
        raise
    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
    await record_facet_health(facet, outcome, source)
    return texto, usage
'''


def test_la_base_del_arnes_cumple():
    """Si la base no pasara, los tests de mutacion probarian otra cosa."""
    assert violaciones(_BASE) == []


def test_detecta_sentencia_antes_del_try():
    m = _BASE.replace("    try:", "    f = await _resolver(facet)\n    try:")
    assert any("ANTES del try" in x for x in violaciones(m))


def test_detecta_decorador():
    m = "@con_reintento\n" + _BASE.lstrip("\n")
    assert any("decorador" in x for x in violaciones(m))


def test_detecta_with_envolviendo_el_try():
    cuerpo = _BASE.split('"""docstring"""')[1]
    indentado = "\n".join(("  " + l if l.strip() else l)
                          for l in cuerpo.split("\n"))
    m = _BASE.split('"""docstring"""')[0] + '"""docstring"""\n    async with _sem:' + indentado
    assert any("bloques try" in x for x in violaciones(m))


def test_detecta_return_temprano():
    m = _BASE.replace("    try:", '    if facet == "hyde":\n        return "", None\n    try:')
    assert any("ANTES del try" in x for x in violaciones(m))


def test_detecta_handler_que_no_registra():
    m = _BASE.replace(
        "        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))\n", "")
    assert any("NO registra" in x for x in violaciones(m))


def test_detecta_handler_que_no_relanza():
    m = _BASE.replace(
        "        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))\n        raise",
        "        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))\n        return \"\", None")
    assert any("NO re-lanza" in x for x in violaciones(m))


def test_detecta_delegacion_a_otra_funcion():
    m = '''
async def _invoke_facet(facet, config, user_id, message, ctx=None, *, source="chat"):
    """docstring"""
    return await _hacer_todo(facet, config, source)
'''
    assert any("bloques try" in x for x in violaciones(m))


def test_detecta_falta_del_except_generico():
    """El hueco que aparecio atacando este mismo test, no el codigo.

    Sin `except Exception`, una excepcion no prevista escapa de
    _invoke_facet SIN registrar -- y esa es exactamente la propiedad de la
    que depende que probe_facet ya no escriba probe_error."""
    m = _BASE.replace('''    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
''', "")
    assert any("ULTIMO handler" in x for x in violaciones(m))


def test_detecta_archivo_que_no_parsea():
    assert any("no parsea" in x for x in violaciones("def roto(:\n"))


def test_detecta_funcion_renombrada_o_ausente():
    assert any("no existe" in x for x in violaciones("def otra_cosa():\n    pass\n"))
```

- [ ] **Step 2: Correr el test y verificar que los 12 pasan**

Run:
```bash
cd /home/fruiz/jax-platform/backend
./.venv/bin/python -m pytest tests/test_policy_invoke_facet_envoltorio.py -v
```
Expected: **12 passed** (1 del código real + 1 de la base del arnés + 10 de
detección). Este número no es una estimación: el archivo de arriba se corrió
tal cual durante la escritura del plan y dio 12 passed contra el
`api/chat.py` real.

**Nota sobre TDD:** este archivo no sigue el ciclo rojo→verde clásico,
porque un test de política nace verde sobre código que ya cumple. El
equivalente real del "rojo" son los 10 tests de detección: si `violaciones()`
devolviera `[]` siempre (un no-op verde, el patrón del scanner P10), esos 10
fallarían. Son la prueba de que el guard hace trabajo. **Si alguno de los 12
falla, PARÁ y reportá** — no ajustes el analizador para que pase.

- [ ] **Step 3: Verificar el conteo de la suite sin DB**

Run:
```bash
cd /home/fruiz/jax-platform/backend
set -a && . /etc/jax/.env && set +a
JAX_CI_NO_DB=1 ./.venv/bin/python -m pytest -q 2>&1 | tail -2
```
Expected: **120 passed, 151 skipped** (108 previos + 12 nuevos; el skip
count NO cambia). Si el número difiere, **PARÁ y reportá** — no toques el
piso para que cuadre.

- [ ] **Step 4: Agregar el job de CI y subir el piso**

En `.github/workflows/policy.yml`, agregar este job **antes** de
`backend-tests-no-db`, siguiendo el patrón de `no-fail-open-except`:

```yaml
  # La opcion B del diseno 2026-08-28 (probe_facet deja de escribir
  # probe_error) depende de que _invoke_facet sea un envoltorio total:
  # cuando lanza, YA registro. Sin este job esa propiedad seria tacita, y
  # una garantia tacita es peor que la duplicacion que reemplaza -- cuando
  # se rompe, no avisa. El test se auto-verifica con 9 mutaciones.
  invoke-facet-envoltorio:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pytest
      - run: python -m pytest tests/test_policy_invoke_facet_envoltorio.py -v
```

**`pip install pytest` alcanza acá y no hace falta `requirements.txt`** —
**verificado, no supuesto**: el test sólo usa `ast` y `pathlib` de la
stdlib y lee `api/chat.py` como TEXTO, sin importarlo, así que no arrastra
fastapi, aiomysql ni pydantic. Comprobado en un venv limpio con sólo pytest
durante la escritura de este plan: **12 passed**. Es el modo de falla que
casi se comió el job de la Task 7 de la ronda anterior (`ModuleNotFoundError`
en la colección), así que se cerró por adelantado.

Y en el job `backend-tests-no-db`, subir el piso agregando al comentario
existente (sin borrar el historial de subidas anteriores):

```yaml
      # Subido de 108 a 120 en Task 1 del plan de la alerta que afirma la
      # capa equivocada (2026-08-28): backend/tests/test_policy_invoke_facet_envoltorio.py
      # agrega 12 tests sin DB (1 del codigo real, 1 de la base del arnes,
      # 10 de deteccion de mutaciones), medido con
      # JAX_CI_NO_DB=1 .venv/bin/python -m pytest -q antes/despues --
      # skip count sin cambios (151).
      JAX_CI_MIN_PASSED: "120"
```

- [ ] **Step 5: Commit**

```bash
cd /home/fruiz/jax-platform
git add backend/tests/test_policy_invoke_facet_envoltorio.py .github/workflows/policy.yml
git commit -m "test(policy): _invoke_facet es un envoltorio total, verificado por mutacion

Guard de la opcion B: probe_facet va a dejar de escribir probe_error
porque _invoke_facet ya registro antes de re-lanzar. Sin este test esa
propiedad seria tacita, y una garantia tacita no avisa cuando se rompe.

Se auto-verifica con 9 mutaciones. Dos de ellas (quitar el except
Exception generico, archivo que no parsea) salieron de atacar el test, no
el codigo: la primera version las dejaba pasar en verde."
```

- [ ] **Step 6: Verificar el job ROMPIÉNDOLO con el job real — criterio de cierre**

Octava lección de método: que aparezca en el YAML y salga verde no alcanza.
**No se verifica localmente.**

```bash
cd /home/fruiz/jax-platform
git push -u origin feat/alerta-capa-correcta
gh pr create --base master \
  --title "fix(health): la alerta nombra la causa, no la capa (Task 1/2 — guard)" \
  --body "Task 1 del plan \`docs/superpowers/plans/2026-08-28-alerta-capa-equivocada.md\`. Guard primero: el test de politica que hace mecanica la propiedad de la que depende Task 2."
```

Después, en un commit temporal, romper el código real a propósito y
confirmar que **`invoke-facet-envoltorio` se pone rojo en CI**:

```bash
cd /home/fruiz/jax-platform/backend
python3 - <<'EOF'
p='api/chat.py'; s=open(p).read()
s=s.replace("""    try:
        texto, usage, outcome = await _invoke_facet_dispatch(""","""    _ = await _algo_que_puede_lanzar(facet)
    try:
        texto, usage, outcome = await _invoke_facet_dispatch(""")
open(p,'w').write(s)
EOF
cd /home/fruiz/jax-platform
git commit -aqm "test: ROMPER A PROPOSITO -- verificar que invoke-facet-envoltorio se pone rojo"
git push
```

Esperar el resultado del job (`gh pr checks <N> --watch`) y confirmar que
`invoke-facet-envoltorio` dice **fail**, y que **falló por la violación y no
por colección** — revisar el log:

```bash
gh run view --job <ID> --log-failed | grep -E "ANTES del try|Error"
```
Expected: la línea `hay 1 sentencia(s) ANTES del try`. Si en cambio hay un
`ModuleNotFoundError`, el job está fallando por dependencias y **no probó
nada**: PARÁ y reportá (es el modo de falla que casi pasa en la Task 7 de la
ronda anterior).

Revertir:
```bash
cd /home/fruiz/jax-platform
git revert --no-edit HEAD
git push
```
Expected: `invoke-facet-envoltorio` vuelve a **pass**. Pegar las dos salidas
(rojo y verde) en el reporte de la task.

---

## Task 2 — `probe_facet` deja de escribir `probe_error`

**Files:**
- Modify: `backend/jax_engine/facet_canary.py:96-102`
- Modify: `backend/tests/test_facet_canary.py:172-182`
- Modify: `.github/workflows/policy.yml` (piso de cobertura)

**Interfaces:**
- Consumes: el guard de la Task 1, ya en CI y verificado en rojo.
- Produces: `probe_facet(facet, config, source) -> str | None` — misma
  firma, mismo valor de retorno (`None` si invocó, `"probe_error"` si no).
  **Lo que cambia es sólo el efecto de escritura.**

- [ ] **Step 1: Backup**

```bash
cd /home/fruiz/jax-platform/backend
cp jax_engine/facet_canary.py jax_engine/facet_canary.py.backup-pre-opcionB-$(date +%Y%m%d-%H%M%S)
```

- [ ] **Step 2: Escribir el test que falla**

En `backend/tests/test_facet_canary.py`, **reemplazar** el test de las
líneas 172-182 (`test_probe_facet_registra_probe_error_si_falla_antes_de_invocar`)
por estos dos:

```python
def test_probe_facet_NO_registra_cuando_invoke_facet_lanza(monkeypatch):
    """El corazon de la opcion B (diseno 2026-08-28).

    Cuando _invoke_facet lanza, YA registro el evento clasificado
    (provider_error / config_error / ...) antes de re-lanzar -- es un
    envoltorio total, propiedad protegida por
    tests/test_policy_invoke_facet_envoltorio.py. Un probe_error de
    probe_facet seria una SEGUNDA fila para la misma causa, ~800us mas
    nueva, y el lector (jacobs/facet_health.py, repo jax) toma MAX(ts):
    ganaria la generica y la alerta diria "la sonda fallo" en vez de la
    causa accionable.

    El valor de retorno NO cambia: probe_facet sigue devolviendo
    'probe_error' para que probe_all pueda contar sondeos fallidos sin
    tocar la DB."""
    async def boom(*a, **k):
        raise RuntimeError("el proveedor devolvio 502")
    monkeypatch.setattr(facet_canary, "_invoke_facet", boom)
    recorded = []
    async def fake_record(facet, outcome, source, detail=None):
        recorded.append((facet, outcome)); return True
    monkeypatch.setattr(facet_canary, "record_facet_health", fake_record)

    out = asyncio.run(facet_canary.probe_facet("thot", _config(), "canary_periodic"))
    assert out == "probe_error"
    assert recorded == [], f"probe_facet escribio de mas: {recorded}"


def test_probe_after_rebind_SI_registra_cuando_falla_antes_de_invocar(monkeypatch):
    """La otra mitad: aca probe_error SI es el unico evento.

    probe_after_rebind puede fallar ANTES de llegar a _invoke_facet
    (invalidate_facet_cache, _load_config), y en ese camino nadie mas
    escribio. Sin este registro el fallo quedaria solo en el journal.
    Este test existe para que la Task 2 no se lleve puesta esa mitad."""
    def explota():
        raise RuntimeError("config.toml ilegible")
    monkeypatch.setattr(facet_canary, "_load_config", explota)
    monkeypatch.setattr(facet_canary, "invalidate_facet_cache", lambda k: True)
    monkeypatch.setattr(facet_canary, "CANARY_INTERVAL_SECONDS", 3600)
    recorded = []
    async def fake_record(facet, outcome, source, detail=None):
        recorded.append((facet, outcome, source)); return True
    monkeypatch.setattr(facet_canary, "record_facet_health", fake_record)

    out = asyncio.run(facet_canary.probe_after_rebind("thot"))
    assert out == "probe_error"
    assert recorded == [("thot", "probe_error", "canary_rebind")]
```

- [ ] **Step 3: Correr y verificar que el primero FALLA**

Run:
```bash
cd /home/fruiz/jax-platform/backend
./.venv/bin/python -m pytest tests/test_facet_canary.py -k "NO_registra or SI_registra" -v
```
Expected: `test_probe_facet_NO_registra_cuando_invoke_facet_lanza` **FAILS**
con `probe_facet escribio de mas: [('thot', 'probe_error')]`;
`test_probe_after_rebind_SI_registra...` **PASA** (ese camino ya funciona).

Si el primero pasa sin tocar nada, el cambio ya estaba hecho o el test no
prueba lo que dice: **PARÁ y reportá**.

- [ ] **Step 4: Implementar**

En `backend/jax_engine/facet_canary.py`, reemplazar el `except` de
`probe_facet` (líneas 96-102):

```python
    except Exception:
        # NO se registra aca -- decision de diseno, no un olvido.
        #
        # `_invoke_facet` es un envoltorio TOTAL: cuando lanza, ya escribio
        # el evento clasificado (provider_error / config_error / ...) antes
        # de re-lanzar. Un `probe_error` aca seria una SEGUNDA fila para la
        # misma causa, ~800us mas nueva; el lector (jacobs/facet_health.py,
        # repo jax) toma MAX(ts) por facet, asi que ganaria la generica y la
        # alerta diria "la sonda fallo" en vez de nombrar la causa
        # accionable. Evidencia real del 2026-08-27:
        #
        #   ada  probe_error   canary_rebind  ModelDispatchConfigError: ...  18:02:45.443634
        #   ada  config_error  canary_rebind  ModelDispatchConfigError: ...  18:02:45.442861
        #
        # La propiedad de la que esto depende no es una suposicion: la
        # protege tests/test_policy_invoke_facet_envoltorio.py, que corre en
        # CI y se verifico rompiendolo.
        #
        # Esto NO es fail-open: el evento existe, lo escribio la capa de
        # abajo con mas informacion. El caso en que NADIE escribio --
        # fallar antes de llegar a _invoke_facet -- lo cubre el `except` de
        # probe_after_rebind, que sigue registrando.
        #
        # Ver docs/superpowers/specs/2026-08-28-alerta-capa-equivocada-design.md
        return OUTCOME_PROBE_ERROR
```

Y actualizar el docstring de `probe_facet`, agregando al final:

```python
    Cuando la invocacion falla, NO escribe: la capa de abajo ya registro el
    evento clasificado. Devuelve 'probe_error' igual, como valor de
    retorno, para que probe_all pueda contar sondeos fallidos sin consultar
    la DB.
```

- [ ] **Step 5: Correr los tests**

Run:
```bash
cd /home/fruiz/jax-platform/backend
./.venv/bin/python -m pytest tests/test_facet_canary.py tests/test_facet_canary_rebind.py -v
```
Expected: todos PASS, incluidos los 11 de `test_facet_canary_rebind.py`, que
NO deben haber cambiado — `probe_after_rebind` no se tocó.

- [ ] **Step 6: Verificar que el guard de la Task 1 sigue verde**

Run:
```bash
cd /home/fruiz/jax-platform/backend
./.venv/bin/python -m pytest tests/test_policy_invoke_facet_envoltorio.py -q
```
Expected: 12 passed. `api/chat.py` no se tocó en esta task; si el guard se
pone rojo, algo se modificó que no debía: **PARÁ y reportá**.

- [ ] **Step 7: Suite completa + scanner P10 + conteo sin DB**

```bash
cd /home/fruiz/jax-platform/backend
set -a && . /etc/jax/.env && set +a && export JAX_DB_NAME=jax_memory_test
./.venv/bin/python -m pytest -q 2>&1 | tail -3
./.venv/bin/python -m pytest tests/test_no_fail_open_except.py -q 2>&1 | tail -1
JAX_CI_NO_DB=1 ./.venv/bin/python -m pytest -q 2>&1 | tail -2
```
Expected:
1. Suite completa: los mismos 5 failed + 1 error preexistentes de `master`
   (`test_facet_allowed_callers_migration.py` ×3,
   `test_facet_model_wiring.py`, `test_image_http_pooling.py`,
   `test_websocket_isolation.py`) y **ningún fallo nuevo**. Si aparece otro,
   PARÁ y reportá.
2. Scanner P10: **1 passed**. El `except` nuevo devuelve un valor y no es
   `except: pass`, así que no necesita marca `# fail-soft:`.
3. Conteo sin DB: **121 passed, 151 skipped** (120 de la Task 1, +2 tests
   nuevos, −1 reemplazado). Si difiere, PARÁ y reportá.

- [ ] **Step 8: Subir el piso de cobertura**

En `.github/workflows/policy.yml`, agregar al comentario del piso:

```yaml
      # Subido de 120 a 121 en Task 2 (2026-08-28): el test que afirmaba que
      # probe_facet REGISTRA probe_error se reemplaza por uno que afirma que
      # NO registra (neto 0), y se agrega uno que fija la otra mitad --
      # probe_after_rebind SI registra cuando falla antes de invocar (+1).
      # Skip count sin cambios (151).
      JAX_CI_MIN_PASSED: "121"
```

- [ ] **Step 9: Commit**

```bash
cd /home/fruiz/jax-platform
git add backend/jax_engine/facet_canary.py backend/tests/test_facet_canary.py .github/workflows/policy.yml
git commit -m "fix(health): probe_facet deja de escribir probe_error -- la alerta nombra la causa

_invoke_facet es un envoltorio total: cuando lanza, ya registro el evento
clasificado antes de re-lanzar. El probe_error de probe_facet era una
segunda fila para la misma causa, ~800us mas nueva, y el lector toma
MAX(ts): ganaba la generica y la alerta decia 'la sonda fallo' en vez de
nombrar la causa accionable.

probe_after_rebind sigue registrando: ahi probe_error SI es el unico
evento, y hay un test nuevo que lo fija."
```

- [ ] **Step 10: Verificar CI verde sobre el headSha real, y GATEAR el merge**

Séptima lección: el gate es la condición, no la impresión del resultado.

```bash
cd /home/fruiz/jax-platform
git push
SHA=$(git rev-parse HEAD)
gh pr checks --watch
# GATE: el merge SOLO si no queda ningun check en fail sobre ESE sha
if gh pr view --json statusCheckRollup \
     --jq '.statusCheckRollup[] | select(.conclusion=="FAILURE") | .name' | grep -q .; then
  echo "HAY CHECKS EN ROJO -- NO MERGEAR"; exit 1
fi
echo "todos los checks verdes sobre $SHA"
```
Expected: `invoke-facet-envoltorio`, `no-fail-open-except` y
`backend-tests-no-db` en **pass**. El merge queda para la revisión final,
no lo hace la task.

---

## Fuera de alcance — anotado, no hecho

- **Incluir el `detail` en el texto de la alerta.** El mensaje de
  `config_error` trae el `UPDATE model SET ...` a ejecutar, y hoy no llega
  al Telegram. Es una mejora real con su propia pregunta de diseño (cuánto
  texto entra en un mensaje), en otro repo (`jax`). Candidato de la próxima
  ronda.
- **Opción C** (`probe_error` como dimensión separada, salud del detector
  vs salud del facet). Ver spec §5.
