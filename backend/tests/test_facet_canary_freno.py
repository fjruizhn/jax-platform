"""Con el freno puesto, la sonda de facets no llama a proveedores (2026-09-17).

Ruling del controlador principal sobre la pregunta de R19 del frente B: el
freno significa que JAX no invoca músculos, y la sonda periódica y la sonda
por rebinding también son invocaciones (pagas) a un proveedor. Con el freno
puesto se saltan, dejan rastro en el log y NO escriben facet_health_event:
un salto no es una caída, y una fila de error haría que el reaper alertara
facetas sanas.

El freno se pone de verdad (el archivo de la sesión, JAX_KILL_SWITCH_PATH
del conftest), no con un monkeypatch de `activo`: se prueba el mismo camino
que lee la Mesa. NINGÚN test llama a un proveedor: `_invoke_facet` es un espía."""
import asyncio
import logging
import os

import pytest

import facet_health
from jax_engine import facet_canary


def _config():
    return {"personalities": {"jax_local": {}, "hyde": {}, "thot": {}, "ada": {}}}


def _poner_freno():
    with open(os.environ["JAX_KILL_SWITCH_PATH"], "w") as f:
        f.write("test\n")


@pytest.fixture
def espias(monkeypatch):
    llamadas = {"invoke": [], "record": [], "invalidate": []}

    async def invoke(facet, config, user_id, message, semantic_context=None, *, source="chat"):
        llamadas["invoke"].append((facet, source))
        return "listo", None

    async def record(facet, outcome, source, detail=None):
        llamadas["record"].append((facet, outcome, source))
        return True

    monkeypatch.setattr(facet_canary, "_invoke_facet", invoke)
    monkeypatch.setattr(facet_canary, "record_facet_health", record)
    monkeypatch.setattr(facet_canary, "invalidate_facet_cache",
                        lambda k: llamadas["invalidate"].append(k) or True)
    monkeypatch.setattr(facet_canary, "_load_config", _config)
    monkeypatch.setattr(facet_canary, "CANARY_INTERVAL_SECONDS", 3600)
    return llamadas


def _saltada():
    return getattr(facet_canary, "SALTADA_POR_FRENO", object())


def test_probe_facet_con_el_freno_puesto_no_invoca_ni_registra(espias, caplog):
    _poner_freno()
    with caplog.at_level(logging.WARNING):
        out = asyncio.run(facet_canary.probe_facet("thot", _config(), facet_canary.SOURCE_CANARY_PERIODIC))

    assert not espias["invoke"] == [], "la sonda llamó al proveedor con el freno puesto"
    assert espias["record"] == [], "un salto por freno no es una caída: no se escribe fila"
    assert out == _saltada()
    assert any("thot" in r.getMessage() and "freno" in r.getMessage() for r in caplog.records), \
        "el salto no dejó rastro en el log"


def test_probe_all_con_el_freno_puesto_no_invoca_ninguna_faceta(espias):
    _poner_freno()
    resultados = asyncio.run(facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC))

    assert espias["invoke"] == []
    assert espias["record"] == []
    assert resultados == [_saltada()] * len(facet_canary.canary_facets(_config()))


def test_probe_after_rebind_con_el_freno_puesto_invalida_pero_no_sondea(espias):
    """La invalidación de la caché no es una invocación: sigue corriendo
    (el próximo turno, al soltar el freno, tiene que ver el binding nuevo)."""
    _poner_freno()
    out = asyncio.run(facet_canary.probe_after_rebind("thot"))

    assert espias["invalidate"] == ["thot"]
    assert espias["invoke"] == []
    assert espias["record"] == []
    assert out == _saltada()


def test_con_el_freno_suelto_la_sonda_invoca(espias):
    """Control: sin el freno, el mismo arnés sí llega al proveedor (espía)."""
    assert not os.path.exists(os.environ["JAX_KILL_SWITCH_PATH"])
    out = asyncio.run(facet_canary.probe_after_rebind("thot"))

    assert espias["invoke"] == [("thot", facet_canary.SOURCE_CANARY_REBIND)]
    assert out is None


def test_el_salto_no_es_un_outcome_de_salud():
    """Si fuera un outcome, algún llamador podría terminar escribiéndolo como
    fila y el lector lo vería como no-ok."""
    assert facet_canary.SALTADA_POR_FRENO not in facet_health.OUTCOMES
