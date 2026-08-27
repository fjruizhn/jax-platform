"""Mapeo de los 6 caminos de salida de _invoke_facet a `outcome` tipado.

TODO el I/O esta parcheado. Ningun test de este archivo llama a un
proveedor real -- ver "Riesgo de costo" en el plan."""
import asyncio
import pytest
from api import chat as chat_mod


def _capture(monkeypatch):
    """Reemplaza el escritor por un sink en memoria."""
    got = []
    async def fake_record(facet, outcome, source, detail=None):
        got.append({"facet": facet, "outcome": outcome,
                    "source": source, "detail": detail})
        return True
    monkeypatch.setattr(chat_mod, "record_facet_health", fake_record)
    return got


def _config():
    return {"personalities": {"jax_local": {"system_prompt": "x"},
                              "thot": {"system_prompt": "x"}}}


def test_unbound_se_registra_como_unbound(monkeypatch):
    got = _capture(monkeypatch)

    async def boom(facet): raise chat_mod.FacetUnavailableError("sin binding")
    monkeypatch.setattr(chat_mod, "resolve_facet", boom)

    texto, usage = asyncio.run(
        chat_mod._invoke_facet("thot", _config(), "u1", "hola"))

    assert usage is None
    assert [g["outcome"] for g in got] == ["unbound"]


def test_provider_error_se_registra_y_la_excepcion_SUBE(monkeypatch):
    got = _capture(monkeypatch)

    class _F:
        transport = "ollama"; model = "m"; provider_id = "p"
    async def ok_resolve(facet): return _F()
    async def boom(*a, **k): raise RuntimeError("proveedor caido")
    monkeypatch.setattr(chat_mod, "resolve_facet", ok_resolve)
    monkeypatch.setattr(chat_mod, "_call_ollama", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(chat_mod._invoke_facet("jax_local", _config(), "u1", "hola"))

    assert [g["outcome"] for g in got] == ["provider_error"]


def test_source_por_defecto_es_chat(monkeypatch):
    got = _capture(monkeypatch)
    async def boom(facet): raise chat_mod.FacetUnavailableError("x")
    monkeypatch.setattr(chat_mod, "resolve_facet", boom)

    asyncio.run(chat_mod._invoke_facet("thot", _config(), "u1", "hola"))
    assert got[0]["source"] == "chat"


def test_source_se_puede_pasar_como_keyword(monkeypatch):
    got = _capture(monkeypatch)
    async def boom(facet): raise chat_mod.FacetUnavailableError("x")
    monkeypatch.setattr(chat_mod, "resolve_facet", boom)

    asyncio.run(chat_mod._invoke_facet(
        "thot", _config(), "u1", "hola", source="canary_periodic"))
    assert got[0]["source"] == "canary_periodic"


# --- EL test de esta ronda -------------------------------------------------
# Los estados "gate deniega bien" y "gate deniega por error" son los UNICOS
# que hoy son indistinguibles, y ocurren DENTRO del gate. Si un refactor
# futuro los vuelve a colapsar, el sistema regresa exactamente al problema
# que esta ronda vino a cerrar -- y regresa en silencio.
#
# La asercion es sobre el outcome EXACTO, no sobre "no fue ok": un test que
# solo verifica `!= "ok"` pasaria igual si los dos estados colapsaran entre
# si, que es precisamente la regresion que hay que impedir.

class _Governed:
    transport = "http_openai_compat"
    model = "m"; provider_id = "p"; credential = "c"; base_url = "u"
    max_tokens_param = "max_tokens"; max_output_tokens = 100


def _gobernado(monkeypatch):
    async def resolve(facet): return _Governed()
    monkeypatch.setattr(chat_mod, "resolve_facet", resolve)


def test_gate_que_responde_NO_se_registra_como_gate_denied(monkeypatch):
    got = _capture(monkeypatch)
    _gobernado(monkeypatch)

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"allowed": False, "reason": "no autorizado"}
    class _Client:
        async def post(self, *a, **k): return _Resp()
    async def fake_client(): return _Client()
    monkeypatch.setattr(chat_mod, "get_http_client", fake_client)

    texto, usage = asyncio.run(
        chat_mod._invoke_facet("thot", _config(), "u1", "hola"))

    assert usage is None
    assert [g["outcome"] for g in got] == ["gate_denied"]


def test_gate_inalcanzable_se_registra_como_gate_unreachable(monkeypatch):
    got = _capture(monkeypatch)
    _gobernado(monkeypatch)

    async def fake_client(): raise RuntimeError("las_manos inalcanzable")
    monkeypatch.setattr(chat_mod, "get_http_client", fake_client)

    texto, usage = asyncio.run(
        chat_mod._invoke_facet("thot", _config(), "u1", "hola"))

    assert usage is None
    assert [g["outcome"] for g in got] == ["gate_unreachable"]


def test_los_dos_estados_del_gate_NO_colapsan_entre_si(monkeypatch):
    """El texto que ve el usuario es IDENTICO en los dos casos -- por eso
    hoy son indistinguibles. Lo que tiene que diferir es el outcome."""
    got = _capture(monkeypatch)
    _gobernado(monkeypatch)

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"allowed": False, "reason": "x"}
    class _Client:
        async def post(self, *a, **k): return _Resp()
    async def responde(): return _Client()
    monkeypatch.setattr(chat_mod, "get_http_client", responde)
    texto_a, _ = asyncio.run(chat_mod._invoke_facet("thot", _config(), "u1", "h"))

    async def no_responde(): raise RuntimeError("caido")
    monkeypatch.setattr(chat_mod, "get_http_client", no_responde)
    texto_b, _ = asyncio.run(chat_mod._invoke_facet("thot", _config(), "u1", "h"))

    assert texto_a == texto_b                    # el usuario ve lo mismo
    assert got[0]["outcome"] != got[1]["outcome"]  # el operador NO
    assert [g["outcome"] for g in got] == ["gate_denied", "gate_unreachable"]
