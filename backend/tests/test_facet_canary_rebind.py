"""La sonda por rebinding cuelga de los DOS escritores de facet_binding.
I/O parcheado; no se llama a ningun proveedor."""
import asyncio
import inspect
from api.admin import models as models_mod
from api.admin import facet_bindings as fb_mod
from jax_engine import facet_canary


def test_los_dos_escritores_reciben_background_tasks():
    """Si un escritor no acepta BackgroundTasks, no puede encolar la sonda
    -- y ese es exactamente el bug que ya paso con motor.model_ref."""
    for fn in (models_mod.approve_proposal, fb_mod.update_facet_binding):
        params = inspect.signature(fn).parameters
        assert any("BackgroundTasks" in str(p.annotation) for p in params.values()), \
            f"{fn.__name__} no recibe BackgroundTasks"


def test_probe_after_rebind_usa_source_canary_rebind(monkeypatch):
    got = {}
    async def fake_probe(facet, config, source):
        got["facet"], got["source"] = facet, source
        return None
    monkeypatch.setattr(facet_canary, "probe_facet", fake_probe)
    monkeypatch.setattr(facet_canary, "_load_config",
                        lambda: {"personalities": {"thot": {}}})

    asyncio.run(facet_canary.probe_after_rebind("thot"))
    assert got == {"facet": "thot", "source": "canary_rebind"}


def test_probe_after_rebind_invalida_la_cache_ANTES_de_sondear(monkeypatch):
    """Sin esto, la sonda por rebinding valida el binding viejo durante la
    ventana de 30s del TTL y reporta `ok` sobre el nuevo -- verde sobre el
    rebinding recien hecho, el escenario del 2026-08-24."""
    orden = []
    monkeypatch.setattr(facet_canary, "invalidate_facet_cache",
                        lambda k: orden.append(("invalidate", k)) or True)
    async def fake_probe(facet, config, source):
        orden.append(("probe", facet, source)); return None
    monkeypatch.setattr(facet_canary, "probe_facet", fake_probe)
    monkeypatch.setattr(facet_canary, "_load_config",
                        lambda: {"personalities": {"thot": {}}})

    asyncio.run(facet_canary.probe_after_rebind("thot"))

    # El ORDEN es la aserción, no la mera presencia de los dos.
    assert orden == [("invalidate", "thot"),
                     ("probe", "thot", "canary_rebind")]
