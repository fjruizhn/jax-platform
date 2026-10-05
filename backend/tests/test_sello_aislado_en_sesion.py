"""La sesión de tests nunca apunta al sello REAL de facet_resolver.

2026-09-12: con el sello del catálogo de motores, run_migrations estampa el
sello al terminar. El fixture `client` es de SESIÓN y arranca la app (y con
ella run_migrations) ANTES que el aislamiento por función
(`_sello_de_facets_aislado`): correr la suite en hall9000 estampó
/srv/jax-data/facet-cache-seal a las 14:37:46 -- el archivo que vigilan
Jacobs, el REPL y LAS MANOS en producción.

El aislamiento tiene que existir antes de que se importe facet_resolver, que
lee JAX_FACET_SEAL_PATH al importarse.
"""
import os

_SELLO_REAL = "/srv/jax-data/facet-cache-seal"


def test_la_sesion_no_apunta_al_sello_real():
    ruta = os.environ.get("JAX_FACET_SEAL_PATH")
    assert ruta, "JAX_FACET_SEAL_PATH no está fijado para la sesión de tests"
    assert ruta != _SELLO_REAL and not ruta.startswith("/srv/"), ruta


def test_el_valor_del_modulo_ya_importado_no_es_el_real():
    """El valor que `facet_resolver` YA tenía al arrancar el conftest (no el
    env, no un default recalculado): FACET_SEAL_PATH se lee al importar. Con
    DB de sesión local (sufijo, hall9000) el conftest lo importa vía
    run_migrations; en CI con BASE_COMPARTIDA `asegurar_base_de_test` vuelve
    sin migrar y el módulo no está importado todavía: ahí el conftest anota
    None y lo que se comprueba es que el PRIMER import posterior vea el
    entorno de prueba (el orden lo controla la prueba AST de abajo)."""
    import conftest
    ruta = conftest.SELLO_DEL_MODULO_AL_IMPORTARSE
    if ruta is not None:
        assert not ruta.startswith("/srv/"), ruta
        assert ruta == os.environ["JAX_FACET_SEAL_PATH"], ruta
        return
    # Aún no importado: el primer import leerá el env tal cual está ahora.
    esperado = os.environ["JAX_FACET_SEAL_PATH"]
    assert not esperado.startswith("/srv/"), esperado
    import importlib.util
    import subprocess
    import sys
    if importlib.util.find_spec("aiomysql") is None:
        return  # sin aiomysql no hay import posible ni sello que proteger
    salida = subprocess.run(
        [sys.executable, "-c",
         "import facet_resolver; print(facet_resolver.FACET_SEAL_PATH)"],
        capture_output=True, text=True, check=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ).stdout.strip()
    assert salida == esperado, salida


def _importa_a_nivel_de_modulo(nombre):
    """Líneas del conftest que importan `nombre` a nivel de módulo, fuera de
    un try que atrape ImportError (dentro de funciones no cuenta)."""
    import ast
    import pathlib
    arbol = ast.parse(
        (pathlib.Path(__file__).parent / "conftest.py").read_text())
    halladas = []

    def atrapa_import(t):
        nombres = []
        for h in t.handlers:
            tipos = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
            nombres += [getattr(x, "id", None) for x in tipos]
        return any(n in ("ImportError", "ModuleNotFoundError", "Exception",
                         "BaseException", None) for n in nombres)

    def visita(nodos):
        for n in nodos:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
                continue
            if isinstance(n, ast.Import):
                if any(a.name.split(".")[0] == nombre for a in n.names):
                    halladas.append(n.lineno)
            elif isinstance(n, ast.ImportFrom):
                if (n.module or "").split(".")[0] == nombre:
                    halladas.append(n.lineno)
            elif isinstance(n, ast.Try):
                if not atrapa_import(n):
                    visita(n.body)
                visita(n.orelse + n.finalbody)
                for h in n.handlers:
                    visita(h.body)
            else:
                for campo in ("body", "orelse", "finalbody"):
                    visita(getattr(n, campo, []) or [])
    visita(arbol.body)
    return halladas


def test_el_conftest_no_importa_facet_resolver_a_nivel_de_modulo():
    """`facet_resolver` importa aiomysql; los jobs `no-fail-open-except` e
    `invoke-facet-envoltorio` instalan solo pytest, y un import duro a nivel
    de módulo rompe la carga del conftest entero ("ImportError while loading
    conftest", exit 4). Se lee `sys.modules.get(...)` en su lugar."""
    lineas = _importa_a_nivel_de_modulo("facet_resolver")
    assert not lineas, (
        f"conftest.py importa facet_resolver a nivel de módulo (líneas "
        f"{lineas}) fuera de try/except ImportError")


# Variables de ruta de producción que se leen al importar la app (o que la
# migración ya toca): tienen que estar fijadas ANTES de fijar_base_de_test() /
# asegurar_base_de_test(), que corren run_migrations() e importan módulos.
_RUTAS_ANTES_DE_MIGRAR = (
    "JAX_FACET_SEAL_PATH",
    "JAX_USAGE_SPOOL_DIR",
    "JAX_PROXY_CARRIL_RAIZ",
    "JAX_KILL_SWITCH_PATH",
    "JAX_EJECUTOR_PAUSA",
    "JAX_EJECUTOR_PYTHON",
    "JAX_REPO_BASE",
    "JAX_AUDIT_LOG_PATH",
)


def _lineas_del_conftest():
    import ast
    import pathlib
    arbol = ast.parse(
        (pathlib.Path(__file__).parent / "conftest.py").read_text())
    asignadas, migra = {}, []
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign):
            for t in nodo.targets:
                if (isinstance(t, ast.Subscript)
                        and isinstance(t.value, ast.Attribute)
                        and t.value.attr == "environ"
                        and isinstance(t.slice, ast.Constant)):
                    asignadas.setdefault(t.slice.value, nodo.lineno)
        if (isinstance(nodo, ast.Expr) and isinstance(nodo.value, ast.Call)
                and isinstance(nodo.value.func, ast.Name)
                and nodo.value.func.id in (
                    "fijar_base_de_test", "asegurar_base_de_test")):
            migra.append(nodo.lineno)
    return asignadas, migra


def test_el_conftest_fija_las_rutas_antes_de_migrar():
    """Orden, no solo valor: con DB, asegurar_base_de_test() corre
    run_migrations() en este mismo proceso, que importa facet_resolver."""
    asignadas, migra = _lineas_del_conftest()
    assert migra, "no se encontró la llamada a fijar/asegurar_base_de_test"
    tarde = {v: asignadas.get(v) for v in _RUTAS_ANTES_DE_MIGRAR
             if asignadas.get(v) is None or asignadas[v] > min(migra)}
    assert not tarde, (
        f"se fijan después de la migración (línea {min(migra)}): {tarde}")
