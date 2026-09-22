"""Tests puros (sin red, sin DB, sin tocar /etc/jax/.env de verdad -- la
lectura de producción se monkeypatchea siempre) para
`historial_orquestar.py`.

Punto 7 (cierre jax-platform#146, ronda 7, SEGURIDAD, hallazgo
pre-existente): este script firmaba (y el backend de carga que levanta
verificaba) tokens con el `JAX_JWT_SECRET` de PRODUCCIÓN -- `construir_env()`
copiaba `/etc/jax/.env` entero y nunca lo sobrescribía, a diferencia de
`memoria_levantar_entorno.py::construir_env`, que ya generaba su propia
llave desde la ronda 5. Mismo arreglo acá.

`python3 -m pytest loadtest/test_historial_orquestar.py -q` (no forma
parte de los tres pisos de CI de `backend/`).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import historial_orquestar as ho  # noqa: E402


def test_construir_env_genera_su_propia_llave_distinta_de_produccion(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ho, "_cargar_env_produccion",
        lambda: {"JAX_JWT_SECRET": "secreto-de-produccion-fijo-de-prueba",
                 "JAX_DB_HOST": "127.0.0.1", "JAX_DB_PORT": "3308",
                 "JAX_DB_USER": "u", "JAX_DB_PASSWORD": "p"},
    )
    seed = {"big_pipeline_id": "pipeline-de-prueba", "user_id": 1}
    env = ho.construir_env(seed, tmp_path)
    assert env["JAX_JWT_SECRET"] != "secreto-de-produccion-fijo-de-prueba", (
        "construir_env() sigue usando el JAX_JWT_SECRET de produccion -- "
        "el backend de carga podria firmar tokens validos tambien contra "
        "produccion")
    assert len(env["JAX_JWT_SECRET"]) >= 32, "la llave generada es sospechosamente corta"


def test_construir_env_genera_una_llave_distinta_en_cada_llamada(tmp_path, monkeypatch):
    monkeypatch.setattr(ho, "_cargar_env_produccion", lambda: {"JAX_JWT_SECRET": "produccion"})
    seed = {"big_pipeline_id": "pipeline-de-prueba", "user_id": 1}
    env1 = ho.construir_env(seed, tmp_path)
    env2 = ho.construir_env(seed, tmp_path)
    assert env1["JAX_JWT_SECRET"] != env2["JAX_JWT_SECRET"]


def test_abortar_si_coincide_con_produccion_no_lanza_si_son_distintos():
    # No debe lanzar -- si lanza, el test falla solo.
    ho._abortar_si_el_secreto_de_carga_coincide_con_produccion(
        "secreto-de-carga", "secreto-de-produccion")


def test_abortar_si_coincide_con_produccion_lanza_si_son_iguales():
    with pytest.raises(SystemExit):
        ho._abortar_si_el_secreto_de_carga_coincide_con_produccion(
            "el-mismo-secreto", "el-mismo-secreto")


def test_abortar_si_el_secreto_de_carga_esta_vacio():
    # Un secreto de carga vacio (p.ej. porque /proc/<pid>/environ no lo
    # trae) es tan peligroso como que coincida -- el backend arrancaria sin
    # firmar nada valido, o el chequeo de arriba compararia contra "".
    with pytest.raises(SystemExit):
        ho._abortar_si_el_secreto_de_carga_coincide_con_produccion("", "algo")
