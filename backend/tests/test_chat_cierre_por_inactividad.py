"""Cierre de conversaciones web por inactividad (decisión de Fernando, 2026-10-05).

La memoria solo extrae hechos de conversaciones cerradas (ended_at). Antes,
`_conv_uuids` cerraba solo al apagar la app o por LRU, y había conversaciones
abiertas más de 7 días. Umbral: JAX_CONVERSACION_INACTIVIDAD_MIN (default 30).
El reloj es inyectable (`chat._reloj`) para no dormir en las pruebas.
"""
import asyncio
import logging

import pytest

import api.chat as chat


class _Memoria:
    is_connected = True

    def __init__(self):
        self.ended = []
        self.n = 0
        self.falla_end = False

    async def start_conversation(self, **kw):
        self.n += 1
        return f"conv-{self.n}"

    async def end_conversation(self, uuid_):
        if self.falla_end:
            raise RuntimeError("db caida")
        self.ended.append(uuid_)


class _Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def avanzar_min(self, m):
        self.t += m * 60


@pytest.fixture
def mundo(monkeypatch):
    mem = _Memoria()
    reloj = _Reloj()
    monkeypatch.setattr(chat, "MemoryDB", _Memoria)
    monkeypatch.setattr(chat, "_memory", mem)
    monkeypatch.setattr(chat, "_memory_ready", True)
    monkeypatch.setattr(chat, "_reloj", reloj)
    monkeypatch.delenv("JAX_CONVERSACION_INACTIVIDAD_MIN", raising=False)
    chat._conv_uuids.clear()
    chat._conv_ultima_actividad.clear()
    yield mem, reloj
    chat._conv_uuids.clear()
    chat._conv_ultima_actividad.clear()


def test_umbral_por_defecto_30_min(monkeypatch):
    monkeypatch.delenv("JAX_CONVERSACION_INACTIVIDAD_MIN", raising=False)
    assert chat.umbral_inactividad_segundos() == 30 * 60


def test_umbral_configurable_por_entorno(monkeypatch):
    monkeypatch.setenv("JAX_CONVERSACION_INACTIVIDAD_MIN", "10")
    assert chat.umbral_inactividad_segundos() == 600


@pytest.mark.parametrize("malo", ["abc", "0", "-5", ""])
def test_umbral_invalido_avisa_y_usa_el_default(monkeypatch, caplog, malo):
    monkeypatch.setenv("JAX_CONVERSACION_INACTIVIDAD_MIN", malo)
    with caplog.at_level(logging.WARNING):
        assert chat.umbral_inactividad_segundos() == 30 * 60
    if malo != "":
        assert any("JAX_CONVERSACION_INACTIVIDAD_MIN" in r.getMessage() for r in caplog.records)


async def test_dentro_del_umbral_reusa_la_conversacion(mundo):
    mem, reloj = mundo
    a = await chat._get_conv_uuid(1, "t", None)
    reloj.avanzar_min(29)
    b = await chat._get_conv_uuid(1, "t", None)
    assert a == b == "conv-1"
    assert mem.ended == []


async def test_actividad_renueva_el_plazo(mundo):
    mem, reloj = mundo
    await chat._get_conv_uuid(1, "t", None)
    reloj.avanzar_min(20)
    await chat._get_conv_uuid(1, "t", None)
    reloj.avanzar_min(20)  # 40 desde el inicio, 20 desde la última actividad
    assert await chat._get_conv_uuid(1, "t", None) == "conv-1"
    assert mem.ended == []


async def test_superado_el_umbral_cierra_y_abre_otra(mundo):
    mem, reloj = mundo
    await chat._get_conv_uuid(1, "t", None)
    reloj.avanzar_min(30)
    nueva = await chat._get_conv_uuid(1, "t", None)
    assert nueva == "conv-2"
    assert mem.ended == ["conv-1"]
    assert list(chat._conv_uuids.values()) == ["conv-2"]


async def test_si_el_cierre_falla_igual_abre_otra_y_avisa(mundo, caplog):
    mem, reloj = mundo
    await chat._get_conv_uuid(1, "t", None)
    mem.falla_end = True
    reloj.avanzar_min(31)
    with caplog.at_level(logging.WARNING):
        assert await chat._get_conv_uuid(1, "t", None) == "conv-2"
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_barrido_cierra_solo_las_inactivas_y_las_saca(mundo):
    mem, reloj = mundo
    await chat._get_conv_uuid(1, "t", None)   # conv-1
    reloj.avanzar_min(20)
    await chat._get_conv_uuid(2, "t", None)   # conv-2
    reloj.avanzar_min(15)                      # conv-1: 35 min, conv-2: 15 min
    n = await chat.cerrar_conversaciones_inactivas()
    assert n == 1
    assert mem.ended == ["conv-1"]
    assert list(chat._conv_uuids.values()) == ["conv-2"]
    assert "t:1:None" not in chat._conv_ultima_actividad


async def test_barrido_sin_memoria_no_lanza(monkeypatch):
    monkeypatch.setattr(chat, "_memory", None)
    monkeypatch.setattr(chat, "_memory_ready", False)
    assert await chat.cerrar_conversaciones_inactivas() == 0


async def test_barrido_con_cierre_fallido_avisa_y_sigue(mundo, caplog):
    mem, reloj = mundo
    await chat._get_conv_uuid(1, "t", None)
    mem.falla_end = True
    reloj.avanzar_min(40)
    with caplog.at_level(logging.WARNING):
        assert await chat.cerrar_conversaciones_inactivas() == 0
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_tarea_periodica_barre_y_es_cancelable(mundo, monkeypatch):
    mem, reloj = mundo
    await chat._get_conv_uuid(1, "t", None)
    reloj.avanzar_min(45)
    monkeypatch.setattr(chat, "INTERVALO_BARRIDO_INACTIVIDAD_S", 0.01)
    tarea = asyncio.create_task(chat.start_cierre_por_inactividad())
    for _ in range(100):
        if mem.ended:
            break
        await asyncio.sleep(0.01)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea
    assert mem.ended == ["conv-1"]


async def test_tarea_periodica_sobrevive_a_un_barrido_que_revienta(mundo, monkeypatch, caplog):
    mem, reloj = mundo
    llamadas = []

    async def _revienta():
        llamadas.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(chat, "cerrar_conversaciones_inactivas", _revienta)
    monkeypatch.setattr(chat, "INTERVALO_BARRIDO_INACTIVIDAD_S", 0.01)
    with caplog.at_level(logging.WARNING):
        tarea = asyncio.create_task(chat.start_cierre_por_inactividad())
        for _ in range(100):
            if len(llamadas) >= 2:
                break
            await asyncio.sleep(0.01)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
    assert len(llamadas) >= 2
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ---- cierre al arrancar (SQL) ----

class _Cursor:
    def __init__(self, log, filas=3):
        self.log, self.rowcount = log, filas

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        self.log.append((sql, params))


class _Conn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return _Cursor(self.log)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        pass


class _Pool:
    def __init__(self):
        self.log = []

    def acquire(self):
        return _Conn(self.log)


async def test_cierre_al_arrancar_emite_un_update_acotado_a_web_y_al_umbral(monkeypatch):
    import conversaciones_inactivas as ci
    monkeypatch.delenv("JAX_CONVERSACION_INACTIVIDAD_MIN", raising=False)
    pool = _Pool()
    n = await ci.cerrar_huerfanas_al_arrancar(pool)
    assert n == 3
    sql, params = pool.log[0]
    s = " ".join(sql.split()).lower()
    assert s.startswith("update conversations")
    assert "ended_at is null" in s
    assert "memory_processed" in s
    assert "axioma-web" in str(params) or "axioma-web" in s
    assert 30 in params  # minutos, del umbral configurado


async def test_cierre_al_arrancar_no_lanza_si_la_base_falla(caplog):
    import conversaciones_inactivas as ci

    class _Rota:
        def acquire(self):
            raise RuntimeError("sin base")

    with caplog.at_level(logging.WARNING):
        assert await ci.cerrar_huerfanas_al_arrancar(_Rota()) == 0
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ---- arranque y cableado en el lifespan ----

async def test_iniciar_cierra_huerfanas_y_deja_la_tarea_periodica(monkeypatch):
    import conversaciones_inactivas as ci
    llamadas = []

    async def _espia(pool):
        llamadas.append(pool)
        return 0

    async def _pool():
        return "POOL"

    monkeypatch.setattr(ci, "cerrar_huerfanas_al_arrancar", _espia)
    monkeypatch.setattr(chat, "INTERVALO_BARRIDO_INACTIVIDAD_S", 3600)
    tarea = await chat.iniciar_cierre_por_inactividad(_pool)
    try:
        assert llamadas == ["POOL"]
        assert not tarea.done()
    finally:
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea


async def test_iniciar_sin_pool_avisa_y_deja_la_tarea(monkeypatch, caplog):
    async def _sin_pool():
        raise RuntimeError("sin pool")

    monkeypatch.setattr(chat, "INTERVALO_BARRIDO_INACTIVIDAD_S", 3600)
    with caplog.at_level(logging.WARNING):
        tarea = await chat.iniciar_cierre_por_inactividad(_sin_pool)
    try:
        assert not tarea.done()
        assert any(r.levelno == logging.WARNING for r in caplog.records)
    finally:
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea


def test_el_lifespan_arranca_y_cancela_el_cierre_por_inactividad():
    """Cableado: el lifespan arranca la tarea y la cancela al apagar. (Correr
    el lifespan real exige DB; esto vale en el modo sin DB del CI.)"""
    import inspect
    import main
    fuente = inspect.getsource(main.lifespan.__wrapped__)
    assert "iniciar_cierre_por_inactividad(get_pool)" in fuente
    assert fuente.index("iniciar_cierre_por_inactividad") < fuente.index("yield")
    assert "tarea_inactividad.cancel()" in fuente.split("yield", 1)[1]
