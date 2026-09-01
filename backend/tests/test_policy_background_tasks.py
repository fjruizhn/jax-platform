"""
Ninguna BackgroundTask puede llevarse puestas a las que se encolaron DESPUES.

EL DEFECTO. `BackgroundTasks` de Starlette ejecuta la cadena secuencial y sin
aislamiento: si una tarea lanza, la excepcion propaga y **las siguientes nunca
corren**. No es un defecto de nuestro codigo, es como funciona el mecanismo --
re-verificado el 2026-09-01 contra fastapi 0.139.2 / starlette 1.3.1, no leido
en la documentacion.

POR QUE NO SE VE CUANDO PASA. Bajo uvicorn la respuesta ya se emitio cuando
corren las tareas: el cliente ve 200. El traceback de la que lanzo queda en
`journalctl`; de la que NUNCA CORRIO no queda nada -- ni log, ni fila, ni
error. Es exactamente el modo de falla silencioso que la ronda de alertas vino
a eliminar, en otro mecanismo.

LA REGLA QUE ESTE ARCHIVO HACE CUMPLIR: nadie llama `add_task()` crudo. Todo
va por `jax_engine.background.add_safe_task`, que envuelve cada tarea en su
propio try/except. La regla es "ninguna", no "no mas de una": una sola tarea
hoy no lastima a nadie, pero la propiedad que la hace segura es que sea la
unica -- y eso no lo garantiza nada. El dia que alguien encole la segunda, el
defecto vuelve solo y en silencio.

Se exige mecanicamente (AST) y no por revision: una regla que depende de que
alguien se acuerde ya fallo dos veces en este repo.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient

from jax_engine.background import add_safe_task

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# El unico archivo que puede llamar add_task() crudo es el que provee el
# envoltorio, y tests/ porque el arnes de aca abajo TIENE que reproducir el
# bug para probar que lo detecta.
_EXENTOS = {BACKEND_ROOT / "jax_engine" / "background.py"}
_DIRS_EXCLUIDOS = {".venv", "venv", "__pycache__", "node_modules", ".git", "tests"}


# ---------------------------------------------------------------------------
# 1. El comportamiento real: el envoltorio aisla
# ---------------------------------------------------------------------------

def _app_con(encolar):
    """Construye una app cuyo endpoint encola dos tareas: la primera lanza."""
    corrio: list[str] = []
    app = FastAPI()

    async def explota():
        corrio.append("explota")
        raise RuntimeError("boom")

    async def segunda():
        corrio.append("segunda")

    @app.post("/dos")
    async def dos(bt: BackgroundTasks):
        encolar(bt, explota)
        encolar(bt, segunda)
        return {"ok": True}

    return app, corrio


def test_la_base_del_arnes_reproduce_el_bug_con_add_task_crudo():
    """Sin este test, el de abajo podria estar pasando por cualquier motivo.

    Fija que el arnes SI detecta el defecto real cuando esta presente: con
    `add_task` crudo, `segunda` no corre y la excepcion propaga.
    """
    app, corrio = _app_con(lambda bt, f: bt.add_task(f))
    with pytest.raises(RuntimeError):
        with TestClient(app) as c:
            c.post("/dos")
    assert corrio == ["explota"], "el arnes no esta reproduciendo el bug"


def test_una_tarea_que_lanza_no_se_lleva_puesta_la_siguiente():
    app, corrio = _app_con(add_safe_task)
    with TestClient(app) as c:
        r = c.post("/dos")
    assert r.status_code == 200
    assert corrio == ["explota", "segunda"], (
        "la segunda tarea tiene que correr aunque la primera haya lanzado"
    )


def test_la_excepcion_no_se_traga_en_silencio(caplog):
    """Fail-soft, pero RUIDOSO. Tragar el error sin dejar rastro cambiaria un
    modo de falla silencioso por otro."""
    app, _ = _app_con(add_safe_task)
    with caplog.at_level(logging.ERROR):
        with TestClient(app) as c:
            c.post("/dos")
    registros = [r for r in caplog.records if "background_task_failed" in r.getMessage()]
    assert registros, "la tarea que lanzo tiene que quedar registrada"
    assert "explota" in registros[0].getMessage(), "el log tiene que nombrar la tarea"
    assert registros[0].exc_info is not None, "y llevar el traceback"


def test_una_tarea_sincronica_tambien_queda_envuelta():
    """`add_task` acepta funciones sync; el envoltorio no puede perderlas."""
    corrio = []
    app = FastAPI()

    def sync_explota():
        corrio.append("sync_explota")
        raise RuntimeError("boom")

    def sync_segunda():
        corrio.append("sync_segunda")

    @app.post("/dos")
    async def dos(bt: BackgroundTasks):
        add_safe_task(bt, sync_explota)
        add_safe_task(bt, sync_segunda)
        return {"ok": True}

    with TestClient(app) as c:
        assert c.post("/dos").status_code == 200
    assert corrio == ["sync_explota", "sync_segunda"]


def test_los_argumentos_llegan_a_la_tarea():
    recibido = {}
    app = FastAPI()

    async def tarea(a, b=None):
        recibido["a"], recibido["b"] = a, b

    @app.post("/uno")
    async def uno(bt: BackgroundTasks):
        add_safe_task(bt, tarea, "posicional", b="nombrado")
        return {"ok": True}

    with TestClient(app) as c:
        c.post("/uno")
    assert recibido == {"a": "posicional", "b": "nombrado"}


# ---------------------------------------------------------------------------
# 2. El enforcement mecanico
# ---------------------------------------------------------------------------

def _archivos_de_produccion():
    for path in BACKEND_ROOT.rglob("*.py"):
        rel = path.relative_to(BACKEND_ROOT)
        if any(part in _DIRS_EXCLUIDOS for part in rel.parts):
            continue
        if path in _EXENTOS:
            continue
        yield path


def _llamadas_add_task_crudas(source: str, nombre: str) -> list[int]:
    """Lineas donde se llama `<algo>.add_task(...)`.

    Recorre TODO el arbol (`ast.walk`), no solo el nivel superior: una llamada
    escondida dentro de un `if`, de una funcion anidada o de un `try` cuenta
    igual -- el mecanismo se rompe en cualquiera de esos lugares.
    """
    tree = ast.parse(source, filename=nombre)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_task"
    ]


def test_el_codigo_real_no_llama_add_task_crudo():
    violaciones = []
    escaneados = 0
    for path in _archivos_de_produccion():
        escaneados += 1
        for lineno in _llamadas_add_task_crudas(path.read_text(encoding="utf-8"), str(path)):
            violaciones.append(f"{path.relative_to(BACKEND_ROOT)}:{lineno}")

    # El scanner P10 de jax estuvo meses en verde sobre CERO archivos. Este
    # assert existe para que eso no se pueda repetir aca.
    assert escaneados > 20, f"solo {escaneados} archivos escaneados -- el scanner no esta mirando nada"
    assert not violaciones, (
        "add_task() crudo (una tarea que lance se lleva puestas las encoladas "
        "despues, sin dejar rastro). Usar jax_engine.background.add_safe_task:\n"
        + "\n".join(violaciones)
    )


@pytest.mark.parametrize("mutacion,descripcion", [
    ("async def h(bt):\n    bt.add_task(f)\n", "llamada directa"),
    ("async def h(bt):\n    if cond:\n        bt.add_task(f)\n", "adentro de un if"),
    ("async def h(bt):\n    def anidada():\n        bt.add_task(f)\n", "en una funcion anidada"),
    ("async def h(bt):\n    try:\n        bt.add_task(f)\n    except Exception:\n        pass\n", "adentro de un try"),
    ("async def h(background_tasks):\n    background_tasks.add_task(f)\n", "otro nombre de variable"),
])
def test_el_scanner_detecta_la_mutacion(mutacion, descripcion):
    """Un scanner que nunca se probó contra una violación real no es un
    scanner: es un comentario que corre."""
    assert _llamadas_add_task_crudas(mutacion, "<mutacion>"), (
        f"el scanner NO detecto la violacion: {descripcion}"
    )


def test_el_scanner_no_grita_por_el_envoltorio():
    """`add_safe_task(bt, f)` es una llamada a funcion, no a `.add_task` --
    no debe contarse como violacion, o la regla seria imposible de cumplir."""
    assert not _llamadas_add_task_crudas(
        "async def h(bt):\n    add_safe_task(bt, f)\n", "<ok>"
    )
