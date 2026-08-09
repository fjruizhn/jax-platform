"""cancel_pipeline() already released a tenant's concurrency slot
(resource_manager.release_pipeline) on explicit cancel, but a pipeline that
finishes on its own (completed/failed, detected by the poller) never did --
every non-cancelled pipeline permanently consumed one of the tenant's 3
concurrent slots (jax_engine/resource_manager.py MAX_PIPELINES_PER_TENANT).
"""
from jax_engine.resource_manager import resource_manager
from jax_engine.schemas import PipelineState
from jax_engine.state import JAXEngineState

TENANT_ID = "release-test-tenant"
USER_ID = "release-test-user"


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def get(self, url, *args, **kwargs):
        return self._response


def _make_state_with_pipeline(pid: str) -> JAXEngineState:
    state = JAXEngineState()
    state._state.active_pipelines[pid] = PipelineState(
        pipeline_id=pid, tenant_id=TENANT_ID, user_id=USER_ID,
        name="Test Pipeline", status="running",
    )
    return state


async def test_poll_one_pipeline_releases_the_resource_slot_on_natural_completion():
    pid = "pid-release-1"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    await resource_manager.admit_pipeline(TENANT_ID, pid)

    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "completed"}, "steps": []}))
    await state._poll_one_pipeline(client, pid, pipeline)

    assert pid not in state._state.active_pipelines
    assert await resource_manager.active_count(TENANT_ID) == 0


async def test_poll_one_pipeline_releases_the_resource_slot_on_failure():
    pid = "pid-release-2"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    await resource_manager.admit_pipeline(TENANT_ID, pid)

    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "failed"}, "steps": []}))
    await state._poll_one_pipeline(client, pid, pipeline)

    assert await resource_manager.active_count(TENANT_ID) == 0
