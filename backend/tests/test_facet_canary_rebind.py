"""La sonda por rebinding cuelga de los DOS escritores de facet_binding.
I/O parcheado; no se llama a ningun proveedor."""
import asyncio
import inspect
from types import SimpleNamespace

import facet_resolver
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


# --- Ronda de corrección 1 -------------------------------------------------
# Hallazgo 2: invalidate_facet_cache no tenía ningún test de COMPORTAMIENTO
# -- los tres tests de arriba la monkeypatchean. Si el pop usara una clave
# distinta a la que usa resolve_facet() (p.ej. si algún día la clave del
# caché incorpora role/provider), sería un no-op silencioso: la sonda
# resolvería el binding VIEJO y reportaría verde, con los tres tests de
# arriba sin detectarlo porque ninguno ejercita la función real.

def test_invalidate_facet_cache_saca_la_clave_que_usa_resolve_facet(monkeypatch):
    """No monkeypatchea invalidate_facet_cache -- ejercita la función real
    contra la MISMA clave (facet_key) que resolve_facet() usa para poblar
    _cache (facet_resolver.py:153: `_cache[facet_key] = ...`)."""
    cache = {}
    monkeypatch.setattr(facet_resolver, "_cache", cache)
    cache["thot"] = facet_resolver._CacheEntry(value=object(), fetched_at=0.0)

    assert facet_resolver.invalidate_facet_cache("thot") is True
    assert "thot" not in cache


def test_invalidate_facet_cache_devuelve_false_si_no_habia_entrada(monkeypatch):
    monkeypatch.setattr(facet_resolver, "_cache", {})
    assert facet_resolver.invalidate_facet_cache("nada_cacheado") is False


# Hallazgo 3: el kill switch (CANARY_INTERVAL_SECONDS <= 0) apagaba solo
# start_facet_canary. probe_after_rebind no lo consultaba: un operador que
# apaga la sonda para cortar llamadas pagas seguía pagando una por cada
# approve y por cada PUT a facet-bindings. El fix gatea SOLO el sondeo --
# invalidate_facet_cache corre siempre, apagada o no, porque no es parte de
# la sonda sino una corrección de frescura para el turno de chat siguiente.

def test_kill_switch_apagado_no_sondea_pero_si_invalida(monkeypatch):
    orden = []
    monkeypatch.setattr(facet_canary, "CANARY_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(facet_canary, "invalidate_facet_cache",
                        lambda k: orden.append(("invalidate", k)) or True)
    async def fake_probe(facet, config, source):
        orden.append(("probe", facet, source)); return None
    monkeypatch.setattr(facet_canary, "probe_facet", fake_probe)

    resultado = asyncio.run(facet_canary.probe_after_rebind("thot"))

    assert orden == [("invalidate", "thot")]  # el probe NO corrio
    assert resultado is None


def test_kill_switch_encendido_invalida_y_sondea(monkeypatch):
    orden = []
    monkeypatch.setattr(facet_canary, "CANARY_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(facet_canary, "invalidate_facet_cache",
                        lambda k: orden.append(("invalidate", k)) or True)
    async def fake_probe(facet, config, source):
        orden.append(("probe", facet, source)); return None
    monkeypatch.setattr(facet_canary, "probe_facet", fake_probe)
    monkeypatch.setattr(facet_canary, "_load_config",
                        lambda: {"personalities": {"thot": {}}})

    resultado = asyncio.run(facet_canary.probe_after_rebind("thot"))

    assert orden == [("invalidate", "thot"),
                     ("probe", "thot", "canary_rebind")]
    assert resultado is None


# Hallazgo 4: _load_config() corría FUERA del try de probe_facet, dentro de
# una BackgroundTask -- si lanzaba, Starlette ya había emitido el 200 al
# admin y no quedaba NINGUNA fila en facet_health_event. La sonda no ocurrio
# y el reaper no tiene con que enterarse (verificado empiricamente contra el
# FastAPI de este venv: una excepcion en una BackgroundTask propaga -- el
# journal la ve, la tabla no -- y aborta cualquier BackgroundTask encolada
# despues en la misma request). Fix: TODO el cuerpo va adentro de un unico
# try, invalidate_facet_cache incluido, y el except SIEMPRE registra
# 'probe_error' con source='canary_rebind' en vez de re-lanzar.

def test_fallo_antes_de_sondear_registra_probe_error(monkeypatch):
    monkeypatch.setattr(facet_canary, "CANARY_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(facet_canary, "invalidate_facet_cache", lambda k: True)

    def fake_load_config_explota():
        raise RuntimeError("config.toml roto")
    monkeypatch.setattr(facet_canary, "_load_config", fake_load_config_explota)

    grabado = {}
    async def fake_record(facet, outcome, source, detail=None):
        grabado["facet"], grabado["outcome"], grabado["source"] = facet, outcome, source
        return True
    monkeypatch.setattr(facet_canary, "record_facet_health", fake_record)

    resultado = asyncio.run(facet_canary.probe_after_rebind("thot"))

    assert resultado == "probe_error"
    assert grabado == {"facet": "thot", "outcome": "probe_error", "source": "canary_rebind"}


def test_fallo_en_invalidate_facet_cache_tambien_registra_probe_error(monkeypatch):
    """El try envuelve invalidate_facet_cache tambien -- no solo
    _load_config(). Si esto no estuviera cubierto, un fallo en la
    invalidacion propagaria sin dejar fila, el mismo silencio que el
    Hallazgo 4 vino a cerrar para _load_config()."""
    def fake_invalidate_explota(k):
        raise RuntimeError("_cache corrupto")
    monkeypatch.setattr(facet_canary, "invalidate_facet_cache", fake_invalidate_explota)

    grabado = {}
    async def fake_record(facet, outcome, source, detail=None):
        grabado["facet"], grabado["outcome"], grabado["source"] = facet, outcome, source
        return True
    monkeypatch.setattr(facet_canary, "record_facet_health", fake_record)

    resultado = asyncio.run(facet_canary.probe_after_rebind("thot"))

    assert resultado == "probe_error"
    assert grabado == {"facet": "thot", "outcome": "probe_error", "source": "canary_rebind"}


# Hallazgo 5: test_los_dos_escritores_reciben_background_tasks (arriba) solo
# mira la FIRMA -- borrar la línea `add_task` de un escritor lo deja en
# verde igual. Estos dos verifican el EFECTO: que cada endpoint encola
# probe_after_rebind de verdad, con un doble de BackgroundTasks y un pool
# de DB falso (sin tocar MariaDB).

class _FakeCursor:
    def __init__(self, sink, fetchone_results):
        self.sink = sink
        self._fetchone_results = fetchone_results

    async def execute(self, sql, params=None):
        self.sink.append((sql, params))

    async def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, sink, fetchone_results):
        self.sink = sink
        self._fetchone_results = fetchone_results

    def cursor(self):
        return _FakeCursor(self.sink, self._fetchone_results)

    async def commit(self):
        self.sink.append(("COMMIT", None))

    async def rollback(self):
        self.sink.append(("ROLLBACK", None))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, sink, fetchone_results):
        self.sink = sink
        self.fetchone_results = fetchone_results

    def acquire(self):
        return _FakeConn(self.sink, self.fetchone_results)


class _FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


def test_approve_proposal_encola_probe_after_rebind(monkeypatch):
    sink = []
    async def fake_get_pool():
        return _FakePool(sink, [("thot", 42, "pending")])
    monkeypatch.setattr(models_mod, "get_pool", fake_get_pool)

    bg = _FakeBackgroundTasks()
    user = SimpleNamespace(user_id="1")

    asyncio.run(models_mod.approve_proposal(1, background_tasks=bg, user=user))

    assert len(bg.tasks) == 1
    func, args, kwargs = bg.tasks[0]
    assert func is facet_canary.probe_after_rebind
    assert args == ("thot",)


def test_update_facet_binding_encola_probe_after_rebind(monkeypatch):
    sink = []
    async def fake_get_pool():
        return _FakePool(sink, [("thot",)])
    monkeypatch.setattr(fb_mod, "get_pool", fake_get_pool)

    bg = _FakeBackgroundTasks()
    user = SimpleNamespace(user_id="1")
    req = fb_mod.UpdateBindingRequest(provider_id="deepseek", model_ref=42)

    asyncio.run(fb_mod.update_facet_binding("thot", req=req, background_tasks=bg, user=user))

    assert len(bg.tasks) == 1
    func, args, kwargs = bg.tasks[0]
    assert func is facet_canary.probe_after_rebind
    assert args == ("thot",)
