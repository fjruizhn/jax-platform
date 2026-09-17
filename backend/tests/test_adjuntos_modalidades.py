"""Frente D (2026-09-16): "acepta imagen" sale de model.input_modalities por
el MISMO JOIN que ya trae el modelo (facet_resolver._query_facet), y el sync
de Ollama la llena desde /api/show -- antes ninguna fila de Ollama podía
decir otra cosa que 'text' (Discrepancia 1 del plan)."""
import pytest

import facet_resolver
import http_client
import model_catalog


@pytest.mark.parametrize("crudo,esperado", [
    ("text,image", frozenset({"text", "image"})),
    ("text", frozenset({"text"})),
    ("", frozenset()),
    (None, frozenset()),
    ({"text", "image"}, frozenset({"text", "image"})),
])
def test_modalidades_normaliza_lo_que_devuelva_el_driver(crudo, esperado):
    assert facet_resolver._modalidades(crudo) == esperado


def test_resolved_facet_sin_modalidades_no_acepta_imagen():
    f = facet_resolver.ResolvedFacet(
        key="k", provider_id="p", base_url=None, model="m", credential="",
        transport="ollama", persona=None, params=None,
        max_tokens_param=None, max_output_tokens=None)
    assert "image" not in f.input_modalities


async def _fetch(sql, args=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            return await cur.fetchall()


async def _commit(sql, args=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
        await conn.commit()


def test_resolve_facet_trae_input_modalities_de_la_fila_del_modelo(client):
    (ref, antes), = client.portal.call(
        _fetch,
        "SELECT m.id, m.input_modalities FROM facet_binding b JOIN model m ON m.id = b.model_ref "
        "WHERE b.facet_key = 'jekyll' AND b.role = 'primary'")
    client.portal.call(_commit, "UPDATE model SET input_modalities = 'text,image' WHERE id = %s", (ref,))
    try:
        facet_resolver._cache.pop("jekyll", None)
        resuelta = client.portal.call(facet_resolver.resolve_facet, "jekyll")
        assert resuelta.input_modalities == frozenset({"text", "image"})
    finally:
        client.portal.call(_commit, "UPDATE model SET input_modalities = %s WHERE id = %s", (antes, ref))
        facet_resolver._cache.pop("jekyll", None)


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _OllamaFalso:
    """/api/tags por GET, /api/show por POST con capacidades por tag."""

    def __init__(self, tags, capacidades):
        self._tags = tags
        self._capacidades = capacidades
        self.shows: list[str] = []

    async def get(self, url, **kwargs):
        return _Resp({"models": [{"model": t, "digest": "sha-" + t} for t in self._tags]})

    async def post(self, url, **kwargs):
        assert url.endswith("/api/show"), url
        tag = kwargs["json"]["model"]
        self.shows.append(tag)
        caps = self._capacidades[tag]
        if isinstance(caps, Exception):
            raise caps
        return _Resp({"capabilities": caps})


def test_sync_de_ollama_llena_input_modalities_desde_api_show(client):
    falso = _OllamaFalso(["test-vision:1b", "test-texto:1b"],
                         {"test-vision:1b": ["completion", "vision"], "test-texto:1b": ["completion"]})
    original = http_client._client
    http_client._client = falso
    try:
        client.portal.call(model_catalog.sync_provider_models, "ollama")
    finally:
        http_client._client = original
    filas = dict(client.portal.call(
        _fetch, "SELECT model_id, input_modalities FROM model WHERE provider_id='ollama' "
                "AND model_id IN ('test-vision:1b','test-texto:1b')"))
    try:
        assert set(str(filas["test-vision:1b"]).split(",")) == {"text", "image"}
        assert str(filas["test-texto:1b"]) == "text"
        assert sorted(falso.shows) == ["test-texto:1b", "test-vision:1b"]
    finally:
        client.portal.call(_commit, "DELETE FROM model WHERE provider_id='ollama' "
                                    "AND model_id IN ('test-vision:1b','test-texto:1b')")


def test_sync_de_ollama_si_api_show_falla_no_pisa_lo_que_habia(client):
    client.portal.call(_commit,
        "INSERT INTO model (provider_id, model_id, status, source, source_checked_at, input_modalities) "
        "VALUES ('ollama', 'test-previo:1b', 'available', 'provider_api', NOW(), 'text,image') "
        "ON DUPLICATE KEY UPDATE input_modalities='text,image'")
    falso = _OllamaFalso(["test-previo:1b"], {"test-previo:1b": RuntimeError("show caído")})
    original = http_client._client
    http_client._client = falso
    try:
        client.portal.call(model_catalog.sync_provider_models, "ollama")
        (valor,), = client.portal.call(
            _fetch, "SELECT input_modalities FROM model WHERE provider_id='ollama' AND model_id='test-previo:1b'")
        assert set(str(valor).split(",")) == {"text", "image"}
    finally:
        http_client._client = original
        client.portal.call(_commit, "DELETE FROM model WHERE provider_id='ollama' AND model_id='test-previo:1b'")
