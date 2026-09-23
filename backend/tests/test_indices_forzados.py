"""Chequeo del arranque de los índices forzados (db/indices_forzados.py,
2026-09-23).

- La lista sale del CÓDIGO: los `FORCE INDEX` reales de backend/ (hoy tres)
  y nada de lo que sólo se menciona en comentarios o docstrings.
- Con la base de la sesión SIN `idx_pipelines_descartados` hay un ERROR que
  lo nombra; con el índice, ninguno. Sin el índice, la vista Descartados da
  el 1176 (test_sin_idx_pipelines_descartados_la_consulta_del_usuario_revienta).
- El lifespan lo llama.
"""
import inspect
import logging
import textwrap

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


def test_si_la_comprobacion_falla_es_error_y_no_tumba(caplog):
    import asyncio

    class _PoolRoto:
        def acquire(self):
            raise ConnectionError("sin base")

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        ausentes = asyncio.run(mod.avisar_indices_forzados_ausentes(
            _PoolRoto(), {("t", "idx_x"): ["m.py:1"]}))
    assert ausentes == []
    assert "no se pudo comprobar" in caplog.text and "t.idx_x" in caplog.text


def test_el_lifespan_lo_llama():
    import main

    fuente = inspect.getsource(main.lifespan)
    assert "indices_forzados.indices_forzados" in fuente
    assert "indices_forzados.avisar_indices_forzados_ausentes(" in fuente
