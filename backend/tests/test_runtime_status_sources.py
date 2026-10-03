"""Runtime-status accreditation reads only fixed server-owned projections."""
import asyncio
import threading
from typing import ClassVar
from datetime import datetime, timezone

import pytest

from jax_engine.events import event_bus
from jax_engine import state as state_module
from jax_engine.schemas import EcosystemState
from jax_engine.state import JAXEngineState


class _Response:
    def __init__(self, status_code=200):
        self.status_code = status_code


class _Client:
    def __init__(self, status_code=200):
        self._status_code = status_code

    async def get(self, url, *args, **kwargs):
        return _Response(self._status_code)


class _BlockingHealthCompatibilityState(EcosystemState):
    """Test double that pauses immediately after the legacy status write."""

    writer_paused: ClassVar[threading.Event | None] = None
    resume_writer: ClassVar[threading.Event | None] = None

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name == "las_manos_alive" and type(self).writer_paused is not None:
            type(self).writer_paused.set()
            assert type(self).resume_writer is not None
            assert type(self).resume_writer.wait(timeout=2)


def _at(hour: int) -> datetime:
    return datetime(2026, 10, 3, hour, tzinfo=timezone.utc)


def _commit(state: JAXEngineState, alive: bool, observed_at: datetime) -> None:
    """Commit a completed probe without exercising HTTP in snapshot-only tests."""
    state._commit_las_manos_health_observation(alive, observed_at)


def test_facet_status_snapshot_excludes_user_message_and_display_metadata():
    state = JAXEngineState()
    facet = state._state.facets["hyde"]
    facet.status = "thinking"
    facet.last_message = "private user-specific payload"
    facet.display_name = "Private display label"
    facet.last_update = "2026-10-02T12:00:00+00:00"

    snapshot = state.facet_runtime_status_snapshot("hyde")

    assert snapshot[0] == "thinking"
    assert snapshot[1] > datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    assert snapshot[1] <= datetime.now(timezone.utc)
    assert "private user-specific payload" not in repr(snapshot)
    assert "Private display label" not in repr(snapshot)
    assert state.facet_runtime_status_snapshot("not-registered") is None


def test_stable_facet_state_gets_fresh_read_time_without_rewriting_transition_time():
    state = JAXEngineState()
    facet = state._state.facets["hyde"]
    facet.status = "idle"
    facet.last_update = "2026-10-01T00:00:00+00:00"

    status, observed_at = state.facet_runtime_status_snapshot("hyde")

    assert status == "idle"
    assert observed_at > datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert facet.last_update == "2026-10-01T00:00:00+00:00"


def test_engine_status_keeps_health_probe_time_instead_of_freshening_on_read():
    state = JAXEngineState()
    _commit(state, True, _at(1))

    status, observed_at = state.engine_health_status_snapshot("las_manos")

    assert status == "alive"
    assert observed_at == _at(1)


def test_facet_snapshot_waits_for_atomic_status_transition():
    state = JAXEngineState()
    facet = state._state.facets["hyde"]
    started = threading.Event()
    result = {}

    def read_snapshot():
        started.set()
        result["snapshot"] = state.facet_runtime_status_snapshot("hyde")

    with state._facet_status_lock:
        reader = threading.Thread(target=read_snapshot)
        reader.start()
        assert started.wait(timeout=2)
        facet.status = "thinking"
        facet.last_update = "2026-10-02T12:00:00+00:00"
    reader.join(timeout=2)

    assert not reader.is_alive()
    assert result["snapshot"][0] == "thinking"


def test_engine_status_snapshot_requires_completed_health_probe():
    state = JAXEngineState()

    assert state.engine_health_status_snapshot("las_manos") is None
    assert state.engine_health_status_snapshot("arbitrary-host") is None

    # Compatibility fields are not health evidence before the first completed
    # probe; only the atomic observation reference accredits ENGINE_STATUS.
    state._state.las_manos_alive = True
    state._state.last_health_check = "2026-10-02T12:00:00+00:00"
    assert state.engine_health_status_snapshot("las_manos") is None

    _commit(state, True, datetime(2026, 10, 2, 12, tzinfo=timezone.utc))
    assert state.engine_health_status_snapshot("las_manos") == (
        "alive", datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    )


def test_completed_steady_health_probe_refreshes_observation_time_without_change_event(monkeypatch):
    state = JAXEngineState()
    _commit(state, True, datetime(2026, 10, 1, tzinfo=timezone.utc))
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


@pytest.mark.parametrize(("status_code", "expected"), [(200, "alive"), (503, "down")])
def test_first_completed_probe_creates_the_only_initial_engine_observation(monkeypatch, status_code, expected):
    state = JAXEngineState()
    probe_time = _at(2)
    monkeypatch.setattr(state_module, "utc_ahora", lambda: probe_time.replace(tzinfo=None))

    asyncio.run(state._check_las_manos_health(_Client(status_code)))

    assert state.engine_health_status_snapshot("las_manos") == (expected, probe_time)


@pytest.mark.parametrize(("alive", "status_code"), [(True, 200), (False, 503)])
def test_repeated_same_status_probe_refreshes_atomic_observation_time(monkeypatch, alive, status_code):
    state = JAXEngineState()
    _commit(state, alive, _at(1))
    monkeypatch.setattr(state_module, "utc_ahora", lambda: _at(2).replace(tzinfo=None))
    published = []
    state.register_user("operator", "tenant", "operator")

    async def record(event):
        published.append(event)

    monkeypatch.setattr(event_bus, "publish", record)
    asyncio.run(state._check_las_manos_health(_Client(status_code)))

    expected = "alive" if alive else "down"
    assert state.engine_health_status_snapshot("las_manos") == (expected, _at(2))
    assert published == []


@pytest.mark.parametrize(("old_alive", "new_alive"), [(True, False), (False, True)])
def test_engine_snapshot_never_mixes_adjacent_probe_observations(old_alive, new_alive):
    state = JAXEngineState()
    old = _at(1)
    new = _at(2)
    _commit(state, old_alive, old)
    writer_ready = threading.Event()
    reader_ready = threading.Event()
    result = {}

    def publish_new_observation():
        writer_ready.set()
        state._commit_las_manos_health_observation(new_alive, new)

    def read_snapshot():
        reader_ready.set()
        result["snapshot"] = state.engine_health_status_snapshot("las_manos")

    # Both operations are held at the same atomic-publication boundary. Once
    # released, the reader can see either whole committed observation, never
    # a status from one probe paired with the other's timestamp.
    with state._health_status_lock:
        writer = threading.Thread(target=publish_new_observation)
        reader = threading.Thread(target=read_snapshot)
        writer.start()
        reader.start()
        assert writer_ready.wait(timeout=2)
        assert reader_ready.wait(timeout=2)
    writer.join(timeout=2)
    reader.join(timeout=2)

    assert not writer.is_alive()
    assert not reader.is_alive()
    assert result["snapshot"] in {
        ("alive" if old_alive else "down", old),
        ("alive" if new_alive else "down", new),
    }


def test_engine_snapshot_reads_do_not_refresh_the_committed_probe_time():
    state = JAXEngineState()
    _commit(state, True, _at(1))

    assert state.engine_health_status_snapshot("las_manos") == ("alive", _at(1))
    assert state.engine_health_status_snapshot("las_manos") == ("alive", _at(1))


@pytest.mark.parametrize(("old_alive", "new_status_code"), [(True, 503), (False, 200)])
def test_engine_snapshot_blocks_during_compatibility_write_and_never_returns_mixed_probe_tuple(
    monkeypatch, old_alive, new_status_code
):
    """Pause the real writer after compatibility status changes, before it can finish.

    Old code had no health lock: a reader completed here as ``new status @
    old timestamp``. The atomic implementation holds the reader until the
    full observation has been published, so it returns only the new tuple.
    """
    state = JAXEngineState()
    old = _at(1)
    new = _at(2)
    new_alive = new_status_code == 200
    _commit(state, old_alive, old)
    state._state = _BlockingHealthCompatibilityState(**state._state.model_dump())
    monkeypatch.setattr(state_module, "utc_ahora", lambda: new.replace(tzinfo=None))
    writer_paused = threading.Event()
    resume_writer = threading.Event()
    reader_started = threading.Event()
    reader_finished = threading.Event()
    result = {}
    def write_probe():
        asyncio.run(state._check_las_manos_health(_Client(new_status_code)))

    def read_snapshot():
        reader_started.set()
        result["snapshot"] = state.engine_health_status_snapshot("las_manos")
        reader_finished.set()

    writer = threading.Thread(target=write_probe)
    _BlockingHealthCompatibilityState.writer_paused = writer_paused
    _BlockingHealthCompatibilityState.resume_writer = resume_writer
    try:
        writer.start()
        assert writer_paused.wait(timeout=2)
        reader = threading.Thread(target=read_snapshot)
        reader.start()
        assert reader_started.wait(timeout=2)
        # This bounded event wait is the scheduling proof: old code completes
        # here with a mixed tuple; the health lock keeps the new reader out.
        assert not reader_finished.wait(timeout=1)
        resume_writer.set()
        writer.join(timeout=2)
        reader.join(timeout=2)
    finally:
        resume_writer.set()
        _BlockingHealthCompatibilityState.writer_paused = None
        _BlockingHealthCompatibilityState.resume_writer = None

    assert not writer.is_alive()
    assert not reader.is_alive()
    assert result["snapshot"] == (("alive" if new_alive else "down"), new)
