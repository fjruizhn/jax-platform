#!/usr/bin/env python3
"""P10 — ningún validador o gate puede fallar abierto ante error o
ausencia de señal, incluyendo vía excepción sin capturar (REFORMAS-v3.1.md,
Apendice C-bis, jax/six-impossible-things.html).

Copia de jax/policy/tests/test_no_fail_open_except.py (ronda 4, 2026-08-20,
T3). NO es un import compartido: jax-platform y jax son repos GitHub
privados separados, y un checkout cruzado en CI requeriría un PAT/secret
nuevo -- decisión de infraestructura que esta sesión no tomó. Cada repo
vigila su propio árbol con su propia copia; la duplicación es el costo de
no introducir una credencial compartida sin autorización explícita. Si se
edita la lógica de detección en un lado, hay que replicarlo en el otro
(anotado como deuda de sincronización, no resuelta con un paquete
instalable compartido -- esa es la opción más limpia para una sesión
futura, ver CONTEXT.md de jax).

Enforcement mecánico y acotado, no un analizador general: un bloque
`except` cuyo cuerpo es únicamente `pass` (o `pass` + comentarios) traga
el error sin propagarlo, sin loguearlo y sin dejar ningún rastro.

Regla nueva (Task 3, 2026-09-15): todo `except` AMPLIO -- `Exception`,
`BaseException`, desnudo, o una tupla que incluya alguno -- cuyo cuerpo no
relanza (ningun `raise` en el cuerpo) tambien necesita la marca. Seguir de
largo con un log, o con `x = None`, sigue siendo un fail-soft que tiene que
decir por que. Rige sobre todo el repo, tests incluidos. Deuda de
sincronizacion: la copia de jax/policy/tests NO tiene esta regla todavia.

Marcado, no allowlist: un except-pass legítimo (fail-soft real: nadie
depende de que esa operación haya funcionado) se marca con un comentario
en la MISMA línea del `except`, formato `# fail-soft: <razón específica>`.
Sin esa marca, es una violación.

Corre con:
  python3 backend/tests/test_no_fail_open_except.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_THIS_REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOTS = [_THIS_REPO_ROOT]

EXCLUDE_DIR_NAMES = {
    ".venv", "venv", "node_modules", ".git", ".worktrees", "worktrees",
    "__pycache__", "dist", "build",
}

FAIL_SOFT_MARKER = "# fail-soft:"


def _iter_python_files():
    for root in REPO_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            # Partes RELATIVAS a la raiz (Task 3, 2026-09-15): con `path.parts`
            # absolutas, un checkout que vive bajo un directorio llamado
            # `worktrees` (p. ej. /home/fruiz/worktrees/...) se excluia ENTERO
            # y el test pasaba sin escanear nada.
            if any(part in EXCLUDE_DIR_NAMES for part in path.relative_to(root).parts):
                continue
            yield path


def _is_bare_pass_except(node: ast.ExceptHandler) -> bool:
    body = [
        stmt for stmt in node.body
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Constant)
        or not isinstance(stmt.value.value, str)  # descarta docstrings/comentarios-como-string
    ]
    return len(body) == 1 and isinstance(body[0], ast.Pass)


_BROAD_NAMES = {"Exception", "BaseException"}


def _is_broad(tipo: ast.expr | None) -> bool:
    """Desnudo, Exception, BaseException (tambien `x.Exception`), o una tupla
    que incluya alguno."""
    if tipo is None:
        return True
    if isinstance(tipo, ast.Tuple):
        return any(_is_broad(e) for e in tipo.elts)
    if isinstance(tipo, ast.Name):
        return tipo.id in _BROAD_NAMES
    if isinstance(tipo, ast.Attribute):
        return tipo.attr in _BROAD_NAMES
    return False


def _reraises(node: ast.ExceptHandler) -> bool:
    """Hay un `raise` en CUALQUIER punto del cuerpo (tambien condicional).
    Es la misma definicion con la que se midio el triage de la Task 3."""
    return any(isinstance(n, ast.Raise) for stmt in node.body for n in ast.walk(stmt))


def violations_in_source(source: str, filename: str = "<sintetico>") -> list[int]:
    """Lineas de los `except` que violan la regla, sobre codigo fuente puro
    (lo usan el escaneo del repo y los casos sinteticos)."""
    lines = source.splitlines()
    tree = ast.parse(source, filename=filename)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not (_is_bare_pass_except(node)
                or (_is_broad(node.type) and not _reraises(node))):
            continue
        if FAIL_SOFT_MARKER in lines[node.lineno - 1]:
            continue
        out.append(node.lineno)
    return sorted(out)


def find_fail_open_excepts(files=None) -> list[str]:
    violations = []
    for path in (_iter_python_files() if files is None else files):
        try:
            source = path.read_text(encoding="utf-8")
            lineas = violations_in_source(source, str(path))
        except (SyntaxError, UnicodeDecodeError):
            # Fix ronda 1 (2026-09-15): antes `continue` -- un archivo que el
            # escaner no puede leer quedaba sin revisar, en silencio.
            violations.append(f"{path}: no se pudo parsear")
            continue
        violations.extend(f"{path}:{n}" for n in lineas)
    return violations


# --- casos sinteticos puros (Task 3, 2026-09-15) ---------------------------
# Cada uno fija una frontera de la regla sin depender del arbol del repo.

def _src(cuerpo_except: str, tipo: str = "Exception", marca: str = "") -> str:
    cabecera = f"except {tipo}:" if tipo else "except:"
    return (
        "def f():\n"
        "    try:\n"
        "        g()\n"
        f"    {cabecera}{marca}\n"
        f"        {cuerpo_except}\n"
    )


def test_sintetico_except_amplio_que_sigue_de_largo_sin_marca_falla():
    # El caso que encontro el review: no es `pass`, pero se traga el error igual.
    assert violations_in_source(_src("x = None")) == [4]


def test_sintetico_except_amplio_con_marca_pasa():
    assert violations_in_source(_src("x = None", marca="  # fail-soft: razon concreta")) == []


def test_sintetico_tupla_que_incluye_exception_sin_marca_falla():
    assert violations_in_source(_src("x = None", tipo="(ValueError, Exception)")) == [4]


def test_sintetico_tupla_con_baseexception_por_atributo_sin_marca_falla():
    assert violations_in_source(_src("x = None", tipo="(KeyError, builtins.BaseException)")) == [4]


def test_sintetico_except_amplio_que_relanza_pasa():
    assert violations_in_source(_src("log(); raise")) == []


def test_sintetico_relanzar_en_cualquier_punto_del_cuerpo_cuenta():
    src = (
        "def f():\n"
        "    try:\n"
        "        g()\n"
        "    except Exception as e:\n"
        "        if malo(e):\n"
        "            raise RuntimeError('x') from e\n"
        "        x = None\n"
    )
    assert violations_in_source(src) == []


def test_sintetico_except_desnudo_pass_sin_marca_sigue_fallando():
    assert violations_in_source(_src("pass", tipo="")) == [4]


def test_sintetico_except_estrecho_pass_sin_marca_sigue_fallando():
    # Regla vieja intacta: un except-pass de cualquier tipo necesita la marca.
    assert violations_in_source(_src("pass", tipo="ValueError")) == [4]


def test_sintetico_except_estrecho_que_sigue_de_largo_no_es_asunto_de_esta_regla():
    assert violations_in_source(_src("x = None", tipo="ValueError")) == []


def test_sintetico_funcion_anidada_tambien_se_escanea():
    src = (
        "def f():\n"
        "    def g():\n"
        "        try:\n"
        "            h()\n"
        "        except Exception:\n"
        "            return None\n"
        "    return g\n"
    )
    assert violations_in_source(src) == [5]


def test_sintetico_la_marca_en_el_cuerpo_no_alcanza():
    src = (
        "def f():\n"
        "    try:\n"
        "        g()\n"
        "    except Exception:\n"
        "        # fail-soft: en el cuerpo, no en la linea del except\n"
        "        x = None\n"
    )
    assert violations_in_source(src) == [4]


def test_sintetico_except_amplio_que_solo_loguea_sin_marca_falla():
    # Fix ronda 1 (2026-09-15): un _reraises que contara un logger.* como
    # relanzar dejaba verdes todos los demas casos. Loguear no es relanzar.
    assert violations_in_source(_src('logger.warning("x")')) == [4]


def test_sintetico_except_amplio_que_solo_loguea_con_marca_pasa():
    assert violations_in_source(_src('logger.warning("x")', marca="  # fail-soft: razon concreta")) == []


# Medido el 2026-09-15 (fix ronda 1): 155 archivos .py en el escaneo. El piso
# va por debajo a proposito: crecer no rompe; perder un arbol entero si.
PISO_ARCHIVOS_ESCANEADOS = 120


def test_el_escaneo_del_repo_ve_archivos_de_produccion():
    # Guardia del propio control: un escaneo que no ve nada pasa siempre.
    vistos = {p.relative_to(_THIS_REPO_ROOT).as_posix() for p in _iter_python_files()}
    assert "backend/api/chat.py" in vistos
    assert "backend/tests/test_no_fail_open_except.py" in vistos
    for prefijo in ("backend/jax_engine/", "backend/api/admin/", "backend/api/"):
        assert any(v.startswith(prefijo) for v in vistos), f"el escaneo no ve nada bajo {prefijo}"
    assert len(vistos) >= PISO_ARCHIVOS_ESCANEADOS, (
        f"el escaneo ve {len(vistos)} archivos (< {PISO_ARCHIVOS_ESCANEADOS}): se excluyo un arbol")


def test_un_archivo_que_no_se_puede_parsear_es_violacion(tmp_path):
    # Fix ronda 1 (2026-09-15): antes se salteaba en silencio, y un archivo
    # que el escaner no puede leer nunca se revisaba.
    roto = tmp_path / "roto.py"
    roto.write_text("def f(:\n    pass\n")
    no_utf8 = tmp_path / "latin.py"
    no_utf8.write_bytes(b"x = '\xff\xfe'\n")
    violaciones = find_fail_open_excepts(files=[roto, no_utf8])
    assert violaciones == [f"{roto}: no se pudo parsear", f"{no_utf8}: no se pudo parsear"]


def test_no_fail_open_except() -> None:
    violations = find_fail_open_excepts()
    assert not violations, (
        f"{len(violations)} except (except-pass, o amplio sin relanzar) sin marcar "
        "'# fail-soft: <razón>' en la linea del except:\n"
        + "\n".join(violations)
    )


def main() -> int:
    violations = find_fail_open_excepts()
    if violations:
        print(f"FAIL — {len(violations)} except fail-open sin marca encontrados:")
        for v in violations:
            print(f"  {v}")
        return 1
    print("OK — cero except fail-open sin marca en el codigo fuente escaneado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
