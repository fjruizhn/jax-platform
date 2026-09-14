#!/usr/bin/env python3
"""TRIPWIRE -- ninguna llamada `aiomysql.connect(...)` ni `aiomysql.create_pool(...)`
de `backend/` se abre sin `connect_timeout`.

Hallazgo de revisión de la Tarea 1b (tanda A, PR-A): en jax se agregó
`connect_timeout` de config a todo `aiomysql.connect`/`create_pool` (commit
2dedf0b, jax/core/db_connect_config.py) -- el default de aiomysql (0.3.2) es
`connect_timeout=None`, que `asyncio.wait_for()` interpreta como "sin
límite": si la DB se cuelga en vez de rechazar, la conexión espera para
siempre. jax-platform tiene DOS espejos de ese repo
(`backend/facet_resolver.py::_db_conn` y
`backend/credential_resolver.py::_db_conn`) que quedaban en drift -- más
`backend/db/connection.py::get_pool` (pool principal del backend, usado por
22 módulos), que no es un espejo pero tiene el mismo default sin límite.

A diferencia de jax (que esta ronda scopeó a `.connect()` y dejó
`.create_pool()` para DEUDA.md), este tripwire cubre LOS DOS -- el hallazgo
de esta tarea nombra explícitamente `create_pool` porque
`db/connection.py::get_pool()` es justamente ese patrón y es el pool
principal del backend.

Es un test de CLASE, no de casos -- mismo criterio que el tripwire de jax:
recorre TODOS los .py de `backend/` (excluyendo venvs/.git/__pycache__/
node_modules) y falla ante CUALQUIER `aiomysql.connect(...)` o
`aiomysql.create_pool(...)` nuevo sin el kwarg `connect_timeout`, con
archivo:línea. A diferencia de un grep de texto, un AST no confunde un
comentario o un docstring que MENCIONA `aiomysql.connect(` (como el de este
mismo archivo, o el de `db_connect_config.py`) con una llamada real -- por
eso el detector propio de este archivo no aparece en sus propios hallazgos.

`connect_timeout=<algo>` cuenta como cumplido aunque el valor no sea
`db_connect_timeout_seconds()` -- el helper es la forma RECOMENDADA (ver
`db_connect_config.py`), pero este tripwire vigila el contrato real (el
kwarg wireado), no una implementación particular.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]  # backend/

# Mismo criterio de exclusión que el tripwire de jax: nada de venvs, control
# de versiones, cachés de bytecode ni node_modules. Sin "docs"/"workspace"
# acá a propósito -- un `aiomysql.connect(`/`create_pool(` sin timeout en un
# script suelto de cualquier lado de backend/ se cuelga igual si alguien lo
# corre contra una DB colgada.
EXCLUIDOS = {".git", "__pycache__", ".pytest_cache", "node_modules"}

FUNCIONES_VIGILADAS = {"aiomysql.connect", "aiomysql.create_pool"}


def _excluido(path: Path) -> bool:
    for parte in path.parts:
        if parte in EXCLUIDOS:
            return True
        minuscula = parte.lower()
        if "venv" in minuscula or "scratch" in minuscula:
            return True
    return False


def _archivos_py():
    for p in RAIZ.rglob("*.py"):
        if _excluido(p):
            continue
        yield p


def _hallazgos_en(path: Path) -> list[str]:
    try:
        arbol = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    encontrados = []

    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        try:
            nombre = ast.unparse(nodo.func)
        except Exception:
            continue
        if nombre not in FUNCIONES_VIGILADAS:
            continue
        tiene_timeout = any(kw.arg == "connect_timeout" for kw in nodo.keywords)
        if not tiene_timeout:
            encontrados.append(f"{path.relative_to(RAIZ)}:{nodo.lineno}: {nombre}() sin connect_timeout")
    return encontrados


class AiomysqlConnectTimeoutTripwireTest(unittest.TestCase):
    def test_ningun_aiomysql_connect_o_create_pool_sin_connect_timeout(self):
        hallazgos = []
        for p in _archivos_py():
            hallazgos.extend(_hallazgos_en(p))
        self.assertEqual(
            hallazgos, [],
            "aiomysql.connect()/create_pool() sin connect_timeout (sin "
            "límite si la DB se cuelga en vez de rechazar -- ver "
            "db_connect_config.py):\n  " + "\n  ".join(hallazgos),
        )

    def test_el_detector_reconoce_connect_sin_connect_timeout(self):
        """El freno se ejercita: si el detector no atrapa un caso obvio, el
        verde del test de arriba no significa nada (un control que no falla
        no valida nada)."""
        import tempfile
        codigo = (
            "import aiomysql\n"
            "async def f():\n"
            "    return await aiomysql.connect(host='x', port=3306)\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=RAIZ, delete=True) as fh:
            fh.write(codigo)
            fh.flush()
            hallazgos = _hallazgos_en(Path(fh.name))
        self.assertEqual(len(hallazgos), 1, f"el detector no vio el sitio sin connect_timeout: {hallazgos}")

    def test_el_detector_reconoce_create_pool_sin_connect_timeout(self):
        """Mismo freno para `create_pool` -- es el sitio real que motivó
        ampliar el tripwire de jax (que solo cubre `.connect()`)."""
        import tempfile
        codigo = (
            "import aiomysql\n"
            "async def f():\n"
            "    return await aiomysql.create_pool(host='x', port=3306, minsize=1, maxsize=10)\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=RAIZ, delete=True) as fh:
            fh.write(codigo)
            fh.flush()
            hallazgos = _hallazgos_en(Path(fh.name))
        self.assertEqual(len(hallazgos), 1, f"el detector no vio el sitio sin connect_timeout: {hallazgos}")

    def test_el_detector_no_confunde_texto_con_una_llamada_real(self):
        """Un comentario o docstring que MENCIONA `aiomysql.connect(` (como
        el de este propio archivo) no es una llamada -- el AST no lo ve como
        ast.Call, a diferencia de un grep de texto."""
        import tempfile
        codigo = (
            '"""Este módulo habla de aiomysql.connect( y de '
            'aiomysql.create_pool( en un docstring, no los llama."""\n'
            "# aiomysql.connect( también en un comentario.\n"
            "x = 1\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=RAIZ, delete=True) as fh:
            fh.write(codigo)
            fh.flush()
            hallazgos = _hallazgos_en(Path(fh.name))
        self.assertEqual(hallazgos, [], f"texto confundido con una llamada real: {hallazgos}")

    def test_el_detector_acepta_connect_timeout_explicito(self):
        """Control positivo: un sitio QUE SÍ pasa connect_timeout (en
        cualquiera de las dos funciones vigiladas) no aparece entre los
        hallazgos."""
        import tempfile
        codigo = (
            "import aiomysql\n"
            "async def f():\n"
            "    a = await aiomysql.connect(host='x', port=3306, connect_timeout=10)\n"
            "    b = await aiomysql.create_pool(host='x', port=3306, connect_timeout=10)\n"
            "    return a, b\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=RAIZ, delete=True) as fh:
            fh.write(codigo)
            fh.flush()
            hallazgos = _hallazgos_en(Path(fh.name))
        self.assertEqual(hallazgos, [], f"un sitio con connect_timeout no debería reportarse: {hallazgos}")


if __name__ == "__main__":
    unittest.main()
