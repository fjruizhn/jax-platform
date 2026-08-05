import httpx

import http_client


async def test_get_http_client_returns_same_instance_across_calls():
    try:
        c1 = await http_client.get_http_client()
        c2 = await http_client.get_http_client()
        assert c1 is c2
        assert isinstance(c1, httpx.AsyncClient)
    finally:
        await http_client.close_http_client()


async def test_close_http_client_resets_the_singleton():
    c1 = await http_client.get_http_client()
    await http_client.close_http_client()
    assert c1.is_closed

    c2 = await http_client.get_http_client()
    try:
        assert c2 is not c1
        assert not c2.is_closed
    finally:
        await http_client.close_http_client()
