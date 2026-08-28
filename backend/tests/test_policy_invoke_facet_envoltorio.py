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
mutan el codigo a proposito de 12 formas y exigen que cada una sea
detectada. Dos de esas 12 (quitar el handler generico, y un archivo que no
parsea) salieron de atacar ESTE TEST, no el codigo: la primera version las
dejaba pasar en verde. Otras dos (registro seguido de un `raise` en una
rama muerta, y un registro adentro de un `if` que nunca se activa)
salieron de la Ronda de correccion 1: `ast.walk()` sobre el subarbol
completo del handler dejaba pasar en verde un `record_facet_health` y un
`raise` que EXISTIAN en el codigo pero nunca se ejecutaban de verdad. Por
eso el handler generico como ULTIMO handler es un requisito del diseno y
no una preferencia de estilo -- sin el, una excepcion no prevista escapa
sin registrar, que es exactamente la propiedad que este archivo existe
para proteger.

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


def _es_llamada_directa(stmt: ast.stmt, nombre: str) -> bool:
    """True si `stmt` ES (no CONTIENE) una llamada directa a `nombre`,
    opcionalmente envuelta en `await`.

    A proposito NO usa ast.walk ni baja a subarboles: una llamada dentro
    de un `if`, un `try` anidado, un `while`, un `with` o una funcion
    anidada puede estar en el codigo fuente sin ejecutarse nunca de
    verdad. Bypass real encontrado en la Ronda de correccion 1: un
    `except Exception` con `if algo_que_nunca_es_true(): await
    record_facet_health(...)` pasaba en verde porque `ast.walk(h)`
    encontraba la llamada en cualquier parte del subarbol del handler."""
    if not isinstance(stmt, ast.Expr):
        return False
    val = stmt.value
    if isinstance(val, ast.Await):
        val = val.value
    return isinstance(val, ast.Call) and _nombre_llamada(val) == nombre


def _registra_directo(cuerpo: list[ast.stmt]) -> bool:
    """True si alguna sentencia DIRECTA (nivel superior) de `cuerpo` es
    una llamada incondicional a REGISTRO."""
    return any(_es_llamada_directa(s, REGISTRO) for s in cuerpo)


def _relanza_directo(cuerpo: list[ast.stmt]) -> bool:
    """True si alguna sentencia DIRECTA (nivel superior) de `cuerpo` es
    un `raise` incondicional.

    A proposito NO usa ast.walk: un `raise` dentro de un `if False:`
    esta en el subarbol del handler pero nunca se ejecuta -- ese es el
    otro bypass real de la Ronda de correccion 1 (registra y despues
    TRAGA la excepcion con un `raise` en una rama muerta seguido de un
    `return`)."""
    return any(isinstance(s, ast.Raise) for s in cuerpo)


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
        if not (isinstance(s, ast.Return) or _es_llamada_directa(s, REGISTRO)):
            v.append(f"sentencia en linea {s.lineno} DESPUES del try que no "
                     f"registra ni es return")

    for h in t.handlers:
        etiq = (getattr(h.type, "id", getattr(h.type, "attr", "?"))
                if h.type else "bare")
        if not _registra_directo(h.body):
            v.append(f"handler {etiq} (linea {h.lineno}) NO registra antes "
                     f"de propagar")
        if not _relanza_directo(h.body):
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
# Auto-verificacion: las 12 mutaciones tienen que ser DETECTADAS.
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


def test_detecta_raise_en_rama_muerta():
    """Bypass real (Ronda de correccion 1): registra y despues TRAGA la
    excepcion -- el `raise` esta en el subarbol del handler (adentro de
    un `if False:`) pero nunca se ejecuta. `ast.walk(h)` lo encontraba y
    daba `[]` (verde); el chequeo directo sobre `h.body` no."""
    m = _BASE.replace('''    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
''', '''    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        if False:
            raise
        return "", None
''')
    assert any("NO re-lanza" in x for x in violaciones(m))


def test_detecta_registro_condicional_que_nunca_corre():
    """Bypass real (Ronda de correccion 1): el registro esta en el
    subarbol del handler, pero adentro de un `if` que nunca se activa --
    nunca corre de verdad. `ast.walk(h)` lo encontraba y daba `[]`
    (verde); el chequeo directo sobre `h.body` no."""
    m = _BASE.replace('''    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
''', '''    except Exception as e:
        if os.environ.get("FLAG_QUE_NUNCA_SE_ACTIVA"):
            await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
''')
    assert any("NO registra" in x for x in violaciones(m))
