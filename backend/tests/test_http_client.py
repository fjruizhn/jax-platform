import httpx

import http_client


async def test_get_http_client_returns_same_instance_across_calls():
    # Isolate from any pre-existing singleton (e.g. one created by the app's
    # real lifespan via the session-scoped `client` fixture used elsewhere in
    # the suite) so this test always exercises a fresh client of its own and
    # never closes a client another test/fixture still depends on.
    original = http_client._client
    http_client._client = None
    try:
        c1 = await http_client.get_http_client()
        c2 = await http_client.get_http_client()
        assert c1 is c2
        assert isinstance(c1, httpx.AsyncClient)
    finally:
        if http_client._client is not None:
            await http_client.close_http_client()
        http_client._client = original


async def test_close_http_client_resets_the_singleton():
    # Same isolation as above: operate on a private client, then restore
    # whatever singleton state existed before this test ran.
    original = http_client._client
    http_client._client = None
    try:
        c1 = await http_client.get_http_client()
        await http_client.close_http_client()
        assert c1.is_closed

        c2 = await http_client.get_http_client()
        assert c2 is not c1
        assert not c2.is_closed
    finally:
        if http_client._client is not None:
            await http_client.close_http_client()
        http_client._client = original


def test_shared_http_client_is_created_at_app_startup(client):
    assert http_client._client is not None
    assert isinstance(http_client._client, httpx.AsyncClient)
    assert not http_client._client.is_closed
