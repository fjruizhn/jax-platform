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
from api import chat as chat_mod
from facet_resolver import ResolvedFacet

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
    """facet_binding + facet + provider minimas en sqlite.

    `filas` = (facet_key, role, approved_by, approved_at[, status[, transport[, base_url]]]).
    Los defectos (activa, http_openai_compat, sin base_url) son los de una faceta sana."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE facet_binding (facet_key TEXT, provider_id TEXT, role TEXT, approved_by INTEGER, approved_at TEXT)")
    conn.execute("CREATE TABLE facet (`key` TEXT, status TEXT, transport TEXT)")
    conn.execute("CREATE TABLE provider (id TEXT, base_url TEXT)")
    for fila in filas:
        key, role, ap_by, ap_at, *resto = fila
        status, transport, base_url = (list(resto) + [None] * 3)[:3]
        conn.execute("INSERT INTO facet_binding VALUES (?,?,?,?,?)", (key, "p_" + key, role, ap_by, ap_at))
        conn.execute("INSERT INTO facet VALUES (?,?,?)", (key, status or "active", transport or "http_openai_compat"))
        conn.execute("INSERT INTO provider VALUES (?,?)", ("p_" + key, base_url))
    return conn


APROBADO = (1, "2026-09-23 10:00:00")
SIN_APROBAR = (None, None)
OLLAMA = "http://localhost:11434"


@pytest.fixture(autouse=True)
def bindings(monkeypatch):
    """Autouse: NINGUN test de este archivo lee una base real (en CI-con-DB seria la
    compartida; sin DB, un skip). Por defecto la sonda lee un facet_binding vacío;
    devuelve la función que lo siembra.""" 
    # La fixture autouse del conftest ya parchea facet_health.get_pool; aquí se
    # parchea el de la sonda, que es el que lee facet_binding.
    def sembrar(filas):
        conn = _base(filas)

        async def get_pool():
            return _Pool(conn)

        monkeypatch.setattr(facet_canary, "get_pool", get_pool)
    monkeypatch.setenv("JAX_OLLAMA_URL", OLLAMA)
    sembrar([])
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
    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", sin_mision)
    monkeypatch.setattr(facet_canary, "_load_config", _config)
    # Los reintentos y topes de verdad son de minutos: aca, de milisegundos.
    monkeypatch.setattr(facet_canary, "CANARY_RETRY_SECONDS", 0.005)
    monkeypatch.setattr(facet_canary, "CANARY_DEFERRED_MAX_SECONDS", 0.2)
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
    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", hay_mision)

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
    """Como el freno: una misión que arranca a mitad del barrido corta lo que falta
    (lo cortado queda diferido: ver los tests de reintento)."""
    bindings([("el_juez", "primary", *APROBADO)])
    monkeypatch.setattr(facet_canary, "CANARY_DEFERRED_MAX_SECONDS", 0.05)
    llamadas = []

    async def mision():
        llamadas.append(1)
        return {"mision_id": "m-1", "n": 1} if len(llamadas) > 2 else None
    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", mision)

    resultados = asyncio.run(asyncio.wait_for(
        facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC), timeout=2))

    assert len(espias["invoke"]) == 2
    assert resultados[:2] == [None, None]
    assert resultados[2:] == [facet_canary.SALTADA_POR_MISION] * (len(resultados) - 2)
    assert espias["record"] == []


def test_si_no_se_puede_leer_la_mision_se_sondea_con_warning(monkeypatch, espias, caplog):
    async def roto():
        raise ConnectionError("base caida")
    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", roto)
    with caplog.at_level(logging.WARNING):
        out = asyncio.run(facet_canary.probe_facet(
            "el_juez", _config(), facet_canary.SOURCE_CANARY_PERIODIC))
    assert out is None
    assert espias["invoke"] == [("el_juez", facet_canary.SOURCE_CANARY_PERIODIC)]
    assert any("mision" in r.getMessage() for r in caplog.records)


# MAJOR 4: solo entran internas activas y que la sonda mide bien -------------

def test_una_faceta_con_binding_aprobado_pero_disabled_no_entra(bindings):
    bindings([("el_juez", "primary", *APROBADO, "disabled")])
    assert "el_juez" not in asyncio.run(facet_canary.canary_facets(_config()))


def test_una_interna_con_proveedor_ollama_de_otro_origen_no_entra_y_avisa(bindings, caplog):
    """Caso real: auditor_local -> ollama_cpu en :11435. _call_ollama ignora el
    base_url y va a JAX_OLLAMA_URL: se mediria otro servicio."""
    bindings([("auditor_local", "primary", *APROBADO, "active", "ollama", "http://127.0.0.1:11435/v1"),
              ("el_juez", "primary", *APROBADO, "active", "ollama", OLLAMA + "/v1")])
    with caplog.at_level(logging.WARNING):
        facets = asyncio.run(facet_canary.canary_facets(_config()))
    assert "auditor_local" not in facets
    assert "el_juez" in facets, "el mismo origen con /v1 (como esta en la base) SI entra"
    assert any("auditor_local" in r.getMessage() for r in caplog.records), "el descarte no nombra a la faceta"


def test_una_interna_ollama_sin_base_url_entra(bindings):
    bindings([("el_juez", "primary", *APROBADO, "active", "ollama", None)])
    assert "el_juez" in asyncio.run(facet_canary.canary_facets(_config()))


def test_un_bug_nuestro_en_la_lectura_NO_se_traga(monkeypatch):
    """Fail-open solo ante errores de base o de tiempo."""
    async def get_pool():
        raise AttributeError("bug nuestro")
    monkeypatch.setattr(facet_canary, "get_pool", get_pool)
    with pytest.raises(AttributeError):
        asyncio.run(facet_canary.canary_facets(_config()))


def test_un_bug_nuestro_al_leer_la_mision_NO_se_traga(monkeypatch):
    async def roto():
        raise TypeError("bug nuestro")
    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", roto)
    with pytest.raises(TypeError):
        asyncio.run(facet_canary._mision_en_curso())


def test_misiones_exponeturno_en_curso_publica(monkeypatch):
    from ejecutor import misiones

    async def privada():
        return {"mision_id": "m", "n": 1}
    monkeypatch.setattr(misiones, "turno_en_curso", privada)
    assert asyncio.run(misiones.turno_en_curso()) == {"mision_id": "m", "n": 1}


# MAJOR 1: diferir y no descartar --------------------------------------------

def test_un_salto_por_mision_se_reintenta_y_se_sondea_cuando_la_mision_termina(
        monkeypatch, espias):
    estado = {"checks": 0}

    async def mision():
        estado["checks"] += 1
        # En curso en el barrido (1 chequeo por faceta: 3) y en el primer reintento; termina despues.
        return {"mision_id": "m-1", "n": 1} if estado["checks"] <= 4 else None
    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", mision)

    resultados = asyncio.run(facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC))

    sondeadas = sorted(f for f, _ in espias["invoke"])
    assert sondeadas == ["ada", "jax_local", "thot"], "las diferidas no se sondearon al terminar la mision"
    assert facet_canary.SALTADA_POR_MISION not in resultados
    assert espias["record"] == [], "un salto no escribe fila de fallo"


def test_el_reintento_respeta_su_tope_y_avisa(monkeypatch, espias, caplog):
    async def siempre():
        return {"mision_id": "m-1", "n": 1}
    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", siempre)

    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING):
        # wait_for: si el tope no existiera el reintento seria infinito y el test
        # colgaria; asi falla legible.
        resultados = asyncio.run(asyncio.wait_for(
            facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC), timeout=2))

    assert time.monotonic() - t0 < 2, "el reintento no respeto su tope"
    assert espias["invoke"] == [] and espias["record"] == []
    assert resultados == [facet_canary.SALTADA_POR_MISION] * 3
    assert any("sonda diferida agotada" in r.getMessage() and "mision en curso desde" in r.getMessage()
               for r in caplog.records)


def test_el_tope_diferido_no_consume_el_del_barrido(monkeypatch, espias):
    """La mision termina DESPUES de que vence el tope del barrido: si el reintento
    viviera dentro de ese tope (mutacion M6) se cortaria con el barrido y las
    diferidas nunca se sondearian."""
    t0 = time.monotonic()

    async def mision():
        return {"mision_id": "m-1", "n": 1} if time.monotonic() - t0 < 0.6 else None
    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", mision)
    monkeypatch.setattr(facet_canary, "CANARY_SWEEP_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(facet_canary, "CANARY_RETRY_SECONDS", 0.2)
    monkeypatch.setattr(facet_canary, "CANARY_DEFERRED_MAX_SECONDS", 5)

    asyncio.run(facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC))

    assert time.monotonic() - t0 > 0.6, "los reintentos no duraron mas que el tope del barrido: la prueba no prueba"
    assert sorted(f for f, _ in espias["invoke"]) == ["ada", "jax_local", "thot"]


# MAJOR 2: el rebind sondea siempre ------------------------------------------

def test_el_rebind_sondea_y_escribe_su_fila_con_mision_en_curso(monkeypatch, espias):
    async def hay_mision():
        return {"mision_id": "m-1", "n": 1}
    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", hay_mision)
    monkeypatch.setattr(facet_canary, "invalidate_facet_cache", lambda k: True)
    monkeypatch.setattr(facet_canary, "CANARY_INTERVAL_SECONDS", 3600)

    # Un _invoke_facet fiel: el que de verdad escribe la fila es el envoltorio.
    async def invoke(facet, config, user_id, message, semantic_context=None, *, source="chat"):
        espias["invoke"].append((facet, source))
        await facet_canary.record_facet_health(facet, "ok", source)
        return "listo", None
    monkeypatch.setattr(facet_canary, "_invoke_facet", invoke)

    out = asyncio.run(facet_canary.probe_after_rebind("el_juez"))

    assert out is None
    assert espias["invoke"] == [("el_juez", facet_canary.SOURCE_CANARY_REBIND)]
    assert espias["record"] == [("el_juez", "ok", facet_canary.SOURCE_CANARY_REBIND)]


# MAJOR 3: tope por faceta ----------------------------------------------------

def test_una_faceta_que_excede_su_tope_no_impide_sondear_las_siguientes(monkeypatch, espias, caplog):
    monkeypatch.setattr(facet_canary, "CANARY_FACET_TIMEOUT_SECONDS", 0.05)

    async def invoke(facet, config, user_id, message, semantic_context=None, *, source="chat"):
        if facet == "ada":                      # la primera en orden alfabetico
            await asyncio.sleep(10)
        espias["invoke"].append((facet, source))
        return "listo", None
    monkeypatch.setattr(facet_canary, "_invoke_facet", invoke)

    with caplog.at_level(logging.WARNING):
        resultados = asyncio.run(facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC))

    assert [f for f, _ in espias["invoke"]] == ["jax_local", "thot"], "una lenta cancelo a las demas"
    assert resultados[0] == facet_canary.OUTCOME_PROBE_ERROR
    assert any("ada" in r.getMessage() and "tope" in r.getMessage() for r in caplog.records)


def test_una_faceta_cortada_por_su_tope_escribe_la_fila_probe_error(monkeypatch, espias):
    """Sin la fila el reaper ve `unknown` (sin causa); con ella, `down` con causa."""
    monkeypatch.setattr(facet_canary, "CANARY_FACET_TIMEOUT_SECONDS", 0.05)

    async def invoke(facet, config, user_id, message, semantic_context=None, *, source="chat"):
        await asyncio.sleep(10)
    monkeypatch.setattr(facet_canary, "_invoke_facet", invoke)
    escritos = []

    async def record(facet, outcome, source, detail=None):
        escritos.append((facet, outcome, source, detail))
        return True
    monkeypatch.setattr(facet_canary, "record_facet_health", record)

    asyncio.run(facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC))

    periodic = facet_canary.SOURCE_CANARY_PERIODIC
    assert escritos == [(f, "probe_error", periodic, "tope de faceta")
                        for f in ("ada", "jax_local", "thot")]


def test_la_escritura_del_tope_de_faceta_tiene_tope_de_base(monkeypatch, espias, caplog):
    """Una base colgada no puede colgar el barrido por la puerta de atras."""
    monkeypatch.setattr(facet_canary, "CANARY_FACET_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(facet_canary, "CANARY_DB_TIMEOUT_SECONDS", 0.05)

    async def invoke(facet, config, user_id, message, semantic_context=None, *, source="chat"):
        await asyncio.sleep(10)

    async def record_colgado(*a, **k):
        await asyncio.sleep(10)
    monkeypatch.setattr(facet_canary, "_invoke_facet", invoke)
    monkeypatch.setattr(facet_canary, "record_facet_health", record_colgado)

    with caplog.at_level(logging.WARNING):
        resultados = asyncio.run(asyncio.wait_for(
            facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC), timeout=5))

    assert resultados == [facet_canary.OUTCOME_PROBE_ERROR] * 3
    assert any("no se pudo registrar el tope de faceta" in r.getMessage() for r in caplog.records)


def test_los_presupuestos_cumplen_la_cuenta_del_hueco():
    """La cuenta del comentario, DERIVADA de las constantes (no copiada): el hueco
    maximo entre dos eventos de una faceta suma los DOS ciclos vecinos
    (3600 + 2*S + 2*D) y tiene que quedar bajo la ventana REAL del lector. Falla
    si alguien sube S por encima del techo."""
    from jacobs.facet_health import HEALTH_WINDOW_SECONDS

    mision_normal = 200
    D = mision_normal + facet_canary.CANARY_RETRY_SECONDS      # el ultimo reintento cae <= 1 paso despues
    S = facet_canary.CANARY_SWEEP_TIMEOUT_SECONDS
    intervalo = 3600
    assert 2 * intervalo == HEALTH_WINDOW_SECONDS, "la ventana del lector ya no es 2 x intervalo: rehacer la cuenta"
    assert intervalo + 2 * S + 2 * D < HEALTH_WINDOW_SECONDS

    techo = (HEALTH_WINDOW_SECONDS - intervalo - 2 * D - 1) // 2
    legitimo = 5 * (125 + 10) + 2 * (360 + 10) + 10
    assert legitimo <= S <= techo, f"S={S} fuera de [{legitimo}, {techo}]"
    assert facet_canary.CANARY_FACET_TIMEOUT_SECONDS >= 360 + 10


def test_el_origen_trata_localhost_127_y_ipv6_como_el_mismo_host():
    o = facet_canary._origen
    assert o("http://localhost:11434/v1") == o("http://127.0.0.1:11434") == o("http://[::1]:11434")
    assert o("http://localhost:11434") != o("http://localhost:11435")
    assert o("http://localhost:11434") != o("http://otra-maquina:11434")


def test_el_juez_en_localhost_entra_con_JAX_OLLAMA_URL_en_127(bindings, monkeypatch):
    monkeypatch.setenv("JAX_OLLAMA_URL", "http://127.0.0.1:11434")
    bindings([("el_juez", "primary", *APROBADO, "active", "ollama", "http://localhost:11434/v1")])
    assert "el_juez" in asyncio.run(facet_canary.canary_facets(_config()))


# MINOR 1: el _invoke_facet REAL despacha a una faceta fuera de personalities --

def test_el_invoke_facet_real_despacha_a_el_juez_fuera_de_personalities(monkeypatch):
    escritos = []
    llamadas = []

    async def resolve(facet_key):
        return ResolvedFacet(
            key=facet_key, provider_id="ollama", base_url=OLLAMA + "/v1", model="qwen-juez",
            credential="", transport="ollama", persona=None, params=None,
            max_tokens_param=None, max_output_tokens=None)

    async def call_ollama(system_prompt, history, message, config, model, *, imagenes=()):
        llamadas.append(model)
        return "listo", 3, 1

    async def record(facet, outcome, source, detail=None):
        escritos.append((facet, outcome, source))
        return True

    monkeypatch.setattr(facet_canary, "_invoke_facet", chat_mod._invoke_facet)  # el REAL, no el espia del conftest
    monkeypatch.setattr(chat_mod, "resolve_facet", resolve)
    monkeypatch.setattr(chat_mod, "_call_ollama", call_ollama)
    monkeypatch.setattr(chat_mod, "record_facet_health", record)
    async def sin_mision():
        return None
    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", sin_mision)

    # El registro de uso de la sonda (request_type 'canario', PR 8 del diseno
    # del tablero de consumo) tiene sus propios tests en
    # tests/test_facet_canary_uso.py, con DB. Este prueba el DESPACHO: sin
    # esto, el record_usage real pide pool y el test pasaria a necesitar DB
    # (skip en el job sin DB, y una fila 'canario' sin limpiar en el con DB).
    async def sin_registro_de_uso(*args, **kwargs):
        return None
    monkeypatch.setattr(facet_canary, "record_usage", sin_registro_de_uso)

    out = asyncio.run(facet_canary.probe_facet(
        "el_juez", _config(), facet_canary.SOURCE_CANARY_PERIODIC))

    assert out is None
    assert llamadas == ["qwen-juez"], "el_juez no se despacho al modelo de su binding"
    assert escritos == [("el_juez", "ok", facet_canary.SOURCE_CANARY_PERIODIC)]


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
    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", sin_mision)
    monkeypatch.setattr(facet_canary, "_load_config", _config)

    ahora = time.time()
    antes = evaluate_states({}, ["el_juez"], ahora)
    assert antes == {"el_juez": "unknown"}, "sin eventos, el lector da unknown (el defecto original)"

    asyncio.run(facet_canary.probe_all(facet_canary.SOURCE_CANARY_PERIODIC))

    eventos = {f: (ts, outcome) for f, outcome, _src, _detail, ts in filas}
    assert "el_juez" in eventos, "la sonda no escribió ninguna fila para el_juez"
    despues = evaluate_states(eventos, ["el_juez"], time.time())
    assert despues == {"el_juez": "ok"}
