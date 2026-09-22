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
import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import memoria_medir_fundir as mmf  # noqa: E402
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


class _RespuestaFalsa:
    """Imita lo poco de httpx.Response que main_async usa: .status_code y
    .json()."""
    def __init__(self, status_code, datos):
        self.status_code = status_code
        self._datos = datos

    def json(self):
        return self._datos


class _ClienteAsyncFalso:
    """Imita httpx.AsyncClient como context manager async -- devuelve
    _RespuestaFalsa sin tocar la red. GET /grupos trae `grupos_resp`
    (cerrado sobre la variable del test); POST /fundir siempre da 200."""
    def __init__(self, grupos_resp):
        self._grupos_resp = grupos_resp

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        assert url.endswith("/api/admin/memoria/grupos")
        return _RespuestaFalsa(200, self._grupos_resp)

    async def post(self, url, json=None, headers=None):
        assert url.endswith("/api/admin/memoria/hechos/fundir")
        return _RespuestaFalsa(200, {"superados": 1})


class _CursorFalso:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return (0, "superadmin")


class _ConexionFalsa:
    def cursor(self):
        return _CursorFalso()

    def close(self):
        pass


def test_main_async_llama_a_armar_resultado_con_los_disponibles_y_los_intentados_por_separado(
        tmp_path, monkeypatch):
    """m5 (cierre jax-platform#146), MINOR (revisión adversarial, ronda 7):
    los dos tests de arriba prueban `armar_resultado()` AISLADA, con listas
    fabricadas a mano -- no hubieran detectado un error de CABLEADO en el
    sitio real de la llamada (`memoria_medir_fundir.py::main_async`, línea
    ~155): pasar `clusters` (la lista TRUNCADA) en el lugar de
    `clusters_disponibles` también. Este test corre `main_async()` de
    punta a punta, mockeando SÓLO los bordes de I/O (subprocess, DB, red,
    `info.json`) -- nunca `armar_resultado` ni el truncado -- y lee el JSON
    escrito de verdad."""
    # 30 clusters "disponibles" en /grupos; n_max=10 -> sólo se intentan 10.
    n_disponibles, n_max = 30, 10
    grupos_resp = {
        "grupos": [
            {"casi_duplicados": [{"ids": [i, i + 1], "superviviente_id": i}]}
            for i in range(0, n_disponibles * 2, 2)
        ]
    }

    run_dir = tmp_path / "_run"
    run_dir.mkdir()
    (run_dir / "info.json").write_text(json.dumps({"pid": 999999}))
    monkeypatch.setattr(mmf, "RUN_DIR", run_dir)
    monkeypatch.setattr(mmf, "LOADTEST_DIR", tmp_path)
    monkeypatch.setattr(mmf, "leer_environ_de_proceso",
                         lambda pid: {"JAX_JWT_SECRET": "secreto-de-carga-nunca-el-de-produccion"})

    def _run_falso(cmd, capture_output, text, check):
        assert cmd[:3] == ["sudo", "-n", "cat"]
        salida = ("JAX_DB_HOST=127.0.0.1\nJAX_DB_PORT=18080\nJAX_DB_USER=u\n"
                   "JAX_DB_PASSWORD=p\nJAX_JWT_SECRET=secreto-de-produccion-nunca-igual-al-de-carga\n")
        return subprocess.CompletedProcess(cmd, 0, stdout=salida, stderr="")

    monkeypatch.setattr(subprocess, "run", _run_falso)

    import pymysql
    monkeypatch.setattr(pymysql, "connect", lambda **kwargs: _ConexionFalsa())
    monkeypatch.setattr(mmf.httpx, "AsyncClient", _ClienteAsyncFalso(grupos_resp))

    asyncio.run(mmf.main_async("base_de_prueba_falsa", "http://127.0.0.1:18080", n_max))

    resultado = json.loads((tmp_path / "_memoria_resultados_fundir.json").read_text())
    assert resultado["n_clusters_disponibles"] == n_disponibles, (
        "el sitio real de la llamada no está pasando la lista COMPLETA de "
        "clusters -- ver memoria_medir_fundir.py::main_async")
    assert resultado["n_intentados"] == n_max
