"""SP3 del Ejecutor (2026-09-17): la Mesa toma su carril alrededor de la llamada a Ollama.

La Mesa y el proxy del Ejecutor comparten una GPU con `OLLAMA_NUM_PARALLEL=1`. El proxy
(jax/ejecutor/proxy_carril.py) SONDEA `mesa.lock` y no manda nada mientras esté tomado: si
la Mesa no lo toma, el Ejecutor se cuela delante de cada turno de persona (U5: p95 62,71 s).

- El lock se toma con `asyncio.to_thread` (copia de jax/ejecutor/prioridad.py): un `flock`
  bloqueante congelaría el event loop de TODA la plataforma (LAS CUATRO DEL RENDIMIENTO, 3).
- Entre PROCESOS: el otro lado es otro proceso, así que el que retiene el carril en estos
  tests es un proceso aparte (`fork`), no una tarea.
- `facet_canary` sondea jax_local por `_invoke_facet` → `_call_ollama`: entra por el mismo
  carril sin código propio. Lo fija el test estructural de abajo.
- Sin JAX_PROXY_CARRIL_RAIZ (la misma variable que lee el proxy, así los dos miran el mismo
  directorio) no hay llamada: fail-closed, y la app no arranca.
"""
from __future__ import annotations

import ast
import asyncio
import multiprocessing as mp
import time
from pathlib import Path

import pytest

import http_client
from ejecutor.prioridad import carril_mesa, hay_mesa_esperando

BACKEND = Path(__file__).resolve().parents[1]
_CTX = mp.get_context("fork")


def _rematar(p):
    p.join(5)
    if p.is_alive():
        p.terminate()
        p.join(5)


def _retener(raiz, listo, suelte, hasta_s):
    with carril_mesa(raiz):
        listo.set()
        suelte.wait(hasta_s)


class _Respuesta:
    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": "hola"}, "prompt_eval_count": 3, "eval_count": 1}


class _ClienteQueMira:
    """Anota, en el momento del POST, si el carril de la Mesa estaba tomado y cuándo llegó."""

    def __init__(self, raiz, fallar=False):
        self.raiz, self.fallar, self.posts = raiz, fallar, []

    async def post(self, url, **kw):
        self.posts.append((time.monotonic(), hay_mesa_esperando(self.raiz), url))
        if self.fallar:
            raise RuntimeError("upstream_caido")
        return _Respuesta()


@pytest.fixture
def carril(tmp_path, monkeypatch):
    monkeypatch.setenv("JAX_OLLAMA_URL", "http://ollama.invalid:11434")
    monkeypatch.setenv("JAX_PROXY_CARRIL_RAIZ", str(tmp_path))
    original = http_client._client
    yield tmp_path
    http_client._client = original


async def _llamar():
    from api.chat import _call_ollama
    return await _call_ollama("sistema", [], "hola", {"personalities": {"jax_local": {}}}, "qwen-de-prueba")


async def test_la_llamada_a_ollama_va_dentro_del_carril_y_lo_suelta(carril):
    cli = http_client._client = _ClienteQueMira(carril)
    assert (await _llamar())[0] == "hola"
    assert [tomado for _, tomado, _ in cli.posts] == [True], "el POST a Ollama salió sin el carril de la Mesa"
    assert hay_mesa_esperando(carril) is False, "el carril quedó tomado después de la respuesta"


async def test_si_ollama_falla_el_carril_se_suelta(carril):
    http_client._client = _ClienteQueMira(carril, fallar=True)
    with pytest.raises(RuntimeError):
        await _llamar()
    assert hay_mesa_esperando(carril) is False


def test_con_el_carril_tomado_por_otro_proceso_la_mesa_espera_sin_congelar_el_loop_y_luego_entra(carril):
    listo, suelte = _CTX.Event(), _CTX.Event()
    p = _CTX.Process(target=_retener, args=(carril, listo, suelte, 10))
    p.start()
    try:
        assert listo.wait(5) is True
        cli = http_client._client = _ClienteQueMira(carril)

        async def escenario():
            latidos = 0
            llamada = asyncio.create_task(_llamar())
            t0 = time.monotonic()
            while time.monotonic() - t0 < 0.5:
                await asyncio.sleep(0.01)
                latidos += 1
            assert not llamada.done() and cli.posts == [], "la Mesa no esperó al otro proceso"
            soltado = time.monotonic()
            suelte.set()
            await asyncio.wait_for(llamada, 5)
            return latidos, soltado

        latidos, soltado = asyncio.run(escenario())
        # 0,5 s a pasos de 10 ms: un loop congelado por un flock bloqueante daría ~1.
        assert latidos >= 20, f"el event loop se congeló mientras la Mesa esperaba ({latidos} latidos)"
        ((llego, tomado, _),) = cli.posts
        assert llego >= soltado and tomado is True
    finally:
        suelte.set()
        _rematar(p)


async def test_sin_raiz_del_carril_no_hay_llamada(carril, monkeypatch):
    monkeypatch.delenv("JAX_PROXY_CARRIL_RAIZ")
    cli = http_client._client = _ClienteQueMira(carril)
    with pytest.raises(RuntimeError) as e:
        await _llamar()
    assert type(e.value).__name__ == "EntornoInvalido" and "JAX_PROXY_CARRIL_RAIZ" in str(e.value)
    assert cli.posts == []


@pytest.mark.parametrize("valor", [None, "", "relativa/locks"])
def test_sin_raiz_del_carril_valida_la_app_no_arranca(monkeypatch, valor):
    import main
    from unittest.mock import AsyncMock
    if valor is None:
        monkeypatch.delenv("JAX_PROXY_CARRIL_RAIZ", raising=False)
    else:
        monkeypatch.setenv("JAX_PROXY_CARRIL_RAIZ", valor)
    llego_a_la_db = AsyncMock(side_effect=AssertionError("abrió el pool sin validar el carril"))
    monkeypatch.setattr(main, "get_pool", llego_a_la_db)

    async def arrancar():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(RuntimeError) as e:
        asyncio.run(arrancar())
    assert type(e.value).__name__ == "EntornoInvalido" and "JAX_PROXY_CARRIL_RAIZ" in str(e.value)
    llego_a_la_db.assert_not_awaited()


def test_los_tests_no_usan_el_carril_de_produccion():
    # conftest carga /etc/jax/.env: un test que tomara /var/lib/jax-carril/mesa.lock frenaría al
    # Ejecutor de producción mientras dura.
    import conftest
    import os
    assert os.environ["JAX_PROXY_CARRIL_RAIZ"] == conftest.RAIZ_DEL_CARRIL_DE_PRUEBA
    assert not conftest.RAIZ_DEL_CARRIL_DE_PRUEBA.startswith("/var/lib/")


def _llamadas_a_ollama(ruta: Path):
    """Cada `.post(...)` cuyo primer argumento usa `_url_de_ollama`, con sus ancestros."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    padres = {hijo: nodo for nodo in ast.walk(arbol) for hijo in ast.iter_child_nodes(nodo)}
    for funcion in ast.walk(arbol):
        if not isinstance(funcion, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Nombres locales a los que se les asignó algo con la URL de Ollama (`url = f"{_url_de_ollama()}..."`).
        urls = {n.targets[0].id for n in ast.walk(funcion) if isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name) and "_url_de_ollama" in ast.unparse(n.value)}
        for nodo in ast.walk(funcion):
            if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr in {"post", "stream", "send"}:
                texto = ast.unparse(nodo.args[0]) if nodo.args else ""
                if "_url_de_ollama" in texto or texto in urls:
                    cadena, n = [], nodo
                    while n in padres:
                        n = padres[n]
                        cadena.append(n)
                    yield nodo, cadena


def test_toda_llamada_de_la_plataforma_a_ollama_va_dentro_del_carril():
    encontradas = 0
    for ruta in BACKEND.rglob("*.py"):
        if ".venv" in ruta.parts or "tests" in ruta.parts:
            continue
        for nodo, cadena in _llamadas_a_ollama(ruta):
            encontradas += 1
            dentro = any(isinstance(a, ast.AsyncWith) and any("carril_mesa_async" in ast.unparse(i.context_expr)
                                                                for i in a.items) for a in cadena)
            assert dentro, f"{ruta.relative_to(BACKEND)}:{nodo.lineno} llama a Ollama fuera del carril de la Mesa"
    assert encontradas >= 1, "el escaneo no encontró la llamada de la Mesa: no está mirando nada"


def test_la_sonda_llega_a_ollama_solo_por_invoke_facet():
    # facet_canary no tiene llamada propia a Ollama: si algún día la tiene, el test de arriba la
    # exige dentro del carril; este fija que hoy entra por el dispatch del chat.
    fuente = (BACKEND / "jax_engine" / "facet_canary.py").read_text(encoding="utf-8")
    assert "_url_de_ollama" not in fuente and "11434" not in fuente
    assert "await _invoke_facet(" in fuente


def test_la_copia_de_motivo_no_tiene_otro_simbolo_y_es_inmutable():
    from ejecutor.motivo import Motivo
    arbol = ast.parse((BACKEND / "ejecutor" / "motivo.py").read_text(encoding="utf-8"))
    assert {n.name for n in arbol.body if hasattr(n, "name")} == {"Motivo"}
    with pytest.raises(AttributeError):
        Motivo("x").codigo = "y"
