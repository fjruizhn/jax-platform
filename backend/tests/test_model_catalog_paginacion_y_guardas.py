"""Bloque D -- paginación de proveedores + guarda de respuesta sospechosa
(A-3, auditoría adversarial del commit d549335, 2026-09-27).

VERIFICADO EN PRODUCCIÓN por la sesión principal (26-sep): Gemini quedó con
exactamente 50 modelos `available` -- el `pageSize` por defecto de su API --
y el resto se empezó a degradar solo, 3 misses después de "deprecated": el
sync nunca pidió una segunda página. Este archivo cubre:

1. Anthropic pagina con `limit`/`after_id`/`has_more` (todas las páginas se
   juntan antes de decidir qué es nuevo/qué falta).
2. Gemini pagina con `pageSize`/`pageToken`/`nextPageToken`.
3. openai/deepseek/moonshot/zhipu NO paginan -- se autodescriben como
   compatibles con `/v1/models` de OpenAI (ver `_PROVIDER_SYNC_SEED` en
   db/migrations.py, "Los 4 OpenAI-compatibles"), que no tiene parámetros de
   paginación (NO VERIFICADO con curl real contra las 4 APIs desde este
   entorno -- sin credenciales ni red de producción a mano; se infiere de
   que replican el formato de `/v1/models`, que la propia documentación que
   ya cita este módulo describe sin paginar). Un test negativo confirma que
   ninguno de los 4 manda parámetros de página.
4. Una respuesta 200 con lista vacía, o con MENOS DE LA MITAD de lo que ese
   proveedor tenía `available` antes, es un FALLO del proveedor -- no toca
   ninguna fila (ni upsert ni miss).
"""
import uuid

import pytest

import http_client
import model_catalog


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        pass


class _FakeGetClient:
    """Una respuesta fija para toda llamada -- para los tests que NO
    paginan (openai-compatibles) y para los casos de una sola página."""
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


class _FakeGetClientSecuencia:
    """Una respuesta DISTINTA por llamada, en orden -- para simular
    paginación real (página 1, página 2, ...)."""
    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self.calls = []

    async def get(self, url, **kwargs):
        indice = len(self.calls)
        self.calls.append((url, kwargs))
        return self._respuestas[indice]


def _patch_credential(monkeypatch, value):
    async def fake_credential(provider_id):
        return value
    monkeypatch.setattr(model_catalog, "resolve_credential", fake_credential)


def _write_anthropic_credentials(tmp_path, expires_in_seconds=3600):
    import json
    import time

    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat01-fake",
            "expiresAt": int((time.time() + expires_in_seconds) * 1000),
        }
    }))
    return str(path)


async def _fetch_model(provider_id, model_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, source FROM model WHERE provider_id=%s AND model_id=%s",
                (provider_id, model_id),
            )
            return await cur.fetchone()


async def _crear_provider_de_prueba(provider_id, url):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO provider (id, display_name, base_url, auth_type, models_list_url) "
                "VALUES (%s, %s, 'https://example.invalid', 'api_key', %s)",
                (provider_id, provider_id, url),
            )
        await conn.commit()


async def _borrar_provider_de_prueba(provider_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM model WHERE provider_id=%s", (provider_id,))
            await cur.execute("DELETE FROM provider WHERE id=%s", (provider_id,))
        await conn.commit()


async def _sembrar_modelos_disponibles(provider_id, cantidad):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for i in range(cantidad):
                await cur.execute(
                    "INSERT INTO model (provider_id, model_id, status, source, source_checked_at) "
                    "VALUES (%s, %s, 'available', 'manual', NOW())",
                    (provider_id, f"modelo-{i}"),
                )
        await conn.commit()


async def _estado_modelos(provider_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT model_id, status, consecutive_misses FROM model WHERE provider_id=%s ORDER BY model_id",
                (provider_id,),
            )
            return await cur.fetchall()


# --------------------------------------------------------------------------
# 1. Anthropic pagina
# --------------------------------------------------------------------------

async def _normalizar_disponibles(provider_id, salvo=()):
    """Baja a 'deprecated' todo lo 'available' de `provider_id` EXCEPTO lo
    listado en `salvo` -- no borra nada. La base de sesión es persistente
    entre corridas de pytest (docstring de módulo de test_model_catalog_sync.py)
    y varios tests de otros archivos dejan filas 'available' sin limpiar:
    sin esto, el guardián de "lista encogida" (A-3) podría disparar por
    casualidad según cuántas corridas anteriores acumuló la sesión, no por
    nada que este test esté afirmando."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            if salvo:
                marcas = ", ".join(["%s"] * len(salvo))
                await cur.execute(
                    f"UPDATE model SET status='deprecated' WHERE provider_id=%s "
                    f"AND status='available' AND model_id NOT IN ({marcas})",
                    (provider_id, *salvo),
                )
            else:
                await cur.execute(
                    "UPDATE model SET status='deprecated' WHERE provider_id=%s AND status='available'",
                    (provider_id,),
                )
        await conn.commit()


async def _borrar_modelos(provider_id, model_ids):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            marcas = ", ".join(["%s"] * len(model_ids))
            await cur.execute(
                f"DELETE FROM model WHERE provider_id=%s AND model_id IN ({marcas})",
                (provider_id, *model_ids),
            )
        await conn.commit()


def test_sync_provider_models_anthropic_pagina_con_after_id_mientras_has_more(client, monkeypatch, tmp_path):
    path = _write_anthropic_credentials(tmp_path)
    monkeypatch.setattr(model_catalog, "_ANTHROPIC_CREDENTIALS_PATH", path)
    client.portal.call(_normalizar_disponibles, "anthropic")

    pagina1 = _FakeResponse({"data": [{"id": "claude-pag-a"}], "has_more": True, "last_id": "claude-pag-a"})
    pagina2 = _FakeResponse({"data": [{"id": "claude-pag-b"}], "has_more": False, "last_id": "claude-pag-b"})
    fake = _FakeGetClientSecuencia([pagina1, pagina2])
    original = http_client._client
    http_client._client = fake
    try:
        result = client.portal.call(model_catalog.sync_provider_models, "anthropic")
    finally:
        http_client._client = original
        client.portal.call(_borrar_modelos, "anthropic", ["claude-pag-a", "claude-pag-b"])

    assert result["fetched"] == 2
    assert len(fake.calls) == 2
    _url1, kwargs1 = fake.calls[0]
    _url2, kwargs2 = fake.calls[1]
    assert kwargs1["params"]["limit"] == 1000
    assert "after_id" not in kwargs1["params"]
    assert kwargs2["params"]["after_id"] == "claude-pag-a"


def test_fetch_anthropic_paginado_sin_has_more_es_una_sola_pagina():
    """Compatibilidad, ejercitada DIRECTO sobre el helper de paginación (sin
    DB, sin `client`): una respuesta SIN 'has_more' -- como los fakes de
    test_model_catalog_sync.py, escritos antes de este cambio -- no debe
    disparar una segunda llamada. Se prueba el helper puro para no depender
    del guardián de "lista encogida" (A-3), que es una preocupación
    completamente distinta."""
    import asyncio

    fake = _FakeGetClient(_FakeResponse({"data": [{"id": "claude-sin-paginar"}]}))
    resultado = asyncio.run(model_catalog._fetch_anthropic_paginado(
        fake, "https://api.anthropic.com/v1/models", {"Authorization": "Bearer x"}))

    assert resultado == {"data": [{"id": "claude-sin-paginar"}]}
    assert len(fake.calls) == 1


# --------------------------------------------------------------------------
# 2. Gemini pagina
# --------------------------------------------------------------------------

def test_sync_provider_models_gemini_pagina_con_pagetoken_mientras_next_page_token(client, monkeypatch):
    """El caso VERIFICADO en producción (26-sep): sin esto, todo lo que pasa
    de `pageSize` (50 por defecto en la API real) queda fuera y empieza a
    degradarse solo."""
    _patch_credential(monkeypatch, "gk-fake")
    client.portal.call(_normalizar_disponibles, "gemini")

    pagina1 = _FakeResponse({"models": [{"name": "models/gemini-pag-a"}], "nextPageToken": "token-2"})
    pagina2 = _FakeResponse({"models": [{"name": "models/gemini-pag-b"}]})  # última página: sin nextPageToken
    fake = _FakeGetClientSecuencia([pagina1, pagina2])
    original = http_client._client
    http_client._client = fake
    try:
        result = client.portal.call(model_catalog.sync_provider_models, "gemini")
    finally:
        http_client._client = original
        client.portal.call(_borrar_modelos, "gemini", ["gemini-pag-a", "gemini-pag-b"])

    assert result["fetched"] == 2
    assert len(fake.calls) == 2
    _url1, kwargs1 = fake.calls[0]
    _url2, kwargs2 = fake.calls[1]
    assert kwargs1["params"]["pageSize"] == 1000
    assert "pageToken" not in kwargs1["params"]
    assert kwargs2["params"]["pageToken"] == "token-2"
    assert kwargs1["headers"] == {"x-goog-api-key": "gk-fake"}  # T6-2 sigue vigente


def test_fetch_gemini_paginado_sin_next_page_token_es_una_sola_pagina():
    """Mismo criterio que el test análogo de Anthropic: se prueba el helper
    puro, sin tocar el guardián de "lista encogida"."""
    import asyncio

    fake = _FakeGetClient(_FakeResponse({"models": [{"name": "models/gemini-sin-paginar"}]}))
    resultado = asyncio.run(model_catalog._fetch_gemini_paginado(
        fake, "https://generativelanguage.googleapis.com/v1beta/models", {"x-goog-api-key": "gk-fake"}))

    assert resultado == {"models": [{"name": "models/gemini-sin-paginar"}]}
    assert len(fake.calls) == 1


# --------------------------------------------------------------------------
# 3. openai-compatibles (moonshot acá) NO paginan
# --------------------------------------------------------------------------

def test_sync_provider_models_openai_compatible_no_manda_parametros_de_pagina(client, monkeypatch):
    _patch_credential(monkeypatch, "sk-fake")
    fake = _FakeGetClient(_FakeResponse({"data": [{"id": "kimi-sin-paginar"}]}))
    original = http_client._client
    http_client._client = fake
    try:
        client.portal.call(model_catalog.sync_provider_models, "moonshot")
    finally:
        http_client._client = original

    assert len(fake.calls) == 1
    _url, kwargs = fake.calls[0]
    assert "params" not in kwargs or not kwargs["params"]


# --------------------------------------------------------------------------
# 4. Lista vacía / encogida = fallo del proveedor, no toca nada
# --------------------------------------------------------------------------

def test_sync_provider_models_lista_vacia_es_fallo_y_no_toca_nada(client, monkeypatch):
    provider_id = f"test-vacio-{uuid.uuid4().hex[:8]}"
    _patch_credential(monkeypatch, "sk-fake")
    client.portal.call(_crear_provider_de_prueba, provider_id, "https://example.invalid/v1/models")
    client.portal.call(_sembrar_modelos_disponibles, provider_id, 2)
    try:
        fake = _FakeGetClient(_FakeResponse({"data": []}))
        original = http_client._client
        http_client._client = fake
        try:
            result = client.portal.call(model_catalog.sync_provider_models, provider_id)
        finally:
            http_client._client = original

        assert "error" in result
        assert "skipped" not in result

        filas = client.portal.call(_estado_modelos, provider_id)
        assert len(filas) == 2
        for _model_id, status, misses in filas:
            assert status == "available"
            assert misses == 0
    finally:
        client.portal.call(_borrar_provider_de_prueba, provider_id)


def test_sync_provider_models_lista_encogida_a_menos_de_la_mitad_es_fallo(client, monkeypatch):
    provider_id = f"test-encoge-{uuid.uuid4().hex[:8]}"
    _patch_credential(monkeypatch, "sk-fake")
    client.portal.call(_crear_provider_de_prueba, provider_id, "https://example.invalid/v1/models")
    client.portal.call(_sembrar_modelos_disponibles, provider_id, 4)  # mitad = 2
    try:
        fake = _FakeGetClient(_FakeResponse({"data": [{"id": "modelo-0"}]}))  # 1 < 2
        original = http_client._client
        http_client._client = fake
        try:
            result = client.portal.call(model_catalog.sync_provider_models, provider_id)
        finally:
            http_client._client = original

        assert "error" in result
        filas = client.portal.call(_estado_modelos, provider_id)
        assert len(filas) == 4
        for _model_id, status, misses in filas:
            assert status == "available"
            assert misses == 0
    finally:
        client.portal.call(_borrar_provider_de_prueba, provider_id)


def test_sync_provider_models_exactamente_la_mitad_no_es_fallo(client, monkeypatch):
    """Boundary: 'MENOS de la mitad' -- exactamente la mitad tiene que
    proceder con normalidad."""
    provider_id = f"test-mitad-{uuid.uuid4().hex[:8]}"
    _patch_credential(monkeypatch, "sk-fake")
    client.portal.call(_crear_provider_de_prueba, provider_id, "https://example.invalid/v1/models")
    client.portal.call(_sembrar_modelos_disponibles, provider_id, 4)
    try:
        fake = _FakeGetClient(_FakeResponse({"data": [{"id": "modelo-0"}, {"id": "modelo-1"}]}))  # 2 == 4/2
        original = http_client._client
        http_client._client = fake
        try:
            result = client.portal.call(model_catalog.sync_provider_models, provider_id)
        finally:
            http_client._client = original

        assert "error" not in result
        assert result["fetched"] == 2
    finally:
        client.portal.call(_borrar_provider_de_prueba, provider_id)


def test_sync_provider_models_primer_sync_de_un_provider_nuevo_no_es_fallo(client, monkeypatch):
    """Sin historia (0 disponibles antes) una lista chica -- incluso de 1 --
    no es "encogida": no hay nada contra qué compararla."""
    provider_id = f"test-nuevo-{uuid.uuid4().hex[:8]}"
    _patch_credential(monkeypatch, "sk-fake")
    client.portal.call(_crear_provider_de_prueba, provider_id, "https://example.invalid/v1/models")
    try:
        fake = _FakeGetClient(_FakeResponse({"data": [{"id": "modelo-0"}]}))
        original = http_client._client
        http_client._client = fake
        try:
            result = client.portal.call(model_catalog.sync_provider_models, provider_id)
        finally:
            http_client._client = original

        assert "error" not in result
        assert result["fetched"] == 1
    finally:
        client.portal.call(_borrar_provider_de_prueba, provider_id)


def test_sync_provider_models_ollama_lista_vacia_es_fallo_y_no_toca_nada(client):
    """Mismo guardián para ollama -- un /api/tags que responde 200 con
    'models': [] no es lo mismo que 'no alcanzable' (eso ya lo cubre el
    except de _sync_ollama_models); acá HAY respuesta, está vacía.

    Modelo SINTÉTICO propio (no reutiliza uno de otro archivo de test): la
    base de sesión es compartida y persistente, apoyarse en una fila que
    OTRO test dejó sería un acoplamiento por orden de ejecución."""
    model_id = f"test-ollama-vacio-{uuid.uuid4().hex[:8]}"

    async def _sembrar():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO model (provider_id, model_id, status, source, source_checked_at) "
                    "VALUES ('ollama', %s, 'available', 'manual', NOW())",
                    (model_id,),
                )
            await conn.commit()

    async def _borrar():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM model WHERE provider_id='ollama' AND model_id=%s", (model_id,))
            await conn.commit()

    client.portal.call(_sembrar)
    try:
        original = http_client._client
        http_client._client = _FakeGetClient(_FakeResponse({"models": []}))
        try:
            result = client.portal.call(model_catalog.sync_provider_models, "ollama")
        finally:
            http_client._client = original

        assert "error" in result
        assert "skipped" not in result
        fila = client.portal.call(_fetch_model, "ollama", model_id)
        assert fila[0] == "available"  # no se tocó
    finally:
        client.portal.call(_borrar)
