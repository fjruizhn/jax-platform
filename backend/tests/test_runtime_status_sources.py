"""Runtime-status accreditation reads only fixed server-owned projections."""
import asyncio
from datetime import datetime, timezone

import pytest

from jax_engine.events import event_bus
from jax_engine.state import JAXEngineState


class _Response:
    status_code = 200


class _Client:
    async def get(self, url, *args, **kwargs):
        return _Response()


def test_facet_status_snapshot_excludes_user_message_and_display_metadata():
    state = JAXEngineState()
    facet = state._state.facets["hyde"]
    facet.status = "thinking"
    facet.last_message = "private user-specific payload"
    facet.display_name = "Private display label"
    facet.last_update = "2026-10-02T12:00:00+00:00"

    snapshot = state.facet_runtime_status_snapshot("hyde")

    assert snapshot == ("thinking", datetime(2026, 10, 2, 12, tzinfo=timezone.utc))
    assert "private user-specific payload" not in repr(snapshot)
    assert "Private display label" not in repr(snapshot)
    assert state.facet_runtime_status_snapshot("not-registered") is None


def test_engine_status_snapshot_requires_completed_health_probe():
    state = JAXEngineState()

    assert state.engine_health_status_snapshot("las_manos") is None
    assert state.engine_health_status_snapshot("arbitrary-host") is None

    state._state.las_manos_alive = True
    state._state.last_health_check = "2026-10-02T12:00:00+00:00"
    assert state.engine_health_status_snapshot("las_manos") == (
        "alive", datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    )


def test_completed_steady_health_probe_refreshes_observation_time_without_change_event(monkeypatch):
    state = JAXEngineState()
    state._state.las_manos_alive = True
    state._state.last_health_check = "2026-10-01T00:00:00+00:00"
    published = []

    async def record(event):
        published.append(event)

    monkeypatch.setattr(event_bus, "publish", record)
    asyncio.run(state._check_las_manos_health(_Client()))

    refreshed = state.engine_health_status_snapshot("las_manos")
    assert refreshed is not None
    assert refreshed[0] == "alive"
    assert refreshed[1] > datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert published == []

