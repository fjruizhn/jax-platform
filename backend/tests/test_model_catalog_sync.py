"""Bloque D (D1.3/D1.2/D1.4) — sync de 3 capas + drift + deprecacion.
Base real: db/migrations.py ya siembra 7 filas en `model` desde
facet_binding (ver docs/fase2-facetas-diseno.md D1.1).

El `client` fixture (conftest.py) entra TestClient como context manager, asi
que el pool de aiomysql de la app vive en el loop del portal de esa sesion
(ver test_facet_model_wiring.py). Todo lo async corre via
`client.portal.call(...)` para compartir ese mismo loop — llamarlo directo
desde un `async def test_...` revienta con 'attached to a different loop'.
HTTP real fakeado via http_client._client, mismo patron que
test_keys_http_pooling.py.
"""
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
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


async def _fetch_model(provider_id, model_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, source, consecutive_misses, context_window, "
                "price_input_per_1m_usd FROM model WHERE provider_id=%s AND model_id=%s",
                (provider_id, model_id),
            )
            return await cur.fetchone()


async def _fetch_one(sql, params=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            return await cur.fetchone()


async def _fetch_scalar(sql, params=()):
    row = await _fetch_one(sql, params)
    return row[0] if row else None


def _patch_credential(monkeypatch, value):
    async def fake_credential(provider_id):
        return value
    monkeypatch.setattr(model_catalog, "resolve_credential_instrumented", fake_credential)


def test_sync_provider_models_upserts_openai_compatible_response(client, monkeypatch):
    """capa (a): /v1/models OpenAI-compatible (moonshot), sin credencial real
    (resolve_credential_instrumented se fake-ea aparte). Usa moonshot (no
    deepseek) para no compartir fila con el test de deprecacion (D1.4), que
    corre en la misma DB de sesion completa (jax_memory_test)."""
    _patch_credential(monkeypatch, "sk-fake")

    fake = _FakeGetClient(_FakeResponse({"data": [
        {"id": "kimi-k3"},
        {"id": "kimi-k3-preview"},
    ]}))
    original = http_client._client
    http_client._client = fake
    try:
        result = client.portal.call(model_catalog.sync_provider_models, "moonshot")
    finally:
        http_client._client = original

    assert result["provider_id"] == "moonshot"
    assert result["fetched"] == 2

    row_existing = client.portal.call(_fetch_model, "moonshot", "kimi-k3")
    assert row_existing is not None
    assert row_existing[0] == "available"
    assert row_existing[1] == "provider_api"

    row_new = client.portal.call(_fetch_model, "moonshot", "kimi-k3-preview")
    assert row_new is not None
    assert row_new[1] == "provider_api"


def test_sync_provider_models_gemini_uses_models_key_and_strips_prefix(client, monkeypatch):
    """capa (a), rama Gemini: shape de respuesta distinto ({'models': [...]}
    con 'name': 'models/<id>'), ya visto en admin/keys.py:158-166."""
    _patch_credential(monkeypatch, "gk-fake")

    fake = _FakeGetClient(_FakeResponse({"models": [
        {"name": "models/gemini-2.5-flash"},
    ]}))
    original = http_client._client
    http_client._client = fake
    try:
        result = client.portal.call(model_catalog.sync_provider_models, "gemini")
    finally:
        http_client._client = original

    assert result["fetched"] == 1
    url, kwargs = fake.calls[0]
    assert "key=gk-fake" in url

    row = client.portal.call(_fetch_model, "gemini", "gemini-2.5-flash")
    assert row is not None
    assert row[1] == "provider_api"


def test_sync_provider_models_never_writes_facet_binding(client, monkeypatch):
    """REGLA DE ORO (D1.3): el sync escribe `model` libremente, JAMAS
    facet_binding. Confirma con evidencia real, no supuesto. Usa zhipu (no
    deepseek/moonshot) para no compartir fila con otros tests de este
    archivo en la misma sesion de DB."""
    _patch_credential(monkeypatch, "sk-fake")

    before = client.portal.call(
        _fetch_scalar,
        "SELECT model_ref FROM facet_binding WHERE facet_key='ada' AND role='primary'",
    )

    fake = _FakeGetClient(_FakeResponse({"data": [{"id": "totally-different-model"}]}))
    original = http_client._client
    http_client._client = fake
    try:
        client.portal.call(model_catalog.sync_provider_models, "zhipu")
    finally:
        http_client._client = original

    after = client.portal.call(
        _fetch_scalar,
        "SELECT model_ref FROM facet_binding WHERE facet_key='ada' AND role='primary'",
    )

    assert before == after


async def _reset_model_baseline(provider_id, model_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE model SET status='available', consecutive_misses=0 "
                "WHERE provider_id=%s AND model_id=%s",
                (provider_id, model_id),
            )
        await conn.commit()


def test_sync_marks_missing_model_deprecated_after_three_consecutive_misses(client, monkeypatch):
    """D1.4: ausente 3 syncs seguidos -> deprecated. Nunca 'gone' automatico,
    nunca se borra la fila. jax_memory_test es persistente entre corridas de
    pytest (no se recrea) — arranca de un baseline explicito en vez de
    asumir consecutive_misses=0, para no depender de lo que haya dejado una
    corrida anterior."""
    _patch_credential(monkeypatch, "sk-fake")
    client.portal.call(_reset_model_baseline, "deepseek", "deepseek-v4-flash")

    original = http_client._client
    empty_response = _FakeGetClient(_FakeResponse({"data": []}))  # deepseek-v4-flash nunca aparece
    try:
        for _ in range(3):
            http_client._client = empty_response
            client.portal.call(model_catalog.sync_provider_models, "deepseek")
    finally:
        http_client._client = original

    row = client.portal.call(_fetch_model, "deepseek", "deepseek-v4-flash")
    assert row[0] == "deprecated"
    assert row[2] == 3  # consecutive_misses

    n = client.portal.call(
        _fetch_scalar,
        "SELECT COUNT(*) FROM model WHERE provider_id='deepseek' AND model_id='deepseek-v4-flash'",
    )
    assert n == 1  # la fila sigue existiendo, no se borro


def test_enrich_from_models_dev_fills_metadata_without_touching_source(client):
    """capa (b): enriquecimiento, nunca toca `source`/`status`/existencia."""
    fake = _FakeGetClient(_FakeResponse({
        "deepseek": {"models": {"deepseek-v4-flash": {
            "limit": {"context": 128000},
            "cost": {"input": 0.27, "output": 1.10},
        }}}
    }))
    original = http_client._client
    http_client._client = fake
    try:
        result = client.portal.call(model_catalog.enrich_from_models_dev)
    finally:
        http_client._client = original

    assert result["enriched"] >= 1

    row = client.portal.call(_fetch_model, "deepseek", "deepseek-v4-flash")
    assert row[1] == "manual"  # source NO cambio a models_dev (regla D1.3)
    assert row[3] == 128000    # context_window si se lleno
    assert float(row[4]) == 0.27


def test_record_resolved_version_first_observation_is_not_drift(client):
    result = client.portal.call(model_catalog.record_resolved_version, "thot", "gpt-5.5")
    assert result["drift"] is False
    assert result["proposal_id"] is None

    resolved = client.portal.call(
        _fetch_scalar,
        "SELECT resolved_version FROM facet_binding WHERE facet_key='thot' AND role='primary'",
    )
    assert resolved == "gpt-5.5"


def _write_anthropic_credentials(tmp_path, expires_in_seconds):
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


def test_sync_provider_models_anthropic_uses_local_oauth_token(client, monkeypatch, tmp_path):
    """anthropic no tiene credencial en DB (ver credential_resolver.py) —
    usa el token OAuth que Claude Code ya deja en ~/.claude/.credentials.json
    (decision 2026-08-10: opcion 1, leer en caliente, sin refresh propio).
    Verifica tambien que va el header anthropic-version, requerido por la
    API real (confirmado con curl contra api.anthropic.com el 2026-08-10)."""
    path = _write_anthropic_credentials(tmp_path, expires_in_seconds=3600)
    monkeypatch.setattr(model_catalog, "_ANTHROPIC_CREDENTIALS_PATH", path)

    fake = _FakeGetClient(_FakeResponse({"data": [
        {"id": "claude-opus-5"},
        {"id": "claude-fable-5"},
    ]}))
    original = http_client._client
    http_client._client = fake
    try:
        result = client.portal.call(model_catalog.sync_provider_models, "anthropic")
    finally:
        http_client._client = original

    assert result["provider_id"] == "anthropic"
    assert result["fetched"] == 2

    url, kwargs = fake.calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer sk-ant-oat01-fake"
    assert kwargs["headers"]["anthropic-version"]

    row = client.portal.call(_fetch_model, "anthropic", "claude-fable-5")
    assert row is not None
    assert row[1] == "provider_api"


def test_sync_provider_models_anthropic_skips_when_token_file_missing(client, monkeypatch, tmp_path):
    """Fail-soft (opcion 1): sin archivo de credenciales local, el sync no
    revienta — se salta con motivo explicito, igual que 'sin models_list_url'
    para otros providers sin config."""
    monkeypatch.setattr(model_catalog, "_ANTHROPIC_CREDENTIALS_PATH", str(tmp_path / "no-existe.json"))

    fake = _FakeGetClient(_FakeResponse({"data": []}))
    original = http_client._client
    http_client._client = fake
    try:
        result = client.portal.call(model_catalog.sync_provider_models, "anthropic")
    finally:
        http_client._client = original

    assert result["provider_id"] == "anthropic"
    assert result["fetched"] == 0
    assert "skipped" in result
    assert fake.calls == []  # nunca deberia llegar a hacer la request HTTP


def test_sync_provider_models_anthropic_skips_when_token_expired(client, monkeypatch, tmp_path):
    """Token OAuth vencido (vida corta, ver decision 2026-08-10) -> skip
    explicito, nunca una llamada con credencial vieja a la API real."""
    path = _write_anthropic_credentials(tmp_path, expires_in_seconds=-60)
    monkeypatch.setattr(model_catalog, "_ANTHROPIC_CREDENTIALS_PATH", path)

    fake = _FakeGetClient(_FakeResponse({"data": []}))
    original = http_client._client
    http_client._client = fake
    try:
        result = client.portal.call(model_catalog.sync_provider_models, "anthropic")
    finally:
        http_client._client = original

    assert result["fetched"] == 0
    assert "skipped" in result
    assert fake.calls == []


class _FakeRaisingClient:
    async def get(self, url, **kwargs):
        raise ConnectionError("refused")


async def _fetch_model_digest(provider_id, model_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT digest, digest_changed_at FROM model WHERE provider_id=%s AND model_id=%s",
                (provider_id, model_id),
            )
            return await cur.fetchone()


def test_sync_provider_models_ollama_uses_local_tags_without_auth(client):
    """Ollama es local, sin API key (provider.auth_type='none') -- /api/tags
    no debe llevar Authorization ni ningun otro header. Shape real distinto
    a los demas (verificado con curl, 2026-08-10): {'models':[{'model':<tag>,
    'digest':<sha>}]}."""
    fake = _FakeGetClient(_FakeResponse({"models": [
        {"model": "qwen3-coder:30b", "digest": "sha-aaa"},
        {"model": "llama3.2:3b", "digest": "sha-bbb"},
    ]}))
    original = http_client._client
    http_client._client = fake
    try:
        result = client.portal.call(model_catalog.sync_provider_models, "ollama")
    finally:
        http_client._client = original

    assert result["provider_id"] == "ollama"
    assert result["fetched"] == 2

    url, kwargs = fake.calls[0]
    assert not kwargs.get("headers")  # sin auth, a proposito

    row = client.portal.call(_fetch_model, "ollama", "llama3.2:3b")
    assert row is not None
    assert row[1] == "provider_api"


async def _reset_model_digest(provider_id, model_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE model SET digest=NULL, digest_changed_at=NULL "
                "WHERE provider_id=%s AND model_id=%s",
                (provider_id, model_id),
            )
        await conn.commit()


def test_sync_provider_models_ollama_captures_digest_change(client):
    """El tag es un puntero LOCAL -- puede re-pullearse con pesos distintos
    sin que el tag cambie. digest_changed_at debe quedar NULL la primera vez
    (no hay 'antes' con que comparar) y poblarse solo cuando el digest
    realmente cambia entre dos syncs. jax_memory_test es persistente entre
    corridas (ver docstring del archivo) -- arranca de un baseline explicito
    en vez de asumir digest=NULL, para no depender de lo que haya dejado
    una corrida anterior de este mismo test."""
    client.portal.call(_reset_model_digest, "ollama", "qwen2.5:7b")
    original = http_client._client
    try:
        http_client._client = _FakeGetClient(_FakeResponse({"models": [
            {"model": "qwen2.5:7b", "digest": "sha-original"},
        ]}))
        client.portal.call(model_catalog.sync_provider_models, "ollama")
        first = client.portal.call(_fetch_model_digest, "ollama", "qwen2.5:7b")
        assert first[0] == "sha-original"
        assert first[1] is None  # primera observacion, no es un cambio

        http_client._client = _FakeGetClient(_FakeResponse({"models": [
            {"model": "qwen2.5:7b", "digest": "sha-repulled"},
        ]}))
        client.portal.call(model_catalog.sync_provider_models, "ollama")
        second = client.portal.call(_fetch_model_digest, "ollama", "qwen2.5:7b")
        assert second[0] == "sha-repulled"
        assert second[1] is not None  # cambio real detectado
    finally:
        http_client._client = original


def test_sync_provider_models_ollama_skips_when_unreachable(client):
    """Ollama caido/GPU semaphore ocupado -> skip explicito, nunca una
    excepcion que tumbe el resto del sync (openai/anthropic/etc siguen)."""
    original = http_client._client
    http_client._client = _FakeRaisingClient()
    try:
        result = client.portal.call(model_catalog.sync_provider_models, "ollama")
    finally:
        http_client._client = original

    assert result["provider_id"] == "ollama"
    assert result["fetched"] == 0
    assert "skipped" in result


def test_sync_provider_models_anthropic_never_deprecates_bare_alias(client, monkeypatch, tmp_path):
    """Bug real encontrado verificando en produccion (2026-08-10): Anthropic
    /v1/models jamas lista un alias de tier suelto ('sonnet') -- solo IDs
    fechados/fijados detras del alias (confirmado con curl real). Sin esta
    excepcion, la fila a la que Hyde esta bindeado cae a 'deprecated' en 3
    syncs seguidos aunque el alias siga siendo perfectamente valido -- una
    senal falsa de 'esto se esta yendo' en el catalogo."""
    path = _write_anthropic_credentials(tmp_path, expires_in_seconds=3600)
    monkeypatch.setattr(model_catalog, "_ANTHROPIC_CREDENTIALS_PATH", path)
    client.portal.call(_reset_model_baseline, "anthropic", "sonnet")

    fake = _FakeGetClient(_FakeResponse({"data": [{"id": "claude-sonnet-5"}]}))  # 'sonnet' suelto nunca aparece
    original = http_client._client
    try:
        for _ in range(3):
            http_client._client = fake
            client.portal.call(model_catalog.sync_provider_models, "anthropic")
    finally:
        http_client._client = original

    row = client.portal.call(_fetch_model, "anthropic", "sonnet")
    assert row[0] == "available"  # nunca degradado ni deprecated
    assert row[2] == 0  # consecutive_misses nunca sube


def test_record_resolved_version_change_creates_pending_proposal(client):
    client.portal.call(model_catalog.record_resolved_version, "ada", "glm-5.2")  # baseline
    result = client.portal.call(model_catalog.record_resolved_version, "ada", "glm-5.2-preview")  # drift

    assert result["drift"] is True
    assert result["proposal_id"] is not None

    reason, status = client.portal.call(
        _fetch_one,
        "SELECT reason, status FROM model_binding_proposal WHERE id=%s",
        (result["proposal_id"],),
    )
    assert reason == "drift_detected"
    assert status == "pending"

    # la regla de oro: facet_binding.model_ref no la toca este flujo (sigue apuntando al binding original)
    resolved = client.portal.call(
        _fetch_scalar,
        "SELECT resolved_version FROM facet_binding WHERE facet_key='ada' AND role='primary'",
    )
    assert resolved == "glm-5.2-preview"
