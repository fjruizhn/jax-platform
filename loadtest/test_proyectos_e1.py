"""Tests puros (sin red ni DB) de la limpieza de `proyectos_e1.py` (E1, T10, ronda 1, I-1):
la limpieza borra SOLO lo sembrado por la corrida cuyo sufijo se le da.

`python3 -m pytest loadtest/test_proyectos_e1.py -q`
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import proyectos_e1 as pe  # noqa: E402

MIO, AJENO = "a1b2c3d4", "0f9e8d7c"


def _like(patron: str, texto: str) -> bool:
    return re.fullmatch(re.escape(patron).replace("%", ".*"), texto) is not None


def _nombres(sufijo: str) -> tuple[list[str], list[str]]:
    proyectos = [f"{pe.PREFIJO_PROYECTO}{sufijo}-{i:04d}" for i in (0, 999)]
    correos = [f"{pe.PREFIJO_PROYECTO}{k}-{sufijo}@{pe.DOMINIO}" for k in ("miembro", "dueno", "u00", "u39")]
    return proyectos, correos


def test_los_patrones_cubren_todo_lo_de_la_corrida():
    pp, pc = pe.patrones_de_limpieza(MIO)
    proyectos, correos = _nombres(MIO)
    assert all(_like(pp, n) for n in proyectos)
    assert all(_like(pc, c) for c in correos)


def test_los_patrones_no_tocan_la_siembra_de_otra_corrida():
    pp, pc = pe.patrones_de_limpieza(MIO)
    proyectos, correos = _nombres(AJENO)
    assert not any(_like(pp, n) for n in proyectos)
    assert not any(_like(pc, c) for c in correos)


@pytest.mark.parametrize("malo", [None, "", "carga-e1", "%", "a1b2c3d", "A1B2C3D4", "a1b2c3d4%", "a1b2c3d4-x"])
def test_sin_sufijo_valido_no_hay_patron(malo):
    with pytest.raises(ValueError):
        pe.patrones_de_limpieza(malo)


def test_limpiar_sin_sufijo_no_se_conecta_ni_borra(monkeypatch):
    def prohibido():
        raise AssertionError("limpiar sin sufijo no debe ni conectarse")
    monkeypatch.setattr(pe, "_conectar", prohibido)
    for malo in (None, ""):
        with pytest.raises(ValueError):
            pe.limpiar(malo)


def test_sembrar_sin_sufijo_valido_no_escribe(monkeypatch):
    monkeypatch.setattr(pe, "_conectar", lambda: (_ for _ in ()).throw(AssertionError("no debe conectarse")))
    with pytest.raises(ValueError):
        pe.sembrar("")


# --- Escenario peor-caso (ola final, MAJOR-1) -------------------------------------------------
def test_base_de_peor_caso_es_propia_y_nunca_la_compartida_ni_produccion():
    assert pe.base_de_peor_caso(MIO) == f"jax_memory_test_{MIO}"
    for malo in (None, "", "jax_memory", "x", "A1B2C3D4", "a1b2c3d4; DROP"):
        with pytest.raises(ValueError):
            pe.base_de_peor_caso(malo)


def test_borrar_y_crear_base_rechazan_la_compartida_y_produccion(monkeypatch):
    monkeypatch.setattr(pe, "_cargar_env_de_prueba", lambda: (_ for _ in ()).throw(AssertionError("no debe conectarse")))
    for nombre in ("jax_memory_test", "jax_memory", "jax_memory_test_zz_control_sufijo", "otra"):
        with pytest.raises(RuntimeError):
            pe._borrar_base(nombre)
        with pytest.raises(RuntimeError):
            pe._crear_base(nombre)


def test_base_activa_solo_acepta_bases_de_prueba(monkeypatch):
    monkeypatch.delenv("PROYECTOS_E1_BASE", raising=False)
    assert pe._base_activa() == "jax_memory_test"
    monkeypatch.setenv("PROYECTOS_E1_BASE", f"jax_memory_test_{MIO}")
    assert pe._base_activa() == f"jax_memory_test_{MIO}"
    for malo in ("jax_memory", "jax_memory_test_zz", ""):
        monkeypatch.setenv("PROYECTOS_E1_BASE", malo)
        with pytest.raises(SystemExit):
            pe._base_activa()


@pytest.mark.parametrize("status,cuerpo,esperado", [
    (200, "", "ok"), (204, "", "ok"), (409, "{}", "4xx"), (404, "", "4xx"),
    (503, '{"detail":{"code":"reintentar"}}', "503_reintentar"), (503, "otra cosa", "5xx"),
    (500, "", "5xx"), (None, "", "sin_respuesta")])
def test_clasificar_respuesta(status, cuerpo, esperado):
    assert pe.clasificar_respuesta(status, cuerpo) == esperado


def test_clasificar_error_db_por_codigo_de_mariadb():
    assert pe.clasificar_error_db(Exception(1213, "Deadlock found")) == "deadlock_1213"
    assert pe.clasificar_error_db(Exception(1205, "Lock wait timeout")) == "lock_timeout_1205"
    assert pe.clasificar_error_db(Exception(1062, "dup")) == "otro"
    assert pe.clasificar_error_db(Exception("sin codigo")) == "otro"
    assert pe.clasificar_error_db(Exception()) == "otro"


def test_resumen_de_latencias():
    assert pe.resumen([])["n"] == 0 and pe.resumen([])["p95_ms"] is None
    r = pe.resumen([float(i) for i in range(1, 101)])
    assert (r["n"], r["p50_ms"], r["p95_ms"], r["p99_ms"], r["max_ms"]) == (100, 50.0, 95.0, 99.0, 100.0)


def test_veredicto_dice_degrada_por_p95_o_por_errores():
    sin = {"p95_ms": 10.0}
    assert pe.veredicto_peor_caso(sin, {"p95_ms": 20.0}, 0).startswith("no degrada")      # justo 2x: no
    assert pe.veredicto_peor_caso(sin, {"p95_ms": 20.1}, 0).startswith("DEGRADA")
    assert "errores" in pe.veredicto_peor_caso(sin, {"p95_ms": 10.0}, 3)
