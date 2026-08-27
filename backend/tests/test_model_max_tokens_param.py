"""model.max_tokens_param — la columna nace CON lector y CON test.

Incidente que la origina (2026-08-24, thot 3 dias caido en la Mesa web):
_call_openai_compat mandaba `"max_tokens": 131072` fijo, y la API de
gpt-5.6-terra lo rechaza con HTTP 400 ("Unsupported parameter: 'max_tokens'
is not supported with this model. Use 'max_completion_tokens' instead").

El nombre del parametro de limite de salida dejo de ser universal: es una
propiedad estable POR MODELO. Vive en `model` (mismo eje que
supports_tool_use / supports_structured_output / context_window), se lee al
despachar via facet_resolver.ResolvedFacet.max_tokens_param, y un modelo sin
valor FALLA RUIDOSO en vez de asumir uno — si el default fuera el parametro
viejo, el proximo modelo nuevo se romperia igual que thot pero en silencio.

Los tests que tocan DB pasan por `client.portal.call(...)` a proposito: el
pool de aiomysql de la app vive en el loop del portal de esa sesion, y
tocarlo desde otro loop da el RuntimeError de "attached to a different loop"
que ya afecta a otras suites de este repo.
"""
import http_client
import pytest

from api.chat import (
    ModelDispatchConfigError,
    _MAX_OUTPUT_TOKENS,
    _call_openai_compat,
    _max_tokens_field,
)
from db.migrations import _MODEL_MAX_TOKENS_PARAM_SEED, _seed_model_max_tokens_param


# --------------------------------------------------------------------------
# Lector: el nombre del parametro sale del catalogo, no de una constante
# --------------------------------------------------------------------------

def test_model_that_declares_max_completion_tokens_gets_that_name():
    """El caso de thot/gpt-5.6-terra: la API exige el nombre nuevo."""
    assert _max_tokens_field("gpt-5.6-terra", "max_completion_tokens") == "max_completion_tokens"


def test_model_that_declares_max_tokens_gets_the_old_name():
    """El caso de jekyll/deepseek-v4-flash y ada/glm-5.3: siguen exigiendo el
    nombre viejo. Es lo que hace que 'cambiar la constante' no sea un fix —
    arregla la instancia y rompe la clase."""
    assert _max_tokens_field("deepseek-v4-flash", "max_tokens") == "max_tokens"


def test_null_fails_loud_naming_the_model_and_the_remedy():
    """Requisito explicito: NULL no asume, falla — y el error tiene que
    servirle a un operador para saber exactamente que fila sembrar."""
    with pytest.raises(ModelDispatchConfigError) as exc:
        _max_tokens_field("modelo-recien-aprobado", None)
    msg = str(exc.value)
    assert "modelo-recien-aprobado" in msg, "el error no nombra el modelo"
    assert "max_tokens_param" in msg
    assert "UPDATE model SET max_tokens_param" in msg, "el error no dice como arreglarlo"
    assert "max_completion_tokens" in msg and "'max_tokens'" in msg, (
        "el error no ofrece los dos valores posibles"
    )


def test_unknown_param_name_is_rejected_instead_of_going_on_the_wire():
    """Defensa en profundidad contra un valor que se salteo el ENUM de la
    columna: no se manda una clave arbitraria en el JSON al proveedor."""
    with pytest.raises(ModelDispatchConfigError) as exc:
        _max_tokens_field("modelo-x", "maxTokens")
    assert "maxTokens" in str(exc.value)


# --------------------------------------------------------------------------
# Dispatch: el nombre elegido es el que de verdad viaja en el body
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        pass


class _BodyCapturingClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def _dispatch(client, max_tokens_param):
    fake = _BodyCapturingClient(_FakeResponse({"choices": [{"message": {"content": "ok"}}]}))
    original = http_client._client
    http_client._client = fake
    try:
        client.portal.call(
            _call_openai_compat, "https://api.example.com/v1", "sk-fake", "modelo-x",
            "system", [], "hola", max_tokens_param, None,
        )
    finally:
        http_client._client = original
    return fake


def test_dispatch_sends_max_completion_tokens_and_not_max_tokens(client):
    fake = _dispatch(client, "max_completion_tokens")
    body = fake.calls[0][1]["json"]
    assert body["max_completion_tokens"] == _MAX_OUTPUT_TOKENS
    assert "max_tokens" not in body, (
        "mandar tambien el nombre viejo reproduce el HTTP 400 que tumbo a thot"
    )


def test_dispatch_sends_max_tokens_for_models_that_still_require_it(client):
    fake = _dispatch(client, "max_tokens")
    body = fake.calls[0][1]["json"]
    assert body["max_tokens"] == _MAX_OUTPUT_TOKENS
    assert "max_completion_tokens" not in body


def test_dispatch_with_null_never_reaches_the_provider(client):
    """La falla ruidosa corta ANTES de la llamada saliente: un modelo sin
    sembrar no gasta una llamada (ni tokens) para descubrir lo que el catalogo
    deberia declarar."""
    fake = _BodyCapturingClient(_FakeResponse({"choices": [{"message": {"content": "ok"}}]}))
    original = http_client._client
    http_client._client = fake
    try:
        with pytest.raises(ModelDispatchConfigError):
            client.portal.call(
                _call_openai_compat, "https://api.example.com/v1", "sk-fake",
                "modelo-sin-sembrar", "system", [], "hola", None, None,
            )
    finally:
        http_client._client = original
    assert fake.calls == [], "no debe haber ni una llamada al proveedor"


# --------------------------------------------------------------------------
# Migracion + seed: la columna existe y esta poblada tras run_migrations()
# --------------------------------------------------------------------------

async def _fetch(sql, params=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()


async def _exec(sql, params=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)


async def _run_seed():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _seed_model_max_tokens_param(cur)
        await conn.commit()


def test_column_exists_after_run_migrations(client):
    """El `client` fixture entra TestClient como context manager, o sea que el
    lifespan de la app ya corrio run_migrations() sobre jax_memory_test."""
    rows = client.portal.call(
        _fetch,
        "SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'model' "
        "AND COLUMN_NAME = 'max_tokens_param'",
    )
    assert rows, "run_migrations() no creo model.max_tokens_param"
    column_type, is_nullable, default = rows[0]
    assert "max_tokens" in column_type and "max_completion_tokens" in column_type
    assert is_nullable == "YES"
    # MariaDB reporta "sin DEFAULT" en una columna nullable como el literal
    # 'NULL' (string), no como NULL — verificado contra information_schema en
    # jax_memory_test, mismo valor que context_window/digest.
    assert default in (None, "NULL"), (
        f"COLUMN_DEFAULT={default!r}: un DEFAULT convierte 'no declarado' en "
        f"una suposicion silenciosa — justo lo que esta columna existe para impedir"
    )


def test_seed_populates_every_verified_model_present_in_the_catalog(client):
    """Los 3 modelos que HOY pasan por _call_openai_compat (gpt-5.6-terra,
    deepseek-v4-flash, glm-5.3) mas kimi-k3. Se afirma sobre las filas que
    existen en esta DB: jax_memory_test no tiene todo el catalogo de
    produccion, pero ninguna fila sembrable puede quedar NULL."""
    client.portal.call(_run_seed)
    for provider_id, model_id, expected in _MODEL_MAX_TOKENS_PARAM_SEED:
        rows = client.portal.call(
            _fetch,
            "SELECT max_tokens_param FROM model WHERE provider_id = %s AND model_id = %s",
            (provider_id, model_id),
        )
        if not rows:
            continue  # esa fila del catalogo no existe en esta DB
        assert rows[0][0] == expected, f"{provider_id}/{model_id}"


def test_seed_covers_deepseek_and_thot_specifically(client):
    """Guard contra la regresion mas probable de este cambio: sembrar solo el
    modelo de thot tumba jekyll y ada, que hoy funcionan."""
    seeded = {(p, m): v for p, m, v in _MODEL_MAX_TOKENS_PARAM_SEED}
    assert seeded[("openai", "gpt-5.6-terra")] == "max_completion_tokens"
    assert seeded[("deepseek", "deepseek-v4-flash")] == "max_tokens"
    assert seeded[("zhipu", "glm-5.3")] == "max_tokens"
    assert seeded[("moonshot", "kimi-k3")] == "max_tokens"


def test_seed_is_idempotent_and_does_not_overwrite_a_manual_value(client):
    """Mismo guard que _seed_http_facet_allowed_callers: WHERE ... IS NULL."""
    row = client.portal.call(
        _fetch,
        "SELECT id, max_tokens_param FROM model "
        "WHERE provider_id='deepseek' AND model_id='deepseek-v4-flash'",
    )
    assert row, "deepseek-v4-flash deberia existir en jax_memory_test"
    model_row_id, previous = row[0]
    client.portal.call(
        _exec,
        "UPDATE model SET max_tokens_param='max_completion_tokens' WHERE id=%s",
        (model_row_id,),
    )
    try:
        client.portal.call(_run_seed)
        after = client.portal.call(
            _fetch, "SELECT max_tokens_param FROM model WHERE id=%s", (model_row_id,)
        )
        assert after[0][0] == "max_completion_tokens", (
            "el seed piso un valor puesto a mano"
        )
    finally:
        client.portal.call(
            _exec, "UPDATE model SET max_tokens_param=%s WHERE id=%s",
            (previous, model_row_id),
        )
        client.portal.call(_run_seed)


def test_seed_leaves_unverified_models_null(client):
    """No se adivina por proveedor: un modelo fuera de la lista verificada
    queda NULL y falla ruidoso cuando alguien lo bindee. gpt-5.5 (el modelo
    ANTERIOR de thot, todavia en el catalogo) es el caso real."""
    rows = client.portal.call(
        _fetch,
        "SELECT max_tokens_param FROM model WHERE provider_id='openai' AND model_id='gpt-5.5'",
    )
    if rows:
        assert rows[0][0] is None, (
            "gpt-5.5 no esta en la lista verificada: sembrarlo por parecido de "
            "proveedor es exactamente la suposicion que esta columna elimina"
        )


# --------------------------------------------------------------------------
# Plomeria: el dato llega del catalogo al despachador
# --------------------------------------------------------------------------

def test_resolve_facet_carries_max_tokens_param_from_the_model_row(client):
    """El JOIN de facet_resolver._query_facet ya traia model.model_id; ahora
    tambien trae max_tokens_param — una sola fuente de verdad, la misma fila."""
    import facet_resolver

    client.portal.call(_run_seed)
    facet_resolver._cache.pop("jekyll", None)
    resolved = client.portal.call(facet_resolver.resolve_facet, "jekyll")
    assert resolved.transport == "http_openai_compat"
    assert resolved.max_tokens_param == "max_tokens", (
        f"jekyll resolvio a {resolved.model!r} con "
        f"max_tokens_param={resolved.max_tokens_param!r}"
    )
