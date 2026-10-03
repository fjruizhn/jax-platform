"""`cupo.Cupo` y los dos cupos que lo usan (subir y reprocesar): estado independiente. Sin DB."""
import pytest

from proyectos_documentos import cupo, cupo_de_reprocesos, cupo_de_subidas


def test_cupo_por_usuario_y_global():
    c = cupo.Cupo()
    assert c.tomar("a", por_usuario=1, globales=2)
    assert not c.tomar("a", por_usuario=1, globales=2)          # tope del usuario
    assert c.tomar("b", por_usuario=1, globales=2)
    assert not c.tomar("c", por_usuario=1, globales=2)          # tope global
    assert c.en_uso() == (2, {"a": 1, "b": 1})
    c.soltar("a")
    assert c.tomar("c", por_usuario=1, globales=2)
    with pytest.raises(RuntimeError):
        c.soltar("zzz")


def test_los_cupos_de_subir_y_de_reprocesar_no_comparten_estado():
    antes_s, antes_r = cupo_de_subidas.en_uso(), cupo_de_reprocesos.en_uso()
    assert cupo_de_reprocesos.tomar("u", por_usuario=1, globales=1)
    try:
        assert not cupo_de_reprocesos.tomar("u", por_usuario=1, globales=1)    # el de reprocesar esta lleno
        assert cupo_de_subidas.en_uso() == antes_s                              # el de subir ni se enteró
        assert cupo_de_subidas.tomar("u", por_usuario=1, globales=1)            # y tiene lugar
        cupo_de_subidas.soltar("u")
    finally:
        cupo_de_reprocesos.soltar("u")
    assert cupo_de_reprocesos.en_uso() == antes_r and cupo_de_subidas.en_uso() == antes_s


def test_soltar_sin_tomar_nombra_el_cupo():
    with pytest.raises(RuntimeError, match="reprocesos"):
        cupo_de_reprocesos.soltar("nadie")
    with pytest.raises(RuntimeError, match="subidas"):
        cupo_de_subidas.soltar("nadie")


def test_el_cupo_por_usuario_y_el_global_se_toman_y_se_sueltan_por_separado():
    c = cupo.Cupo()
    assert c.tomar_usuario("a", por_usuario=1)
    assert not c.tomar_usuario("a", por_usuario=1)                  # tope del usuario, sin tocar el global
    assert c.en_uso() == (0, {"a": 1})
    assert c.tomar_global(globales=1)
    assert not c.tomar_global(globales=1)                           # tope global
    assert c.en_uso() == (1, {"a": 1})
    c.soltar_global()
    c.soltar_usuario("a")
    assert c.en_uso() == (0, {})
    with pytest.raises(RuntimeError):
        c.soltar_global()
    with pytest.raises(RuntimeError):
        c.soltar_usuario("a")
