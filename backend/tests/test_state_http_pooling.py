import asyncio

import http_client
from jax_engine.state import JAXEngineState


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class _FakeClient:
    def __init__(self):
        self.get_calls = []

    async def get(self, url, *args, **kwargs):
        self.get_calls.append(url)
        return _FakeResponse(200)


async def test_poll_las_manos_uses_the_shared_client():
    fake = _FakeClient()
    original = http_client._client
    http_client._client = fake
    state = JAXEngineState()
    try:
        task = asyncio.get_event_loop().create_task(state._poll_las_manos())
        await asyncio.sleep(0)  # let the first loop iteration run
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:  # fail-soft: patron estandar de teardown asyncio: await de una task cancelada lanza CancelledError, se espera
            pass
    finally:
        http_client._client = original

    assert len(fake.get_calls) >= 1
    assert fake.get_calls[0].endswith("/health")


async def test_poll_pipelines_uses_the_shared_client():
    fake = _FakeClient()
    original = http_client._client
    http_client._client = fake
    state = JAXEngineState()
    from jax_engine.schemas import PipelineState
    state._state.active_pipelines["pid-1"] = PipelineState(
        pipeline_id="pid-1", tenant_id="t1", user_id="u1", name="p", status="running",
    )
    try:
        task = asyncio.get_event_loop().create_task(state._poll_pipelines())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:  # fail-soft: mismo patron estandar de teardown asyncio que el test anterior
            pass
    finally:
        http_client._client = original

    assert any("/jacobs/pipeline/pid-1" in url for url in fake.get_calls)
