"""Escritor de facet_health_event. TODO el I/O esta parcheado: este
archivo NUNCA toca la DB real ni la red. Ver "Riesgo de costo" en el plan
(incidente _motor_v02_test.py, 2026-08-24: 11 dispatches reales a
produccion disparados por correr pytest)."""
import pytest
import facet_health
import api.health as health_module


class _FakeCursor:
    def __init__(self, sink): self.sink = sink
    async def execute(self, sql, params=None): self.sink.append((sql, params))
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _FakeConn:
    def __init__(self, sink): self.sink = sink
    def cursor(self): return _FakeCursor(self.sink)
    async def commit(self): self.sink.append(("COMMIT", None))
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _FakePool:
    def __init__(self, sink): self.sink = sink
    def acquire(self): return _FakeConn(self.sink)


def test_record_escribe_una_fila_con_epoch(monkeypatch):
    sink = []
    async def fake_pool(): return _FakePool(sink)
    monkeypatch.setattr(facet_health, "get_pool", fake_pool)

    import asyncio
    ok = asyncio.run(facet_health.record_facet_health(
        "thot", "provider_error", "chat", "HTTPStatusError: 502"))

    assert ok is True
    inserts = [s for s in sink if s[0] != "COMMIT"]
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "INSERT INTO facet_health_event" in sql
    assert params[0] == "thot"
    assert params[1] == "provider_error"
    assert params[2] == "chat"
    assert isinstance(params[4], float)   # ts epoch, NO string de fecha


def test_record_rechaza_outcome_invalido(monkeypatch):
    sink = []
    async def fake_pool(): return _FakePool(sink)
    monkeypatch.setattr(facet_health, "get_pool", fake_pool)

    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(facet_health.record_facet_health("thot", "inventado", "chat"))
    assert sink == []   # no escribio nada


def test_record_es_fail_soft_ante_error_de_db(monkeypatch, caplog):
    async def fake_pool(): raise RuntimeError("db caida")
    monkeypatch.setattr(facet_health, "get_pool", fake_pool)

    import asyncio
    ok = asyncio.run(facet_health.record_facet_health("thot", "ok", "chat"))

    assert ok is False              # no revienta el turno de chat
    assert caplog.records           # pero NO es silencioso


def test_el_fallo_del_escritor_queda_OBSERVABLE(monkeypatch, caplog):
    """Un escritor que pierde filas en silencio hace que todo lo demas
    mienta: la salud se calcula sobre datos incompletos y eso se ve igual
    que "no paso nada". El rastro NO puede vivir en la DB -- la DB es lo
    que esta caido."""
    facet_health.reset_write_failures()
    async def fake_pool(): raise RuntimeError("db caida")
    monkeypatch.setattr(facet_health, "get_pool", fake_pool)

    import asyncio
    asyncio.run(facet_health.record_facet_health("thot", "ok", "chat"))
    asyncio.run(facet_health.record_facet_health("ada", "ok", "chat"))

    stats = facet_health.write_failure_stats()
    assert stats["write_failures"] == 2
    assert "db caida" in stats["last_error"]
    # prefijo estable, para poder contarlo desde journalctl sin el endpoint
    assert any("facet_health_write_failed" in r.getMessage()
               for r in caplog.records)


def test_escritura_exitosa_no_incrementa_el_contador(monkeypatch):
    facet_health.reset_write_failures()
    sink = []
    async def fake_pool(): return _FakePool(sink)
    monkeypatch.setattr(facet_health, "get_pool", fake_pool)

    import asyncio
    asyncio.run(facet_health.record_facet_health("thot", "ok", "chat"))

    assert facet_health.write_failure_stats()["write_failures"] == 0


def test_health_endpoint_expone_facet_health_writer_sin_db(monkeypatch):
    """Correccion ronda 1: el endpoint GET /api/health quedaba modificado
    sin ningun test que corra en CI (el unico que lo tocaba, test_health.py,
    usa el fixture `client` y se saltea bajo JAX_CI_NO_DB=1 -- CI real corre
    justamente en ese modo, backend-tests-no-db). Este test llama la
    funcion de ruta directo, sin `client` y sin MariaDB: parchea
    engine_state y ws_hub con dobles minimos, nunca abre un pool real."""
    facet_health.reset_write_failures()

    class _FakeState:
        las_manos_alive = True
        connected_users = {"u1": object()}
        active_pipelines = {}

    class _FakeEngineState:
        def get_state(self):
            return _FakeState()

    class _FakeWsHub:
        async def connected_user_ids(self):
            return ["u1"]

    monkeypatch.setattr(health_module, "engine_state", _FakeEngineState())
    monkeypatch.setattr(health_module, "ws_hub", _FakeWsHub())

    import asyncio
    body = asyncio.run(health_module.health())

    assert body["facet_health_writer"] == facet_health.write_failure_stats()
    assert "write_failures" in body["facet_health_writer"]
    assert "last_error" in body["facet_health_writer"]
    for key in (
        "service", "status", "las_manos",
        "connected_users", "ws_connected_users", "active_pipelines",
    ):
        assert key in body
