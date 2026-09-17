"""jax-platform presenta su credencial de servicio a LAS MANOS (2026-09-17).

LAS MANOS exige credencial en toda ruta salvo /health y saca la identidad de la
credencial (jax: las_manos/auth_servicio.py). Si un pedido de este backend a
LAS MANOS sale sin `headers=encabezados_las_manos()`, LAS MANOS responde 401 y
la función de la Mesa que lo usa deja de andar. El guard de abajo recorre TODO
el backend: una llamada nueva (p. ej. /jacobs/preflight o /continue) sin la
credencial pone esto en rojo antes del despliegue.

Puro: sin DB, sin red.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import ast
import asyncio
import secrets
from pathlib import Path

import pytest

import credencial_las_manos as cred
from config_entorno import EntornoInvalido

BACKEND = Path(__file__).resolve().parents[1]
_BASES = ("LAS_MANOS_URL", "JACOBS_URL")
_METODOS = {"get", "post", "put", "patch", "delete", "request", "stream"}


def _pedidos_a_las_manos():
    for ruta in sorted(BACKEND.rglob("*.py")):
        partes = ruta.relative_to(BACKEND).parts
        if partes[0] in {"tests", ".venv", "venv"} or "site-packages" in partes:
            continue
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        for n in ast.walk(arbol):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in _METODOS and n.args):
                continue
            destino = ast.unparse(n.args[0])
            if any(b in destino for b in _BASES):
                yield ruta.relative_to(BACKEND), n, destino


def test_hay_pedidos_a_las_manos_que_vigilar():
    # Si el guard deja de encontrar llamadas, no vigila nada.
    assert len([p for p in _pedidos_a_las_manos() if "/health" not in p[2]]) >= 7


def test_todo_pedido_a_las_manos_salvo_health_lleva_la_credencial():
    faltan = []
    for ruta, n, destino in _pedidos_a_las_manos():
        if destino.endswith("/health'") or destino.endswith('/health"'):
            continue
        headers = [k for k in n.keywords if k.arg == "headers"]
        if not headers or ast.unparse(headers[0].value) != "encabezados_las_manos()":
            faltan.append(f"{ruta}:{n.lineno} {destino}")
    assert not faltan, "pedidos a LAS MANOS sin credencial de servicio:\n" + "\n".join(faltan)


def test_nombres_iguales_a_los_de_las_manos():
    assert cred.ENCABEZADO == "X-Jax-Credencial-Servicio"
    assert cred.VARIABLE == "JAX_LAS_MANOS_CREDENCIAL_PLATAFORMA"


def test_encabezado_sale_del_entorno(monkeypatch):
    valor = secrets.token_urlsafe(32)
    monkeypatch.setenv(cred.VARIABLE, valor)
    assert cred.encabezados_las_manos() == {cred.ENCABEZADO: valor}


@pytest.mark.parametrize("valor", [None, "", "   ", "a" * 42, "a" * 42 + " b"])
def test_sin_credencial_valida_el_pedido_no_sale(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv(cred.VARIABLE, raising=False)
    else:
        monkeypatch.setenv(cred.VARIABLE, valor)
    with pytest.raises(EntornoInvalido):
        cred.encabezados_las_manos()


def test_el_sondeo_de_pipelines_presenta_la_credencial(monkeypatch):
    """De punta a punta en el sondeo real de jax_engine.state: la cabecera viaja."""
    from jax_engine.schemas import PipelineState
    from jax_engine.state import engine_state

    valor = secrets.token_urlsafe(32)
    monkeypatch.setenv(cred.VARIABLE, valor)
    vistas = []

    class _Resp:
        status_code = 401

    class _Cliente:
        async def get(self, url, **kw):
            vistas.append(kw.get("headers"))
            return _Resp()

    pipeline = PipelineState(pipeline_id="p1", tenant_id="t", user_id="u", name="n", status="running")
    asyncio.run(engine_state._poll_one_pipeline(_Cliente(), "p1", pipeline))
    assert vistas == [{cred.ENCABEZADO: valor}]
