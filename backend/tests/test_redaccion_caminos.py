"""Task 6 S1 (2026-09-15): la key de Gemini no queda en claro en NINGUN camino
que guarda, loguea o devuelve un error de proveedor.

Caminos (hallazgos-laterales.md §3):
  1. chat -> _invoke_facet -> record_facet_health -> facet_health_event.detail
  2. sonda por rebind (facet_canary.probe_after_rebind) -> mismo escritor
  3. POST /api/admin/models/sync -> log + providers[].error

Todo el HTTP pasa por httpx.MockTransport: sin red, sin LAS MANOS, sin Google.
La key es FALSA. Los tests `_db` escriben en jax_memory_test (conftest) y se
saltean sin DB; los demas son puros (pool falso) y corren tambien sin DB.
"""
import asyncio
import logging
import uuid

import httpx
import pytest

import facet_health
import model_catalog
from api import chat as chat_mod
from api.admin import models as models_mod
from auth.jwt import create_access_token
from auth.models import AuthUser
from jax_engine import facet_canary

KEY = "AIzaFAKE-task6-0123456789abcdef"


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/motor/authorize-facet"):
        return httpx.Response(200, json={"allowed": True})
    return httpx.Response(400, json={"error": {"message": "API key not valid"}})


class _Gemini:
    transport = "http_gemini"
    model = "gemini-2.5-pro"
    provider_id = "gemini"
    credential = KEY
    base_url = None
    max_tokens_param = None
    max_output_tokens = None


def _parchear_gemini_que_falla(monkeypatch):
    async def resolve(facet):
        return _Gemini()

    async def cliente():
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    monkeypatch.setattr(chat_mod, "resolve_facet", resolve)
    monkeypatch.setattr(chat_mod, "get_http_client", cliente)


def _config():
    return {"personalities": {"jax_local": {"system_prompt": "x"},
                              "thot": {"system_prompt": "x"}}}


# --- pool falso: captura el INSERT real de record_facet_health ---------------
class _Cur:
    def __init__(self, sink): self.sink = sink
    async def execute(self, sql, params=None): self.sink.append((sql, params))
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Conn:
    def __init__(self, sink): self.sink = sink
    def cursor(self): return _Cur(self.sink)
    async def commit(self): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Pool:
    def __init__(self, sink): self.sink = sink
    def acquire(self): return _Conn(self.sink)


def _sink(monkeypatch):
    sink = []

    async def pool():
        return _Pool(sink)

    monkeypatch.setattr(facet_health, "get_pool", pool)
    return sink


def _details(sink):
    return [p[3] for sql, p in sink if "INSERT INTO facet_health_event" in sql]


# --- 1. chat ------------------------------------------------------------------
def test_chat_gemini_con_error_no_guarda_ni_loguea_la_key(monkeypatch, caplog):
    sink = _sink(monkeypatch)
    _parchear_gemini_que_falla(monkeypatch)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(httpx.HTTPStatusError) as exc:
        asyncio.run(chat_mod._invoke_facet("thot", _config(), "u1", "hola"))
    # T6-2 (2026-09-15): la key viaja en la cabecera x-goog-api-key, asi que
    # la excepcion de httpx YA NO la trae (antes este control afirmaba lo
    # contrario). La redaccion del escritor sigue probada abajo, con textos
    # que si la traen (defensa en profundidad).
    assert KEY not in str(exc.value)

    details = _details(sink)
    assert len(details) == 1
    assert details[0].startswith("HTTPStatusError: ")
    assert "400" in details[0]
    assert KEY not in details[0]
    assert KEY not in caplog.text


def test_record_facet_health_redacta_en_el_punto_de_escritura(monkeypatch):
    """Un llamador nuevo que pase str(e) crudo tampoco la filtra."""
    sink = _sink(monkeypatch)
    asyncio.run(facet_health.record_facet_health(
        "thot", "provider_error", "chat",
        f"x for url 'https://g.example/m?key={KEY}'"))
    assert KEY not in _details(sink)[0]


def test_record_facet_health_redacta_antes_de_truncar(monkeypatch):
    """Si truncara primero, una key cortada a la mitad ya no tendria forma
    reconocible y su prefijo quedaria en la fila."""
    sink = _sink(monkeypatch)
    relleno = "x" * 200
    asyncio.run(facet_health.record_facet_health(
        "thot", "provider_error", "chat", f"{relleno} ?key={KEY}"))
    assert "AIzaFAKE" not in _details(sink)[0]


# --- 2. sonda por rebind --------------------------------------------------------
def test_sonda_por_rebind_no_guarda_la_key(monkeypatch):
    sink = _sink(monkeypatch)

    def invalidar(facet_key):
        raise RuntimeError(f"fallo con url https://g.example/m?key={KEY}")

    monkeypatch.setattr(facet_canary, "invalidate_facet_cache", invalidar)
    asyncio.run(facet_canary.probe_after_rebind("thot"))
    details = _details(sink)
    assert details and KEY not in details[0]


# --- 3. sync de modelos ---------------------------------------------------------
def _parchear_sync_que_falla(monkeypatch):
    async def sync(provider_id):
        req = httpx.Request("GET", f"https://generativelanguage.googleapis.com/v1beta/models?key={KEY}")
        httpx.Response(403, request=req).raise_for_status()

    async def enrich():
        raise RuntimeError(f"models.dev con token=tok-FAKE-zzz999 y {KEY}")

    monkeypatch.setattr(model_catalog, "sync_provider_models", sync)
    monkeypatch.setattr(model_catalog, "enrich_from_models_dev", enrich)


def test_sync_de_modelos_no_devuelve_ni_loguea_la_key(monkeypatch, caplog):
    _parchear_sync_que_falla(monkeypatch)
    caplog.set_level(logging.DEBUG)
    body = asyncio.run(models_mod.sync_models(
        user=AuthUser(user_id="1", tenant_id="1", role="superadmin")))
    texto = repr(body)
    assert body["ok"] is False
    assert "403" in body["providers"][0]["error"]
    assert KEY not in texto
    assert "tok-FAKE-zzz999" not in texto
    assert KEY not in caplog.text
    assert "tok-FAKE-zzz999" not in caplog.text


# --- con DB (jax_memory_test) --------------------------------------------------
async def _detail_guardado(facet):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT detail FROM facet_health_event WHERE facet=%s ORDER BY id DESC", (facet,))
            filas = await cur.fetchall()
            await cur.execute("DELETE FROM facet_health_event WHERE facet=%s", (facet,))
        await conn.commit()
    return [f[0] for f in filas]


def test_chat_gemini_con_error_no_deja_la_key_en_la_db(client, monkeypatch, caplog):
    _parchear_gemini_que_falla(monkeypatch)
    caplog.set_level(logging.DEBUG)
    facet = f"t6-{uuid.uuid4().hex[:10]}"

    async def invocar():
        try:
            await chat_mod._invoke_facet(facet, _config(), "u1", "hola")
        except httpx.HTTPStatusError:
            return True
        return False

    assert client.portal.call(invocar) is True
    details = client.portal.call(_detail_guardado, facet)
    assert len(details) == 1
    assert details[0].startswith("HTTPStatusError: ")
    assert KEY not in details[0]
    assert KEY not in caplog.text


def test_sync_de_modelos_por_http_no_devuelve_la_key(client, monkeypatch, caplog):
    _parchear_sync_que_falla(monkeypatch)
    caplog.set_level(logging.DEBUG)
    token = create_access_token("1", "1", "superadmin")
    resp = client.post("/api/admin/models/sync", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is False
    assert KEY not in resp.text
    assert KEY not in caplog.text
