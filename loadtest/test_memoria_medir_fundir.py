"""Tests puros (sin red, sin DB, sin tocar producción) para
`memoria_medir_fundir.py::armar_resultado`.

m5 (cierre jax-platform#146, ronda 6): `n_clusters_disponibles` se calculaba
DESPUÉS de truncar la lista de clusters a `n_max` -- con 757 clusters reales
disponibles y `n_max=200` (el default), el JSON de resultado decía "200
disponibles", no 757 (reproducido en vivo, ronda 5:
`_resultados_r5_peor_caso_fundir.json`, campo `n_clusters_disponibles: 200`).

Este arnés no requiere pytest.ini/conftest propios de `loadtest/` -- se
corre apuntándolo directo: `python3 -m pytest loadtest/test_memoria_medir_
fundir.py -q` (no forma parte de los tres pisos de CI de `backend/`, que
sólo cuentan `backend/tests/`).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from memoria_medir_fundir import armar_resultado  # noqa: E402


def test_n_clusters_disponibles_es_el_total_antes_de_truncar():
    # 757 clusters reales devueltos por /grupos, de los cuales sólo se
    # intentan fundir 200 (n_max) -- el mismo escenario que destapó el
    # hallazgo (ver docstring del módulo).
    disponibles = [{"ids": [i, i + 1], "superviviente_id": i} for i in range(757)]
    intentados = disponibles[:200]
    r = armar_resultado("jax_memory_test_fundir146_r5peor", disponibles, intentados, [], 0, {"200": 200})
    assert r["n_clusters_disponibles"] == 757
    assert r["n_intentados"] == 200


def test_n_intentados_es_lo_que_de_verdad_se_intento_no_lo_disponible():
    disponibles = [{"ids": [i, i + 1], "superviviente_id": i} for i in range(30)]
    intentados = disponibles[:10]
    r = armar_resultado("base", disponibles, intentados, [1.0, 2.0], 0, {"200": 10})
    assert r["n_intentados"] == 10
    assert r["n_clusters_disponibles"] == 30
    assert r["ok"] == 2
    assert r["n_muestras"] == 2
