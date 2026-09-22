"""Tests puros (sin red, sin DB, sin tocar /etc/jax/.env de verdad -- la
lectura de producción se monkeypatchea siempre) para
`descartados_orquestar.py`.

Mismo hallazgo de seguridad que `test_historial_orquestar.py` (jax-platform#146,
ronda 7): `construir_env()` tiene que generar su PROPIA llave de firma,
nunca la de producción, y `_abortar_si_el_secreto_de_carga_coincide_con_produccion`
es la barrera dura que revienta si por algún motivo coinciden. Las dos
funciones están duplicadas (no importadas) entre los dos orquestadores --
ver la nota de módulo de descartados_orquestar.py -- así que llevan su
propio test, no comparten el de historial.

`python3 -m pytest loadtest/test_descartados_orquestar.py -q` (no forma
parte de los tres pisos de CI de `backend/`; corre en el job `loadtest-tests`).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import descartados_orquestar as do  # noqa: E402


def test_construir_env_genera_su_propia_llave_distinta_de_produccion(tmp_path, monkeypatch):
    monkeypatch.setattr(
        do, "_cargar_env_produccion",
        lambda: {"JAX_JWT_SECRET": "secreto-de-produccion-fijo-de-prueba",
                 "JAX_DB_HOST": "127.0.0.1", "JAX_DB_PORT": "3308",
                 "JAX_DB_USER": "u", "JAX_DB_PASSWORD": "p"},
    )
    env = do.construir_env(tmp_path)
    assert env["JAX_JWT_SECRET"] != "secreto-de-produccion-fijo-de-prueba", (
        "construir_env() sigue usando el JAX_JWT_SECRET de produccion -- "
        "el backend de carga podria firmar tokens validos tambien contra "
        "produccion")
    assert len(env["JAX_JWT_SECRET"]) >= 32, "la llave generada es sospechosamente corta"


def test_construir_env_genera_una_llave_distinta_en_cada_llamada(tmp_path, monkeypatch):
    monkeypatch.setattr(do, "_cargar_env_produccion", lambda: {"JAX_JWT_SECRET": "produccion"})
    env1 = do.construir_env(tmp_path)
    env2 = do.construir_env(tmp_path)
    assert env1["JAX_JWT_SECRET"] != env2["JAX_JWT_SECRET"]


def test_construir_env_fija_jax_db_name_a_la_base_de_test(tmp_path, monkeypatch):
    monkeypatch.setattr(do, "_cargar_env_produccion", lambda: {"JAX_DB_NAME": "jax_memory"})
    env = do.construir_env(tmp_path)
    assert env["JAX_DB_NAME"] == do.BASE_DE_PRUEBA == "jax_memory_test"


def test_abortar_si_coincide_con_produccion_no_lanza_si_son_distintos():
    # No debe lanzar -- si lanza, el test falla solo.
    do._abortar_si_el_secreto_de_carga_coincide_con_produccion(
        "secreto-de-carga", "secreto-de-produccion")


def test_abortar_si_coincide_con_produccion_lanza_si_son_iguales():
    with pytest.raises(SystemExit):
        do._abortar_si_el_secreto_de_carga_coincide_con_produccion(
            "el-mismo-secreto", "el-mismo-secreto")


def test_abortar_si_el_secreto_de_carga_esta_vacio():
    with pytest.raises(SystemExit):
        do._abortar_si_el_secreto_de_carga_coincide_con_produccion("", "algo")


def test_verificar_no_apunta_a_produccion_revienta_si_jax_db_name_no_es_de_prueba():
    with pytest.raises(RuntimeError, match="jax_memory"):
        do._verificar_no_apunta_a_produccion({
            "JAX_DB_NAME": "jax_memory",
            "LAS_MANOS_URL": "http://127.0.0.1:17778",
            "JACOBS_URL": "http://127.0.0.1:17778/jacobs",
            "JAX_PLATFORM_URL": "http://127.0.0.1:18081",
        })


def test_verificar_no_apunta_a_produccion_revienta_si_una_url_pisa_un_puerto_real():
    with pytest.raises(RuntimeError, match="PRODUCCIÓN"):
        do._verificar_no_apunta_a_produccion({
            "JAX_DB_NAME": do.BASE_DE_PRUEBA,
            "LAS_MANOS_URL": "http://127.0.0.1:7777",
            "JACOBS_URL": "http://127.0.0.1:17778/jacobs",
            "JAX_PLATFORM_URL": "http://127.0.0.1:18081",
        })


def test_verificar_no_apunta_a_produccion_ok_con_el_entorno_esperado():
    # No debe lanzar.
    do._verificar_no_apunta_a_produccion({
        "JAX_DB_NAME": do.BASE_DE_PRUEBA,
        "LAS_MANOS_URL": do.FAKE_JACOBS_URL,
        "JACOBS_URL": f"{do.FAKE_JACOBS_URL}/jacobs",
        "JAX_PLATFORM_URL": do.BACKEND_URL,
    })
