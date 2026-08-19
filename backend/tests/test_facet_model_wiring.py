"""facet_binding wiring into chat model selection (Bugs 1-3, re-anclado a
Bloque C/D).

Hasta Bloque C, `chat.py` leia el modelo activo de `facet_models` (tabla
legacy, editada desde el panel admin) via `_resolve_active_model`. Bloque C
reemplazo esa funcion por `resolve_facet()` (facet_binding, mismo resolver
que usan Jacobs y el REPL) y NUNCA se actualizaron estos tests — quedaron
mutando `facet_models` mientras el codigo real ya leia `facet_binding`,
dando 3/5 tests en rojo con una premisa obsoleta (no una regresion real).
`_resolve_active_model` se borro por muerta (Bloque D) — ver commit del
mismo dia. Estos tests ahora mutan `facet_binding.model_id`, la fuente
real, para no repetir el mismo patron de "dos superficies, una mentira".

El `client` fixture (conftest.py) entra TestClient como context manager, asi
que el pool de aiomysql de la app vive en el loop del portal de esa sesion.
Filas de DB se mutan via `client.portal.call(...)` (mismo pool, mismo loop)
y se restauran en un finally para no dejar sucio el estado que otros tests
asumen.

La llamada saliente a Ollama se intercepta parcheando
`httpx.AsyncClient.post` (AsyncMock, sin self-binding) — sin red, sin
Ollama real.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from auth.jwt import create_access_token
from facet_resolver import FacetUnavailableError

USER_ID = "test-facet-user"
TENANT_ID = "test-facet-tenant"

SENTINEL_MODEL = "sentinel-dbwins-model:99z"  # unmistakably not from config.toml


def _auth_headers():
    token = create_access_token(USER_ID, TENANT_ID, "operator")
    return {"Authorization": f"Bearer {token}"}


def _ollama_response(content="ok"):
    resp = MagicMock()
    resp.raise_for_status = MagicMock(return_value=None)
    resp.json = MagicMock(return_value={"message": {"content": content}})
    return resp


async def _db_fetch(sql, params=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()


async def _db_exec(sql, params=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)


def _current_jax_local_model_id(client):
    rows = client.portal.call(
        _db_fetch,
        "SELECT model_id FROM facet_binding WHERE facet_key='jax_local' AND role='primary'",
    )
    return rows[0][0] if rows else None


def _set_jax_local_model_id(client, model_id):
    client.portal.call(
        _db_exec,
        "UPDATE facet_binding SET model_id=%s WHERE facet_key='jax_local' AND role='primary'",
        (model_id,),
    )


def _post_chat(client):
    """Force facet=jax_local, capture the model sent to Ollama.

    Message is deliberately NOT a model-identity question ("que modelo sos")
    — that phrasing is now intercepted by chat.py's anti-confabulation
    short-circuit (backend/api/chat.py:_is_model_identity_question) before
    Ollama is ever called, which would make this fixture assert on the wrong
    code path entirely."""
    mock_post = AsyncMock(return_value=_ollama_response())
    with patch.object(httpx.AsyncClient, "post", mock_post):
        resp = client.post(
            "/api/chat",
            json={"message": "hola, como estas", "facet": "jax_local"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 200, resp.text
    assert mock_post.call_count == 1, f"expected one Ollama call, got {mock_post.call_count}"
    payload = mock_post.call_args.kwargs["json"]
    return payload


def test_facet_binding_model_wins_over_config_toml(client):
    """El model_id de facet_binding debe ser lo que chat.py manda a Ollama,
    NO el model_default de config.toml. Reproduce Bug 1 (re-anclado a la
    fuente real post-Bloque-C)."""
    prev_model = _current_jax_local_model_id(client)
    _set_jax_local_model_id(client, SENTINEL_MODEL)
    try:
        payload = _post_chat(client)
        assert payload["model"] == SENTINEL_MODEL, (
            f"chat sent '{payload['model']}' but facet_binding's model is "
            f"'{SENTINEL_MODEL}' — config.toml is still winning (Bug 1)"
        )
    finally:
        _set_jax_local_model_id(client, prev_model)


def test_degrades_explicitly_when_facet_unavailable(client):
    """Si resolve_facet() lanza FacetUnavailableError (sin binding activo),
    chat.py NO cae en silencio a config.toml — devuelve una degradacion
    explicita (fail-closed, ver _invoke_facet en api/chat.py) sin invocar
    Ollama. Ya no hay fallback a config.toml para esto: ese mecanismo
    (facet_models -> config.toml) se borro con el corte a facet_binding."""
    mock_post = AsyncMock(return_value=_ollama_response())
    with patch("api.chat.resolve_facet", AsyncMock(side_effect=FacetUnavailableError("sin binding"))), \
         patch.object(httpx.AsyncClient, "post", mock_post):
        resp = client.post(
            "/api/chat",
            json={"message": "hola, como estas", "facet": "jax_local"},
            headers=_auth_headers(),
        )
    # FacetUnavailableError -> _invoke_facet devuelve mensaje de degradacion
    # explicito (fail-closed, ver api/chat.py) en vez de invocar Ollama con
    # un modelo vacio o caer silenciosamente a config.toml.
    assert resp.status_code == 200, resp.text
    assert mock_post.call_count == 0, (
        "sin binding activo no debe invocar Ollama en absoluto (fail-closed)"
    )


def test_jax_local_system_prompt_states_resolved_model(client):
    """jax_local's system prompt must name the actually-resolved (DB-active)
    model, so it stops confabulating its identity (Bug 3)."""
    prev_model = _current_jax_local_model_id(client)
    _set_jax_local_model_id(client, SENTINEL_MODEL)
    try:
        payload = _post_chat(client)
        system_msg = payload["messages"][0]
        assert system_msg["role"] == "system"
        assert SENTINEL_MODEL in system_msg["content"], (
            "jax_local system prompt does not state the resolved model name"
        )
    finally:
        _set_jax_local_model_id(client, prev_model)


def test_model_identity_question_short_circuits_before_ollama(client):
    """'que modelo sos' must be answered deterministically from the resolved
    facet_binding model, without ever calling Ollama — the LLM confabulates
    its own identity even when handed the correct model as context, so this
    class of question is intercepted before _call_ollama (see
    _is_model_identity_question in chat.py)."""
    prev_model = _current_jax_local_model_id(client)
    _set_jax_local_model_id(client, SENTINEL_MODEL)
    try:
        mock_post = AsyncMock(return_value=_ollama_response())
        with patch.object(httpx.AsyncClient, "post", mock_post):
            resp = client.post(
                "/api/chat",
                json={"message": "que modelo sos", "facet": "jax_local"},
                headers=_auth_headers(),
            )
        assert resp.status_code == 200, resp.text
        assert mock_post.call_count == 0, (
            f"model-identity question must short-circuit before Ollama, "
            f"got {mock_post.call_count} call(s)"
        )
        assert SENTINEL_MODEL in resp.json()["response"], (
            "short-circuit reply does not name the resolved DB-active model"
        )
    finally:
        _set_jax_local_model_id(client, prev_model)
