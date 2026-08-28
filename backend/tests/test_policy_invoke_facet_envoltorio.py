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

No verifica que hoy este bien: DETECTA LA MUTACION -- clasificada por
FAMILIA DE ATAQUE, NO por conteo. Un numero de tests con 100% de deteccion
puede tapar una familia entera sin tocar: paso DOS VECES en esta misma
task (Rondas de correccion 1 y 3). Por eso lo que se mantiene aca es esta
lista de familias, no un numero -- si aparece una familia nueva, se agrega
un item aca, y el numero de tests que resulta es una CONSECUENCIA, no la
medida:

1. Ausencia -- el control no esta: se quito el `raise`, el registro, o el
   `except` generico. Cubierta desde el diseno original.
2. Inalcanzabilidad -- el control esta ESCRITO pero no corre: `if False:`,
   un flag que nunca se activa, cualquier rama condicional muerta.
   Cubierta desde la Ronda de correccion 1, que encontro que `ast.walk()`
   sobre el subarbol completo del handler dejaba pasar en verde un
   `record_facet_health` o un `raise` que EXISTIAN en el codigo pero nunca
   se ejecutaban de verdad.
3. Sustitucion de identidad -- el control esta y CORRE, pero el nombre no
   resuelve a la funcion real. Detectada NO enumerando constructos del
   lenguaje, sino PREGUNTANDOLE AL COMPILADOR: `symtable` es la propia
   tabla de simbolos que arma CPython, y responde si `record_facet_health`
   es local al scope de `_invoke_facet`. Ese cambio es el punto: la
   enumeracion de nodos AST (`Assign`, `FunctionDef`, `NamedExpr`,
   `import ... as`, ...) era un SUBCONJUNTO de las reglas de scoping de
   Python y siempre lo iba a ser -- se le escapaban el desempaquetado de
   tupla, `del`, el target de un `for`, `with ... as`, la captura de
   `match/case`. Con `symtable`, un constructo NUEVO del lenguaje queda
   cubierto sin tocar el guard: era exactamente el techo del enfoque
   anterior (`match/case` llego en 3.10 y este backend corre 3.14 -- la
   lista enumerada nunca iba a estar completa).

   La CORRUPCION DE ALCANCE POR RAMA MUERTA entra aca y NO en la familia
   2: un `del record_facet_health` (o un `for`, un `with ... as`, un
   `case`) dentro de un `if False:` vuelve el nombre LOCAL para TODA la
   funcion segun las reglas de CPython, asi que la llamada real -- que es
   directa e incondicional -- revienta con `UnboundLocalError`. No es
   inalcanzabilidad del control: es identidad rota ANTES de la llamada.
5. Ambiguedad del sujeto -- el codigo esta bien, pero el guard audita
   OTRA funcion. Aparece de dos formas, las dos verificadas: un homonimo
   anidado que se lleva la busqueda del scope (`ast` mira una,
   `symtable` otra), y dos `_invoke_facet` de nivel superior (las dos
   miran la primera; en runtime queda la ultima). Por eso hay una sola
   fuente de verdad -- el nodo AST -- y mas de una definicion es de por
   si una violacion. Es la familia que solo se ve atacando AL GUARD, no
   al codigo: las tres anteriores se descubrieron mutando `chat.py`,
   esta mutando el archivo alrededor de el.
4. Argumentos incorrectos -- registra de verdad, con la funcion real, pero
   con el outcome equivocado (ej. `ModelDispatchConfigError` escribe `ok`
   en vez de `config_error`). FUERA POR DISEÑO, no un descuido: eso es
   comportamiento, no estructura, y lo cubren `test_facet_health_outcomes.py`
   y los tests de la sonda, que SI conocen el significado de cada outcome.
   Un guard estatico que tambien intentara verificar argumentos terminaria
   reimplementando esos tests, peor.

Las 23 mutaciones de abajo son la cobertura ACTUAL de las familias 1-3
y 5.
Dos de ellas (quitar el handler generico, y un archivo que no parsea)
salieron de atacar ESTE TEST, no el codigo: la primera version las dejaba
pasar en verde. Por eso el handler generico como ULTIMO handler es un
requisito del diseno y no una preferencia de estilo -- sin el, una
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
import symtable
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


def _scope_de_funcion(scope: symtable.SymbolTable, funcion: str,
                      lineno: int):
    """Busca RECURSIVAMENTE el scope de `funcion` entre los hijos de
    `scope`, exigiendo que empiece en `lineno`, o None si no aparece.

    Recursivo a proposito: hoy `_invoke_facet` es de nivel superior, pero
    el guard no deberia depender de eso -- si maniana queda anidada en un
    `if TYPE_CHECKING:`, en una clase o en otra funcion, la politica
    tiene que seguir aplicandose.

    El `lineno` NO es un extra defensivo: es lo que ata esta busqueda al
    nodo AST que ya resolvio `violaciones()`. Sin el, esta funcion elegia
    por NOMBRE con retorno temprano y podia devolver un scope DISTINTO de
    la funcion que auditan los chequeos estructurales -- bypass real de la
    Ronda de correccion 4, introducido por su propio fix: un homonimo
    anidado y limpio, definido antes en el archivo, se llevaba la busqueda
    y el shadowing de la funcion real pasaba en verde. `symtable` y `ast`
    coinciden en el lineno del `def` (verificado, tambien con decoradores
    y `async def`: el decorador no lo corre)."""
    for hijo in scope.get_children():
        if (hijo.get_type() == "function" and hijo.get_name() == funcion
                and hijo.get_lineno() == lineno):
            return hijo
        encontrado = _scope_de_funcion(hijo, funcion, lineno)
        if encontrado is not None:
            return encontrado
    return None


def _ligado_localmente(scope: symtable.SymbolTable, nombre: str) -> bool:
    """True si `nombre` es LOCAL al scope de la funcion, segun la tabla de
    simbolos que arma el propio compilador de CPython.

    No enumera constructos: le PREGUNTA al compilador. La version anterior
    (Ronda de correccion 3) listaba tipos de nodo AST -- `Assign`,
    `AnnAssign`, `FunctionDef`, `NamedExpr`, `import ... as`, parametros --
    y esa lista es por construccion un subconjunto de las reglas de
    scoping de Python. Bypasses reales que la atravesaban en verde:
    desempaquetado de tupla, y corrupcion de alcance por rama muerta
    (`del`, target de `for`, `with ... as`, captura de `match/case` dentro
    de un `if False:`), que vuelven el nombre local para TODA la funcion y
    hacen que la llamada real reviente con `UnboundLocalError`.

    `symtable` cubre todos esos, y tambien los que el lenguaje agregue
    despues, sin tocar este archivo."""
    try:
        s = scope.lookup(nombre)
    except KeyError:
        return False
    return s.is_local() or s.is_assigned()


def _ligaduras_de_modulo(arbol: ast.Module, nombre: str) -> list[int]:
    """Lineas de las sentencias de nivel superior que LIGAN `nombre` en el
    scope del modulo. Lanza SyntaxError si alguna no se puede analizar.

    Le pregunta al compilador sentencia por sentencia -- misma leccion que
    `_ligado_localmente`, aplicada al sujeto en vez de al registro.
    Enumerar aqui los constructos que ligan un nombre (`def`, `Assign`,
    `import ... as`, desempaquetado, `del`, `for`, `with ... as`, ...)
    seria repetir el error que la Ronda de correccion 3 dejo documentado:
    esa lista es por construccion un subconjunto de las reglas de scoping
    y el lenguaje la invalida solo.

    Existe porque el chequeo por `def` no ve la re-ligadura del propio
    sujeto: `_invoke_facet = lambda *a, **k: ...` despues de la
    definicion real deja UNA sola `def` en el arbol, asi que el guard
    auditaba la funcion limpia mientras en runtime el nombre resolvia a
    otra cosa. Es la misma familia 5 por una via distinta."""
    lineas: list[int] = []
    for s in arbol.body:
        tabla = symtable.symtable(ast.unparse(s), "<sentencia>", "exec")
        try:
            simbolo = tabla.lookup(nombre)
        except KeyError:
            continue                       # la sentencia ni lo menciona
        if simbolo.is_assigned() or simbolo.is_imported():
            lineas.append(s.lineno)
    return lineas


def violaciones(codigo: str) -> list[str]:
    """Devuelve [] si _invoke_facet es un envoltorio total; motivos si no."""
    try:
        arbol = ast.parse(codigo)
        tabla = symtable.symtable(codigo, "<policy>", "exec")
    except SyntaxError as e:
        return [f"el archivo no parsea ({e.msg}, linea {e.lineno}): "
                f"el test no puede verificar nada"]

    candidatos = sorted(
        (n for n in ast.walk(arbol)
         if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
         and n.name == FUNCION),
        key=lambda n: n.lineno)
    if not candidatos:
        return [f"{FUNCION} no existe (renombrada?): la politica no aplica "
                f"a nada y eso ya es una violacion"]
    def _ambiguo(lineas: list[int]) -> list[str]:
        return [f"hay {len(lineas)} definiciones de {FUNCION} (lineas "
                f"{', '.join(str(l) for l in lineas)}) disputandose el "
                f"mismo nombre: el guard no puede saber cual protege. En "
                f"Python queda en efecto la ULTIMA, asi que auditar la "
                f"primera es un guard que aprueba codigo que no corre"]

    # Cuantas sentencias del modulo ligan el nombre. Cubre la re-ligadura
    # por assign/import/desempaquetado, no solo las `def` homonimas.
    try:
        ligaduras = _ligaduras_de_modulo(arbol, FUNCION)
    except SyntaxError as e:
        return [f"una sentencia de nivel superior no se pudo analizar "
                f"({e.msg}): el guard no puede afirmar que {FUNCION} sea "
                f"el nombre que corre, asi que falla cerrado"]
    if len(ligaduras) > 1:
        return _ambiguo(ligaduras)

    # Un homonimo ANIDADO no compite por este nombre -- no se puede llamar
    # desde afuera -- asi que no es ambiguedad: es el caso que el chequeo
    # de identidad por lineno tiene que resolver. Tratarlo como ambiguo
    # taparia el bypass de la Ronda 4 con un verdicto que suena parecido y
    # dejaria ese chequeo sin ejercitar nunca.
    nivel_superior = [n for n in arbol.body
                      if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                      and n.name == FUNCION]
    if not nivel_superior and len(candidatos) > 1:
        # Ninguna liga el nombre del modulo y hay varias anidadas: no hay
        # forma sintactica de saber cual es LA funcion. Fallar cerrado.
        return _ambiguo([n.lineno for n in candidatos])

    # Fuente UNICA de verdad de "cual es la funcion": este nodo. El scope
    # de symtable se ata a el por lineno, nunca por nombre (ver
    # _scope_de_funcion). Se acepta anidada (un `if TYPE_CHECKING:`, una
    # clase) mientras sea la unica: la politica no depende de que este en
    # el nivel superior.
    fn = nivel_superior[0] if nivel_superior else candidatos[0]

    scope = _scope_de_funcion(tabla, FUNCION, fn.lineno)
    if scope is None:
        return [f"{FUNCION} (linea {fn.lineno}) no aparece en la tabla de "
                f"simbolos: el chequeo de identidad no puede correr, y un "
                f"chequeo que no corre no protege nada"]

    v: list[str] = []
    if _ligado_localmente(scope, REGISTRO):
        v.append(f"`{REGISTRO}` queda ligado localmente en el scope de "
                 f"{FUNCION} (segun symtable): la llamada matchea por "
                 f"nombre, no por identidad -- un homonimo local, o una "
                 f"corrupcion de alcance en una rama muerta, anula el "
                 f"registro real")
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
# Auto-verificacion: las 23 mutaciones de las familias 1-3 y 5 (ver docstring
# del modulo) tienen que ser DETECTADAS.
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


def test_detecta_reasignacion_local_del_registro():
    """Bypass real (Ronda de correccion 3): una reasignacion local anula
    la llamada real -- el guard matcheaba por NOMBRE, nunca por identidad.
    `record_facet_health = lambda *a, **k: None` deja que la llamada
    siguiente siga pareciendo un registro real."""
    m = _BASE.replace('''    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
''', '''    except Exception as e:
        record_facet_health = lambda *a, **k: None
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
''')
    assert any("queda ligado localmente" in x for x in violaciones(m))


def test_detecta_funcion_anidada_homonima():
    """Bypass real (Ronda de correccion 3): una funcion anidada con el
    mismo nombre que REGISTRO no hace nada -- mismo problema de identidad
    que la reasignacion, otra via de llegar al mismo hueco."""
    m = _BASE.replace('''    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
''', '''    except Exception as e:
        async def record_facet_health(*a, **k):
            return None
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
''')
    assert any("queda ligado localmente" in x for x in violaciones(m))


def _con_handler(nuevo_handler: str) -> str:
    """Reemplaza el `except Exception` de la base por `nuevo_handler`."""
    return _BASE.replace('''    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
''', nuevo_handler)


def test_detecta_desempaquetado_de_tupla():
    """Variante trivial de la reasignacion que la Ronda de correccion 3
    creia haber cerrado: la enumeracion de nodos miraba `Assign` con
    target `Name`, pero un target `Tuple` liga igual. symtable no ve la
    diferencia porque no mira la forma del target: mira el scope."""
    m = _con_handler('''    except Exception as e:
        record_facet_health, _basura = (lambda *a, **k: None), None
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
''')
    assert any("queda ligado localmente" in x for x in violaciones(m))


def test_detecta_del_en_rama_muerta():
    """Corrupcion de alcance: `del` en una rama que NUNCA corre vuelve el
    nombre local para TODA la funcion. La llamada real, directa e
    incondicional, revienta con UnboundLocalError -- no registra nada."""
    m = _con_handler('''    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        if False:
            del record_facet_health
        raise
''')
    assert any("queda ligado localmente" in x for x in violaciones(m))


def test_detecta_target_de_for_en_rama_muerta():
    """Misma corrupcion de alcance, via el target de un `for`."""
    m = _con_handler('''    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        if False:
            for record_facet_health in []:
                pass
        raise
''')
    assert any("queda ligado localmente" in x for x in violaciones(m))


def test_detecta_with_as_en_rama_muerta():
    """Misma corrupcion de alcance, via `with ... as`."""
    m = _con_handler('''    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        if False:
            with _algo() as record_facet_health:
                pass
        raise
''')
    assert any("queda ligado localmente" in x for x in violaciones(m))


def test_detecta_captura_de_match_case_en_rama_muerta():
    """Misma corrupcion de alcance, via la captura de un `case`.

    `match/case` llego en Python 3.10: es el ejemplo canonico de por que
    enumerar constructos era un techo -- el guard anterior se escribio sin
    conocerlo y no habia forma de que lo cubriera."""
    m = _con_handler('''    except Exception as e:
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        if False:
            match e:
                case record_facet_health:
                    pass
        raise
''')
    assert any("queda ligado localmente" in x for x in violaciones(m))


_DECOY_ANIDADO = '''
def _decoy_container():
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

_ATAQUE_SHADOWING = _con_handler('''    except Exception as e:
        record_facet_health = lambda *a, **k: None
        await record_facet_health(facet, OUTCOME_PROVIDER_ERROR, source, str(e))
        raise
''')


def test_detecta_homonimo_anidado_que_secuestra_la_tabla_de_simbolos():
    """Bypass real (Ronda de correccion 4, introducido por su propio fix):
    el chequeo de identidad buscaba el scope de _invoke_facet POR NOMBRE
    con DFS y retorno temprano, mientras los chequeos estructurales usaban
    el nodo AST. Un homonimo ANIDADO y limpio, definido antes en el
    archivo, se lleva la busqueda del scope: symtable audita la funcion
    decoy y ast la real, asi que el shadowing de la real pasa en verde.

    Ironia que vale registrar: el fix contra la sustitucion de identidad
    introdujo sustitucion de identidad en el propio guard. Por eso hay una
    sola fuente de verdad -- el nodo AST -- y el scope se busca por LINENO
    contra ella, no por nombre."""
    sin_decoy = violaciones(_ATAQUE_SHADOWING)
    assert any("queda ligado localmente" in x for x in sin_decoy)
    con_decoy = violaciones(_DECOY_ANIDADO + _ATAQUE_SHADOWING)
    assert any("queda ligado localmente" in x for x in con_decoy), (
        "un homonimo anidado antes en el archivo secuestro el chequeo de "
        "identidad: el guard audito la funcion equivocada")


def test_detecta_definicion_duplicada_de_la_funcion():
    """Bypass real encontrado atacando el fix de la Ronda 5, de la misma
    familia y NO cubierto por el: dos `_invoke_facet` de nivel superior.
    El guard resolvia la PRIMERA (limpia) y en runtime queda en efecto la
    ULTIMA (sucia) -- gana la ultima asignacion al nombre del modulo.

    Desambiguar por lineno no alcanza aca: ast y symtable coinciden, los
    dos en la funcion equivocada. La unica respuesta correcta es fallar
    CERRADO ante la ambiguedad: si hay mas de una, el guard no puede saber
    cual protege."""
    m = _BASE + _ATAQUE_SHADOWING
    assert any("definiciones" in x for x in violaciones(m)), (
        "dos definiciones homonimas de nivel superior: el guard audito la "
        "primera y dejo pasar la que realmente corre"
    )


def test_detecta_religadura_del_sujeto_por_asignacion():
    """Bypass real encontrado atacando el fix de la Ronda 5: el sujeto
    auditado se re-liga DESPUES de definirse. Queda UNA sola `def` en el
    archivo -- la limpia, que el guard aprueba -- pero en runtime
    `_invoke_facet` resuelve al lambda. Mismo hueco que la definicion
    duplicada, por una via que un chequeo basado en `def` no ve."""
    m = _BASE + "\n_invoke_facet = lambda *a, **k: ('', None)\n"
    assert any("disputandose el mismo nombre" in x for x in violaciones(m))


def test_detecta_religadura_del_sujeto_por_import_as():
    """Misma re-ligadura por otra via. No se cubre enumerando `import as`
    junto a `Assign`: se cubre preguntandole al compilador que nombres
    liga cada sentencia -- ver `_ligaduras_de_modulo`."""
    m = _BASE + "\nfrom _otro import cualquiera as _invoke_facet\n"
    assert any("disputandose el mismo nombre" in x for x in violaciones(m))


def test_no_marca_homonimo_anidado_sobre_codigo_limpio():
    """Contrapeso de los dos tests de ambiguedad: un homonimo ANIDADO no
    disputa el nombre del modulo, asi que sobre codigo limpio tiene que
    dar verde.

    Sin este test, "tratar toda multiplicidad como ambigua" pasaria los
    dos tests de arriba y dejaria el chequeo de identidad por lineno sin
    ejercitar NUNCA -- un guard que dice violacion siempre no protege mas
    que uno que dice [] siempre; solo se rompe distinto."""
    assert violaciones(_DECOY_ANIDADO + _BASE) == []
    assert violaciones(_BASE + _DECOY_ANIDADO) == []
