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


async def _ejecutar(sql, params=None):
    """Para DDL/DML sin resultado (DELETE/UPDATE) -- `_fetch_scalar` asume
    un result set y un DELETE no tiene ninguno. `params=None` (no `()`) a
    propósito: pymysql intenta `query %% args` en cuanto `args is not None`,
    y una consulta con un `%` literal (un `LIKE 'x-%'`) explota con
    "not enough arguments for format string" incluso pasándole una tupla
    vacía."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
        await conn.commit()


def _patch_credential(monkeypatch, value):
    async def fake_credential(provider_id):
        return value
    monkeypatch.setattr(model_catalog, "resolve_credential", fake_credential)


def test_sync_provider_models_upserts_openai_compatible_response(client, monkeypatch):
    """capa (a): /v1/models OpenAI-compatible (moonshot), sin credencial real
    (resolve_credential se fake-ea aparte). Usa moonshot (no
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


def test_sync_provider_models_nuevos_lists_only_previously_unseen_ids(client, monkeypatch):
    """D-catálogo (2026-09-27): un operador que corre el sync quiere saber
    "¿apareció algo que no estaba?" sin comparar el catálogo entero a mano --
    `nuevos` es esa lista, y sólo esa: lo que YA estaba en `model` para este
    proveedor no cuenta, aunque la corrida lo vuelva a ver."""
    _patch_credential(monkeypatch, "sk-fake")
    ya_conocido = "kimi-k3"  # ya sembrado por otro test de este archivo, misma sesión de DB
    nuevo = f"test-nuevo-{uuid.uuid4().hex[:8]}"

    original = http_client._client
    try:
        http_client._client = _FakeGetClient(_FakeResponse({"data": [{"id": ya_conocido}]}))
        client.portal.call(model_catalog.sync_provider_models, "moonshot")

        http_client._client = _FakeGetClient(_FakeResponse({"data": [
            {"id": ya_conocido}, {"id": nuevo},
        ]}))
        result = client.portal.call(model_catalog.sync_provider_models, "moonshot")
    finally:
        http_client._client = original

    assert result["nuevos"] == [nuevo]


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
    # T6-2 (2026-09-15): la key va en la cabecera x-goog-api-key, nunca en la URL.
    assert url == "https://generativelanguage.googleapis.com/v1beta/models"
    assert "key=" not in url
    assert kwargs["headers"] == {"x-goog-api-key": "gk-fake"}

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
    corrida anterior.

    A-3 (auditoría adversarial, 2026-09-27): una lista VACÍA ahora es
    'respuesta sospechosa' y no toca nada (ver
    test_model_catalog_paginacion_y_guardas.py) -- ya no sirve para simular
    "el modelo desapareció". Se simula con un decoy: una lista NO vacía que
    nunca incluye a 'deepseek-v4-flash'. El tamaño del decoy sale de
    consultar cuántos 'available' hay HOY para deepseek (la base de sesión
    es persistente entre corridas de pytest y puede tener más que sólo el
    seed) -- así el fake nunca dispara el guardián de "lista encogida" por
    casualidad, sin importar cuánto haya acumulado la sesión."""
    _patch_credential(monkeypatch, "sk-fake")
    client.portal.call(_reset_model_baseline, "deepseek", "deepseek-v4-flash")

    disponibles = client.portal.call(
        _fetch_scalar,
        "SELECT COUNT(*) FROM model WHERE provider_id='deepseek' AND status='available'",
    )
    decoy_response = _FakeGetClient(_FakeResponse({
        "data": [{"id": f"deepseek-decoy-{i}"} for i in range(disponibles + 1)],
    }))  # nunca incluye 'deepseek-v4-flash', y nunca es una lista más chica que la mitad de lo que ya había

    original = http_client._client
    try:
        for _ in range(3):
            http_client._client = decoy_response
            client.portal.call(model_catalog.sync_provider_models, "deepseek")
    finally:
        http_client._client = original
        # Limpieza: los decoys quedarían 'available' para siempre en la base
        # de sesión persistente, e inflarían el "disponibles antes" de la
        # PRÓXIMA corrida de este mismo test (crecimiento sin límite entre
        # corridas). Sólo se borran los decoys -- 'deepseek-v4-flash' queda.
        client.portal.call(
            _ejecutar,
            "DELETE FROM model WHERE provider_id='deepseek' AND model_id LIKE 'deepseek-decoy-%'",
        )

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


def test_enrich_from_models_dev_ignora_release_date_mal_formado_sin_abortar(client):
    """Bug real (2026-08-10): models.dev a veces devuelve release_date como
    'YYYY-MM' (sin dia) -- MySQL DATE bajo STRICT_TRANS_TABLES rechaza ese
    valor y la excepcion abortaba el loop entero, dejando sin enriquecer
    todo lo que venia despues del modelo con la fecha mala (encontrado
    verificando en produccion: kimi-k2.7-code nunca llego a re-sincronizar
    su precio real tras una corrupcion de test). Fix: una fecha mal
    formada se trata como ausente (no se escribe), NO aborta el resto."""
    fake = _FakeGetClient(_FakeResponse({
        "deepseek": {"models": {"deepseek-v4-flash": {
            "release_date": "2026-01",  # mal formado, sin dia
            "cost": {"input": 0.14, "output": 0.28},
        }}},
        "google": {"models": {"gemini-2.5-flash": {
            "release_date": "2026-03-15",  # bien formado, debe aplicarse
            "cost": {"input": 0.15, "output": 0.60},
        }}},
    }))
    original = http_client._client
    http_client._client = fake
    try:
        result = client.portal.call(model_catalog.enrich_from_models_dev)
    finally:
        http_client._client = original

    assert "error" not in result
    assert result["enriched"] >= 2

    row = client.portal.call(
        _fetch_one,
        "SELECT release_date FROM model WHERE provider_id='deepseek' AND model_id='deepseek-v4-flash'",
    )
    assert row[0] is None or str(row[0]) != "2026-01"  # nunca escribio el valor invalido

    row2 = client.portal.call(
        _fetch_one,
        "SELECT release_date FROM model WHERE provider_id='gemini' AND model_id='gemini-2.5-flash'",
    )
    assert str(row2[0]) == "2026-03-15"  # la fecha valida de OTRO modelo si se aplico


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


def test_read_anthropic_oauth_token_prefers_env_var_over_file(monkeypatch, tmp_path):
    """2026-09-27: desde que los servicios corren como `jaxsvc` (17-sep),
    `~/.claude/.credentials.json` no existe para ese usuario -- el sync de
    anthropic se saltaba en SILENCIO y `ok` no bajaba (ver
    test_sync_all_*). La credencial real ahora es la cuenta Max de Fernando
    vía `claude setup-token`, guardada como CLAUDE_CODE_OAUTH_TOKEN en
    /etc/jax/.env -- MISMO nombre que ya honra el CLI de Claude Code, sin
    inventar uno nuevo. Con la variable puesta, el archivo NUNCA se abre
    (ruta a un archivo que no existe -- si lo intentara abrir, reventaría)."""
    monkeypatch.setenv(model_catalog.ANTHROPIC_OAUTH_TOKEN_ENV, "sk-ant-oat01-desde-env")
    monkeypatch.setattr(model_catalog, "_ANTHROPIC_CREDENTIALS_PATH", str(tmp_path / "no-existe.json"))

    assert model_catalog._read_anthropic_oauth_token() == "sk-ant-oat01-desde-env"


def test_read_anthropic_oauth_token_empty_env_falls_back_to_file(monkeypatch, tmp_path):
    """Una variable puesta pero vacía (`CLAUDE_CODE_OAUTH_TOKEN=`, típico de un
    .env con la línea agregada sin valor todavía) NO cuenta como token: cae al
    archivo local, igual que si la variable no existiera."""
    monkeypatch.setenv(model_catalog.ANTHROPIC_OAUTH_TOKEN_ENV, "   ")
    path = _write_anthropic_credentials(tmp_path, expires_in_seconds=3600)
    monkeypatch.setattr(model_catalog, "_ANTHROPIC_CREDENTIALS_PATH", path)

    assert model_catalog._read_anthropic_oauth_token() == "sk-ant-oat01-fake"


def test_read_anthropic_oauth_token_no_env_no_file_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.delenv(model_catalog.ANTHROPIC_OAUTH_TOKEN_ENV, raising=False)
    monkeypatch.setattr(model_catalog, "_ANTHROPIC_CREDENTIALS_PATH", str(tmp_path / "no-existe.json"))

    with pytest.raises(model_catalog.AnthropicOAuthUnavailableError):
        model_catalog._read_anthropic_oauth_token()


def test_sync_provider_models_anthropic_uses_env_token_end_to_end(client, monkeypatch, tmp_path):
    """Integración completa (D1.3-a): con CLAUDE_CODE_OAUTH_TOKEN puesto, el
    sync de anthropic funciona sin `~/.claude/.credentials.json` -- el caso
    real de jaxsvc en producción, reproducido con evidencia y no supuesto.

    A-3 (auditoría adversarial, 2026-09-27): esta prueba manda una lista de
    UN solo id -- otros tests de este archivo (anthropic OAuth local, alias
    'sonnet') ya dejaron más de un modelo 'available' en la base de sesión
    persistente, así que sin normalizar el guardián de "lista encogida"
    vería 1 de N y lo trataría como sospechoso. Se bajan a 'deprecated' los
    OTROS anthropic 'available' antes de esta llamada -- no se borra nada,
    y ningún test de este archivo depende de que sigan 'available' después
    del suyo propio (cada uno resetea su propio baseline al empezar)."""
    client.portal.call(
        _ejecutar,
        "UPDATE model SET status='deprecated' WHERE provider_id='anthropic' "
        "AND model_id != 'claude-opus-5' AND status='available'",
    )
    monkeypatch.setenv(model_catalog.ANTHROPIC_OAUTH_TOKEN_ENV, "sk-ant-oat01-jaxsvc")
    monkeypatch.setattr(model_catalog, "_ANTHROPIC_CREDENTIALS_PATH", str(tmp_path / "no-existe.json"))

    fake = _FakeGetClient(_FakeResponse({"data": [{"id": "claude-opus-5"}]}))
    original = http_client._client
    http_client._client = fake
    try:
        result = client.portal.call(model_catalog.sync_provider_models, "anthropic")
    finally:
        http_client._client = original

    assert "skipped" not in result
    assert result["fetched"] == 1
    url, kwargs = fake.calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer sk-ant-oat01-jaxsvc"


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


def test_sync_provider_models_ollama_nuevos_lists_only_previously_unseen_ids(client):
    """Mismo contrato de `nuevos` que los proveedores OpenAI-compatible
    (test_sync_provider_models_nuevos_lists_only_previously_unseen_ids),
    ejercitado en la rama de ollama -- shape de respuesta y upsert distintos,
    misma pregunta ("¿qué es nuevo?")."""
    ya_conocido = "llama3.2:3b"  # ya sembrado por otro test de este archivo, misma sesión de DB
    nuevo = f"test-nuevo-ollama-{uuid.uuid4().hex[:8]}"

    original = http_client._client
    try:
        http_client._client = _FakeGetClient(_FakeResponse({"models": [
            {"model": ya_conocido, "digest": "sha-aaa"},
        ]}))
        client.portal.call(model_catalog.sync_provider_models, "ollama")

        http_client._client = _FakeGetClient(_FakeResponse({"models": [
            {"model": ya_conocido, "digest": "sha-aaa"},
            {"model": nuevo, "digest": "sha-nueva"},
        ]}))
        result = client.portal.call(model_catalog.sync_provider_models, "ollama")
    finally:
        http_client._client = original

    assert result["nuevos"] == [nuevo]


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
