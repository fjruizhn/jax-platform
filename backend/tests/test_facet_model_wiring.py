"""facet_models DB wiring into chat model selection (Bugs 1-3).

The admin panel writes the active model per facet into the `facet_models`
table, but chat.py historically read the model only from the static
config.toml. These tests pin the fix: the DB's active row must win, with
config.toml's model_default as the fallback when no active row exists, and
jax_local's system prompt must state the model it is actually running as.

The `client` fixture (conftest.py) enters TestClient as a context manager, so
the app's aiomysql pool lives on that session's anyio portal loop. DB rows are
therefore mutated through `client.portal.call(...)` (same pool, same loop) and
restored in a finally block so the seeded data other tests rely on is intact.

The outbound Ollama call is intercepted by patching `httpx.AsyncClient.post`
(AsyncMock, so no self-binding) — no network, no real Ollama needed.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from auth.jwt import create_access_token

# Non-integer ids on purpose: chat.py only touches the semantic-memory path
# when user_id/tenant_id parse as ints. Keeping them non-numeric skips memory
# entirely (that DB isn't provisioned in the test schema) and isolates the
# single outbound model call we assert on.
USER_ID = "test-facet-user"
TENANT_ID = "test-facet-tenant"

CONFIG_FALLBACK_MODEL = "qwen3-coder:30b"  # jax_local's config.toml model_default
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


def _active_jax_local_id(client):
    rows = client.portal.call(
        _db_fetch,
        "SELECT id FROM facet_models WHERE facet='jax_local' AND is_active=TRUE",
    )
    return rows[0][0] if rows else None


def _post_chat(client):
    """Force facet=jax_local, capture the model sent to Ollama."""
    mock_post = AsyncMock(return_value=_ollama_response())
    with patch.object(httpx.AsyncClient, "post", mock_post):
        resp = client.post(
            "/api/chat",
            json={"message": "que modelo sos", "facet": "jax_local"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 200, resp.text
    assert mock_post.call_count == 1, f"expected one Ollama call, got {mock_post.call_count}"
    payload = mock_post.call_args.kwargs["json"]
    return payload


def test_db_active_model_wins_over_config_toml(client):
    """The DB's active model_name must be what chat sends to Ollama, NOT the
    config.toml model_default. Reproduces Bug 1 on unmodified code."""
    prev_active = _active_jax_local_id(client)
    client.portal.call(_db_exec, "UPDATE facet_models SET is_active=FALSE WHERE facet='jax_local'")
    client.portal.call(
        _db_exec,
        "INSERT INTO facet_models (facet, provider_id, model_name, is_active) "
        "VALUES ('jax_local','ollama',%s,TRUE)",
        (SENTINEL_MODEL,),
    )
    try:
        payload = _post_chat(client)
        assert payload["model"] == SENTINEL_MODEL, (
            f"chat sent '{payload['model']}' but the DB's active model is "
            f"'{SENTINEL_MODEL}' — config.toml is still winning (Bug 1)"
        )
    finally:
        client.portal.call(
            _db_exec, "DELETE FROM facet_models WHERE facet='jax_local' AND model_name=%s",
            (SENTINEL_MODEL,),
        )
        if prev_active is not None:
            client.portal.call(
                _db_exec, "UPDATE facet_models SET is_active=TRUE WHERE id=%s", (prev_active,)
            )


def test_falls_back_to_config_when_no_active_row(client):
    """With no active facet_models row for jax_local, chat must fall back to
    config.toml's model_default rather than erroring."""
    prev_active = _active_jax_local_id(client)
    client.portal.call(_db_exec, "UPDATE facet_models SET is_active=FALSE WHERE facet='jax_local'")
    try:
        payload = _post_chat(client)
        assert payload["model"] == CONFIG_FALLBACK_MODEL, (
            f"expected config fallback '{CONFIG_FALLBACK_MODEL}', got '{payload['model']}'"
        )
    finally:
        if prev_active is not None:
            client.portal.call(
                _db_exec, "UPDATE facet_models SET is_active=TRUE WHERE id=%s", (prev_active,)
            )


def test_falls_back_to_config_when_db_query_raises(client):
    """If the facet_models query raises (e.g. MariaDB down/unreachable), chat
    must still fall back to config.toml's model_default rather than 502ing.
    jax_local is the local/offline-resilient facet — the rest of the chat
    path already degrades gracefully on DB failure (see _ensure_memory)."""
    mock_post = AsyncMock(return_value=_ollama_response())
    with patch("api.chat.get_pool", AsyncMock(side_effect=RuntimeError("DB down"))), \
         patch.object(httpx.AsyncClient, "post", mock_post):
        resp = client.post(
            "/api/chat",
            json={"message": "que modelo sos", "facet": "jax_local"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 200, resp.text
    assert mock_post.call_count == 1, f"expected one Ollama call, got {mock_post.call_count}"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["model"] == CONFIG_FALLBACK_MODEL, (
        f"expected config fallback '{CONFIG_FALLBACK_MODEL}' when the DB query raises, "
        f"got '{payload['model']}'"
    )


def test_jax_local_system_prompt_states_resolved_model(client):
    """jax_local's system prompt must name the actually-resolved (DB-active)
    model, so it stops confabulating its identity (Bug 3)."""
    prev_active = _active_jax_local_id(client)
    client.portal.call(_db_exec, "UPDATE facet_models SET is_active=FALSE WHERE facet='jax_local'")
    client.portal.call(
        _db_exec,
        "INSERT INTO facet_models (facet, provider_id, model_name, is_active) "
        "VALUES ('jax_local','ollama',%s,TRUE)",
        (SENTINEL_MODEL,),
    )
    try:
        payload = _post_chat(client)
        system_msg = payload["messages"][0]
        assert system_msg["role"] == "system"
        assert SENTINEL_MODEL in system_msg["content"], (
            "jax_local system prompt does not state the resolved model name"
        )
    finally:
        client.portal.call(
            _db_exec, "DELETE FROM facet_models WHERE facet='jax_local' AND model_name=%s",
            (SENTINEL_MODEL,),
        )
        if prev_active is not None:
            client.portal.call(
                _db_exec, "UPDATE facet_models SET is_active=TRUE WHERE id=%s", (prev_active,)
            )
