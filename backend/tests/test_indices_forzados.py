"""Chequeo del arranque de los índices forzados (db/indices_forzados.py,
2026-09-23).

- La lista sale del CÓDIGO: los `FORCE INDEX` reales de backend/ (hoy tres)
  y nada de lo que sólo se menciona en comentarios o docstrings.
- Con la base de la sesión SIN `idx_pipelines_descartados` hay un ERROR que
  lo nombra; con el índice, ninguno. Sin el índice, la vista Descartados da
  el 1176 (test_sin_idx_pipelines_descartados_la_consulta_del_usuario_revienta).
- Tolerante (auditoría de jax-platform#160): un .py roto es un ERROR con su
  nombre y el escaneo sigue; si falla todo el escaneo, el lifespan completa
  igual (se corre el lifespan DE VERDAD, con sus dependencias sustituidas).
- Falta de tabla se nombra como tabla; índices sin distinguir mayúsculas;
  .venv/node_modules/__pycache__ ni se recorren.
"""
import asyncio
import logging
import textwrap
import types

import pytest

from db import indices_forzados as mod
from tests.identidades import sql

LOGGER = "db.indices_forzados"


def test_la_lista_sale_del_codigo_con_los_force_index_reales():
    forzados = mod.indices_forzados()
    assert set(forzados) == {
        ("jacobs_pipelines", "idx_pipelines_visibles"),
        ("jacobs_pipelines", "idx_pipelines_descartados"),
        ("jacobs_events", "idx_events_pipeline_tipo"),
    }, forzados
    for donde in forzados.values():
        assert all(d.startswith("api/pipelines.py:") for d in donde), donde


def test_comentarios_docstrings_y_tests_no_cuentan(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text('Q = "SELECT 1 FROM t FORCE INDEX (idx_de_test)"\n')
    (tmp_path / "m.py").write_text(textwrap.dedent('''\
        """SELECT * FROM t FORCE INDEX (idx_del_docstring)"""
        # SELECT * FROM t FORCE INDEX (idx_del_comentario)
        def f():
            """FROM t FORCE INDEX (idx_docstring_de_funcion)"""
            return ("SELECT a FROM tabla_a AS x "
                    "FORCE INDEX (idx_a, `idx_b`) WHERE 1")
        Q = "SELECT b FROM `tabla_b` FORCE INDEX(idx_c)"
        '''))
    assert mod.indices_forzados(tmp_path) == {
        ("tabla_a", "idx_a"): ["m.py:5"],
        ("tabla_a", "idx_b"): ["m.py:5"],
        ("tabla_b", "idx_c"): ["m.py:7"],
    }


def _correr(client, caplog):
    from db.connection import get_pool

    async def _chequeo():
        return await mod.avisar_indices_forzados_ausentes(await get_pool())

    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        return client.portal.call(_chequeo)


def test_sin_el_indice_hay_error_con_su_nombre_y_con_el_no(client, caplog):
    ddl = ("CREATE INDEX idx_pipelines_descartados ON jacobs_pipelines "
           "(user_id, tenant_id, status, descartado_at) ALGORITHM=INPLACE LOCK=NONE")
    assert _correr(client, caplog) == []
    assert not [r for r in caplog.records if r.name == LOGGER], caplog.text
    try:
        client.portal.call(sql, "DROP INDEX idx_pipelines_descartados ON jacobs_pipelines")
        ausentes = _correr(client, caplog)
        assert ausentes == [("jacobs_pipelines", "idx_pipelines_descartados")]
        errores = [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.ERROR]
        assert len(errores) == 1, caplog.text
        assert "idx_pipelines_descartados" in errores[0].getMessage()
        assert "jacobs_pipelines" in errores[0].getMessage()
        assert "api/pipelines.py:" in errores[0].getMessage()
    finally:
        client.portal.call(sql, ddl)
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)
    assert _correr(client, caplog) == []
    assert not [r for r in caplog.records if r.name == LOGGER], caplog.text


class _Cursor:
    def __init__(self, tablas, indices):
        self._tablas, self._indices, self._ultima = tablas, indices, None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, consulta, args=()):
        self._ultima = consulta

    async def fetchall(self):
        if "information_schema.TABLES" in self._ultima:
            return [(t,) for t in self._tablas]
        return list(self._indices)


class _PoolFalso:
    """Pool mínimo con las filas de information_schema que se le den."""

    def __init__(self, tablas=(), indices=()):
        self._cur = _Cursor(tablas, indices)

    def acquire(self):
        pool = self

        class _Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return pool._cur

        return _Conn()


def _errores(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.ERROR]


def test_si_la_comprobacion_falla_es_error_y_no_tumba(caplog):
    class _PoolRoto:
        def acquire(self):
            raise ConnectionError("sin base")

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        ausentes = asyncio.run(mod.avisar_indices_forzados_ausentes(
            _PoolRoto(), {("t", "idx_x"): ["m.py:1"]}))
    assert ausentes == []
    assert "no se pudo comprobar" in caplog.text and "t.idx_x" in caplog.text


def test_un_py_roto_es_error_con_su_nombre_y_el_escaneo_sigue(tmp_path, caplog):
    """Con el escaneo de 81dbbb2 (sin try por archivo) un SyntaxError o un
    UnicodeDecodeError salía del escaneo y tumbaba el arranque."""
    (tmp_path / "a_roto.py").write_text("def f(:\n")
    (tmp_path / "b_latin1.py").write_bytes('Q = "ñ"\n'.encode("latin-1"))
    (tmp_path / "c_bueno.py").write_text('Q = "SELECT a FROM t FORCE INDEX (idx_ok)"\n')
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        forzados = mod.indices_forzados(tmp_path)
    assert forzados == {("t", "idx_ok"): ["c_bueno.py:1"]}
    errores = _errores(caplog)
    assert len(errores) == 2, errores
    assert any("a_roto.py" in e and "SyntaxError" in e for e in errores), errores
    assert any("b_latin1.py" in e and "UnicodeDecodeError" in e for e in errores), errores


def test_venv_node_modules_y_pycache_ni_se_recorren(tmp_path, monkeypatch):
    for d in (".venv/lib", "node_modules/x", "__pycache__", "sub/node_modules", "sub/ok"):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "m.py").write_text('Q = "SELECT a FROM t FORCE INDEX (idx_de_afuera)"\n')
    (tmp_path / "sub" / "ok" / "m.py").write_text('Q = "SELECT a FROM t FORCE INDEX (idx_propio)"\n')
    abiertos = []
    leer_real = mod.Path.read_text

    def _espia(self, *a, **k):
        abiertos.append(self.relative_to(tmp_path).as_posix())
        return leer_real(self, *a, **k)

    monkeypatch.setattr(mod.Path, "read_text", _espia)
    assert mod.indices_forzados(tmp_path) == {("t", "idx_propio"): ["sub/ok/m.py:1"]}
    assert abiertos == ["sub/ok/m.py"]


def test_indice_sin_distinguir_mayusculas():
    pool = _PoolFalso(tablas=["jacobs_pipelines"], indices=[("jacobs_pipelines", "IDX_Pipelines_Descartados")])
    assert asyncio.run(mod.avisar_indices_forzados_ausentes(
        pool, {("jacobs_pipelines", "idx_pipelines_descartados"): ["api/x.py:1"]})) == []


def test_si_falta_la_tabla_el_error_nombra_la_tabla(caplog):
    pool = _PoolFalso(tablas=["jacobs_pipelines"], indices=[("jacobs_pipelines", "idx_a")])
    forzados = {("jacobs_pipelines", "idx_b"): ["api/x.py:1"], ("jacobs_events", "idx_c"): ["api/x.py:2"]}
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        ausentes = asyncio.run(mod.avisar_indices_forzados_ausentes(pool, forzados))
    assert ausentes == [("jacobs_events", "idx_c"), ("jacobs_pipelines", "idx_b")]
    errores = _errores(caplog)
    assert len(errores) == 2, errores
    assert "falta la TABLA jacobs_events" in errores[0], errores
    assert "falta el índice idx_b en la tabla jacobs_pipelines" in errores[1], errores


class _Nada:
    """Sustituto genérico de las dependencias del lifespan: cualquier
    atributo es él mismo, llamarlo también, y se puede esperar (await)."""

    def __getattr__(self, _nombre):
        return self

    def __call__(self, *a, **k):
        return self

    def __await__(self):
        return iter(())


def _correr_lifespan(monkeypatch):
    """Corre el lifespan REAL de main.py (arranque y cierre) con todas sus
    dependencias sustituidas por _Nada, MENOS el chequeo de índices: así se
    prueba lo que el lifespan hace con él, sin tocar la base, el pool de la
    sesión ni tareas de fondo. `asyncio` se sustituye sólo en create_task."""
    import main
    from api import chat

    nombres = [n for n in main.lifespan.__wrapped__.__code__.co_names
               if n in vars(main) and n not in ("indices_forzados", "asyncio", "logger")]
    for n in nombres:
        monkeypatch.setattr(main, n, _Nada())
    monkeypatch.setattr(main, "asyncio", types.SimpleNamespace(
        to_thread=asyncio.to_thread, create_task=lambda coro: None))

    async def _sin_conversaciones():
        return 0

    monkeypatch.setattr(chat, "flush_open_conversations", _sin_conversaciones)

    async def _ciclo():
        async with main.lifespan(main.app):
            return "arrancó"

    return asyncio.run(_ciclo())


def test_el_lifespan_llama_al_chequeo_de_indices(monkeypatch):
    llamadas = []

    async def _espia(pool, forzados=None):
        llamadas.append(forzados)
        return []

    monkeypatch.setattr(mod, "avisar_indices_forzados_ausentes", _espia)
    assert _correr_lifespan(monkeypatch) == "arrancó"
    assert len(llamadas) == 1
    assert ("jacobs_pipelines", "idx_pipelines_descartados") in llamadas[0]


def test_si_el_escaneo_revienta_el_lifespan_completa_igual(monkeypatch, caplog):
    """Con 81dbbb2 (to_thread del escaneo sin try en el lifespan) esto
    tumbaba el arranque."""
    def _revienta(*a, **k):
        raise PermissionError("sin permiso de lectura")

    monkeypatch.setattr(mod, "indices_forzados", _revienta)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        assert _correr_lifespan(monkeypatch) == "arrancó"
    assert any("PermissionError" in e and "NO quedaron comprobados" in e for e in _errores(caplog)), caplog.text


def test_un_paquete_tests_anidado_si_se_escanea(tmp_path):
    # Solo backend/tests se poda; x/tests/ con SQL real sigue escaneado (auditoría #160 r2).
    (tmp_path / "tests").mkdir(); (tmp_path / "x" / "tests").mkdir(parents=True)
    (tmp_path / "tests" / "t.py").write_text('Q = "SELECT 1 FROM a FORCE INDEX (idx_de_test)"\n')
    (tmp_path / "x" / "tests" / "m.py").write_text('Q = "SELECT 1 FROM b FORCE INDEX (idx_anidado)"\n')
    from db import indices_forzados as mod
    claves = {i for (_t, i) in mod.indices_forzados(tmp_path)}
    assert "idx_anidado" in claves and "idx_de_test" not in claves


# --- MINOR 1 (auditoría #160): JAX_CHEQUEO_INDICES_FORZADOS_TOPE_S validado -

@pytest.mark.parametrize("crudo", ["abc", "nan", "inf", "0", "-1"])
def test_tope_de_chequeo_invalido_da_error_de_configuracion_claro(monkeypatch, crudo):
    """Antes de este arreglo: "abc" tumbaba el import con un ValueError
    crudo, "nan" colgaba el event loop (math.ceil(nan) dentro de
    asyncio.wait_for), "inf" dejaba el chequeo sin tope de verdad y
    "0"/"-1" hacían que nunca corriera -- ninguno daba un error de
    configuración con el nombre de la variable."""
    monkeypatch.setenv(mod.VARIABLE_TOPE_CHEQUEO_S, crudo)
    with pytest.raises(mod.IndicesForzadosConfigInvalida) as e:
        mod.cargar_tope_chequeo_s()
    assert mod.VARIABLE_TOPE_CHEQUEO_S in str(e.value)
    assert crudo in str(e.value)


def test_tope_de_chequeo_valido_se_acepta(monkeypatch):
    monkeypatch.setenv(mod.VARIABLE_TOPE_CHEQUEO_S, "7.5")
    assert mod.cargar_tope_chequeo_s() == 7.5


def test_tope_de_chequeo_usa_15_por_default_si_falta(monkeypatch):
    monkeypatch.delenv(mod.VARIABLE_TOPE_CHEQUEO_S, raising=False)
    assert mod.cargar_tope_chequeo_s() == 15.0


# --- MINOR 2/3 (auditoría #160): timeout con fase, aislado de la base -------

class _CursorQueNoContesta:
    """Acepta el `execute` (la conexión "está aceptada") y nunca contesta,
    como el escenario real del docstring del módulo -- a diferencia de
    `_Cursor`/`_PoolFalso` de arriba, que sí responden."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *a, **k):
        await asyncio.sleep(30)

    async def fetchall(self):  # pragma: no cover - execute nunca vuelve
        return []


class _PoolQueNoContesta:
    def acquire(self):
        class _Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return _CursorQueNoContesta()

        return _Conn()


def test_una_base_que_no_contesta_no_cuelga_el_arranque(monkeypatch, caplog):
    """El timeout sólo puede venir de la base colgada: `indices_forzados`
    (el escaneo del código) se sustituye por un dict fijo y `obtener_pool`
    devuelve un pool de verdad de inmediato -- lo que se cuelga es la
    CONSULTA a information_schema (`_CursorQueNoContesta.execute`), así que
    si el test colgara por otra fase, este mismo test fallaría por
    timeout real en vez de pasar por la razón equivocada. El log tiene que
    nombrar el tope en segundos y la fase de la consulta -- antes salía
    "(TimeoutError: )" vacío, sin decir ni el tope ni dónde se colgó."""
    monkeypatch.setattr(mod, "TOPE_CHEQUEO_S", 0.2)
    monkeypatch.setattr(mod, "indices_forzados", lambda: {("t", "idx_x"): ["m.py:1"]})

    async def obtener_pool_rapido():
        return _PoolQueNoContesta()

    with caplog.at_level("ERROR"):
        asyncio.run(asyncio.wait_for(mod.chequeo_de_arranque(obtener_pool_rapido), 5))
    errores = _errores(caplog)
    assert len(errores) == 1, errores
    assert "NO quedaron comprobados" in errores[0]
    assert "0.2" in errores[0]
    assert "consultar information_schema" in errores[0], errores[0]
