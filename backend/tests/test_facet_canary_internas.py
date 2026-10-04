"""La sonda periódica vigila también las facetas con binding aprobado que no
están en `personalities` (caso real: `el_juez`), y se salta con una misión del
Ejecutor en curso (2026-10-04).

Defecto de origen: el reaper de Jacobs alertaba «facet 'el_juez': unknown» cada
6 h desde el 2026-09-23. Su único evento en facet_health_event era del
rebinding de ese día: `canary_facets` solo probaba `personalities - {hyde}` y
el_juez (auditor C5, no auto-seleccionable) no está ahí. Hoy nada vigilaba su
salud.

NINGÚN test llama a un proveedor ni toca MariaDB: `_invoke_facet` es un espía y
la base es un sqlite en memoria que ejecuta el SQL REAL de la sonda
(`SQL_FACETAS_CON_BINDING_APROBADO`), no una copia de su lógica en un doble.
"""
import asyncio
import logging
import os
import sqlite3
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import facet_health
from jax_engine import facet_canary

# El lector vive en el repo `jax` (jacobs/facet_health.py); mismo mecanismo que
# test_jacobs_status_mapeo_completo.py. Se APENDEA, no se antepone: `jacobs` solo
# existe allí y no hay por qué dejar que `jax` tape ningún módulo de este repo.
_JAX = str(Path(os.environ["JAX_REPO_PATH"]))
if _JAX not in sys.path:
    sys.path.append(_JAX)
from jacobs.facet_health import evaluate_states  # noqa: E402


def _config():
    return {"personalities": {"jax_local": {}, "hyde": {}, "thot": {}, "ada": {}}}


class _Cur:
    def __init__(self, conn):
        self._cur = conn.cursor()

    async def execute(self, sql, args=()):
        self._cur.execute(sql, args)

    async def fetchall(self):
        return self._cur.fetchall()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self._cur.close()


class _Conn:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _Cur(self._conn)


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield _Conn(self._conn)


def _base(filas):
    """facet_binding mínima en sqlite; `filas` = (facet_key, role, approved_by, approved_at)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE facet_binding (facet_key TEXT, role TEXT, approved_by INTEGER, approved_at TEXT)")
    conn.executemany("INSERT INTO facet_binding VALUES (?,?,?,?)", filas)
    return conn


APROBADO = (1, "2026-09-23 10:00:00")
SIN_APROBAR = (None, None)


@pytest.fixture
def bindings(monkeypatch):
    """Devuelve una función que siembra facet_binding y deja la sonda leyendo de ahí."""
    # La fixture autouse del conftest ya parchea facet_health.get_pool; aquí se
    # parchea el de la sonda, que es el que lee facet_binding.
    def sembrar(filas):
        conn = _base(filas)

        async def get_pool():
            return _Pool(conn)

        monkeypatch.setattr(facet_canary, "get_pool", get_pool)
    return sembrar


@pytest.fixture
def espias(monkeypatch):
    ll = {"invoke": [], "record": []}

    async def invoke(facet, config, user_id, message, semantic_context=None, *, source="chat"):
        ll["invoke"].append((facet, source))
        return "listo", None

    async def record(facet, outcome, source, detail=None):
        ll["record"].append((facet, outcome, source))
        return True

    async def sin_mision():
        return None

    monkeypatch.setattr(facet_canary, "_invoke_facet", invoke)
    monkeypatch.setattr(facet_canary, "record_facet_health", record)
    monkeypatch.setattr(facet_canary.misiones, "_turno_en_curso", sin_mision)
    monkeypatch.setattr(facet_canary, "_load_config", _config)
    return ll


# (1) el_juez entra por su binding primary aprobado --------------------------

def test_el_juez_entra_por_su_binding_primary_aprobado(bindings):
    bindings([("el_juez", "primary", *APROBADO)])
    facets = asyncio.run(facet_canary.canary_facets(_config()))
    assert "el_juez" in facets
    assert {"jax_local", "thot", "ada"} <= set(facets), "personalities sigue entrando"
    assert "hyde" not in facets


def test_hyde_no_entra_ni_con_binding_aprobado(bindings):
    bindings([("hyde", "primary", *APROBADO), ("el_juez", "primary", *APROBADO)])
    assert "hyde" not in asyncio.run(facet_canary.canary_facets(_config()))


# (2) sin binding primary aprobado, no entra ---------------------------------

@pytest.mark.parametrize("filas", [
    [],                                                        # sin binding
    [("el_juez", "primary", *SIN_APROBAR)],                    # primary sin aprobar
    [("el_juez", "primary", 1, None)],                         # falta approved_at
    [("el_juez", "primary", None, "2026-09-23 10:00:00")],     # falta approved_by
    [("el_juez", "fallback_1", *APROBADO)],                    # aprobado pero no primary
    [("el_juez", "disabled", *APROBADO)],
])
def test_una_faceta_sin_binding_primary_aprobado_no_entra(bindings, filas):
    bindings(filas)
    assert "el_juez" not in asyncio.run(facet_canary.canary_facets(_config()))


def test_si_la_base_no_contesta_se_sondea_personalities_y_queda_warning(monkeypatch, caplog):
    async def get_pool():
        raise ConnectionError("base caida")
    monkeypatch.setattr(facet_canary, "get_pool", get_pool)
    with caplog.at_level(logging.WARNING):
        facets = asyncio.run(facet_canary.canary_facets(_config()))
    assert facets == ["ada", "jax_local", "thot"]
    assert any("facet_binding" in r.getMessage() for r in caplog.records)


# El camino de invocación: el_juez se sondea de punta a punta -----------------

def test_probe_all_sondea_a_el_juez_fuera_de_personalities(bindings, espias):
    bindings([("el_juez", "primary", *APROBADO)])
    asyncio.run(facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC))
    sondeadas = [f for f, _ in espias["invoke"]]
    assert "el_juez" in sondeadas
    assert ("el_juez", facet_canary.SOURCE_CANARY_PERIODIC) in espias["invoke"]


# (3) con misión en curso la sonda se salta y no escribe fallo ---------------

def test_con_mision_en_curso_se_salta_sin_invocar_ni_escribir(monkeypatch, espias, caplog):
    async def hay_mision():
        return {"mision_id": "m-1", "n": 1}
    monkeypatch.setattr(facet_canary.misiones, "_turno_en_curso", hay_mision)

    with caplog.at_level(logging.WARNING):
        out = asyncio.run(facet_canary.probe_facet(
            "el_juez", _config(), facet_canary.SOURCE_CANARY_PERIODIC))

    assert out == facet_canary.SALTADA_POR_MISION
    assert espias["invoke"] == [], "la sonda invocó al modelo con una misión en curso"
    assert espias["record"] == [], "un salto no es una caída: no se escribe fila en facet_health_event"
    assert any("el_juez" in r.getMessage() and "mision" in r.getMessage() for r in caplog.records), \
        "el salto no dejó rastro en el log"


def test_sin_mision_en_curso_si_sondea_a_el_juez(espias):
    out = asyncio.run(facet_canary.probe_facet(
        "el_juez", _config(), facet_canary.SOURCE_CANARY_PERIODIC))
    assert out is None
    assert espias["invoke"] == [("el_juez", facet_canary.SOURCE_CANARY_PERIODIC)]


def test_la_mision_se_mira_en_cada_faceta_y_una_que_arranca_corta_el_resto(
        bindings, monkeypatch, espias):
    """Como el freno: una misión que arranca a mitad del barrido corta lo que falta."""
    bindings([("el_juez", "primary", *APROBADO)])
    llamadas = []

    async def mision():
        llamadas.append(1)
        return {"mision_id": "m-1", "n": 1} if len(llamadas) > 2 else None
    monkeypatch.setattr(facet_canary.misiones, "_turno_en_curso", mision)

    resultados = asyncio.run(facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC))

    assert len(espias["invoke"]) == 2
    assert resultados[:2] == [None, None]
    assert resultados[2:] == [facet_canary.SALTADA_POR_MISION] * (len(resultados) - 2)
    assert espias["record"] == []


def test_si_no_se_puede_leer_la_mision_se_sondea_con_warning(monkeypatch, espias, caplog):
    async def roto():
        raise ConnectionError("base caida")
    monkeypatch.setattr(facet_canary.misiones, "_turno_en_curso", roto)
    with caplog.at_level(logging.WARNING):
        out = asyncio.run(facet_canary.probe_facet(
            "el_juez", _config(), facet_canary.SOURCE_CANARY_PERIODIC))
    assert out is None
    assert espias["invoke"] == [("el_juez", facet_canary.SOURCE_CANARY_PERIODIC)]
    assert any("mision" in r.getMessage() for r in caplog.records)


# (4) tras una sonda ok, el lector deja de dar unknown -----------------------

def test_tras_una_sonda_ok_de_el_juez_el_lector_deja_de_dar_unknown(monkeypatch, bindings):
    """La fila la escribe el `record_facet_health` REAL (INSERT capturado en un
    pool falso) y la lee el `evaluate_states` REAL del repo jax: la prueba cruza
    escritor y lector, que es donde estaba el defecto."""
    bindings([("el_juez", "primary", *APROBADO)])
    filas = []

    class _CurIns:
        async def execute(self, sql, args):
            assert "INSERT INTO facet_health_event" in sql
            filas.append(args)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    class _ConnIns:
        def cursor(self):
            return _CurIns()

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    class _PoolIns:
        def acquire(self):
            return _ConnIns()

    async def get_pool():
        return _PoolIns()

    async def invoke_real_en_lo_que_importa(facet, config, user_id, message, *, source):
        # Lo que hace _invoke_facet tras un dispatch exitoso (api/chat.py).
        await facet_health.record_facet_health(facet, "ok", source)
        return "listo", None

    async def sin_mision():
        return None

    monkeypatch.setattr(facet_health, "get_pool", get_pool)
    monkeypatch.setattr(facet_canary, "_invoke_facet", invoke_real_en_lo_que_importa)
    monkeypatch.setattr(facet_canary.misiones, "_turno_en_curso", sin_mision)
    monkeypatch.setattr(facet_canary, "_load_config", _config)

    ahora = time.time()
    antes = evaluate_states({}, ["el_juez"], ahora)
    assert antes == {"el_juez": "unknown"}, "sin eventos, el lector da unknown (el defecto original)"

    asyncio.run(facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC))

    eventos = {f: (ts, outcome) for f, outcome, _src, _detail, ts in filas}
    assert "el_juez" in eventos, "la sonda no escribió ninguna fila para el_juez"
    despues = evaluate_states(eventos, ["el_juez"], time.time())
    assert despues == {"el_juez": "ok"}
