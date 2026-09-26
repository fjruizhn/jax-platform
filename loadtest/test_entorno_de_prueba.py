"""`loadtest/entorno_de_prueba.py` (PASO 0, 2026-09-25): el cargador de
credenciales de los scripts de `loadtest/` nunca corre `sudo` ni abre nada
bajo `/etc/jax/`. Mismo criterio y misma forma que
`backend/tests/test_conftest_sin_produccion.py`, pero para este directorio.

`python3 -m pytest loadtest/test_entorno_de_prueba.py -q` (no forma parte de
los tres pisos de CI de `backend/`; corre en el job `loadtest-tests`)."""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import entorno_de_prueba as E  # noqa: E402

SECRETOS_DE_SESION = ("JAX_JWT_SECRET", "FERNET_KEY", "TELEGRAM_BOT_TOKEN")


def _fuente() -> str:
    return Path(__file__).with_name("entorno_de_prueba.py").read_text(encoding="utf-8")


def test_ninguna_llamada_real_invoca_subprocess():
    """AST, no texto: el módulo no tiene ningún `Call` a `subprocess`/`os.system`."""
    arbol = ast.parse(_fuente(), filename="entorno_de_prueba.py")
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call):
            nombre = getattr(nodo.func, "id", None) or getattr(nodo.func, "attr", None)
            assert nombre not in ("run", "Popen", "call", "check_call", "check_output", "system")


def test_la_lista_blanca_no_incluye_secretos_de_sesion():
    for prohibida in SECRETOS_DE_SESION:
        assert prohibida not in E.CLAVES_DB


def test_credenciales_de_base_de_prueba_lee_el_entorno_del_proceso(monkeypatch):
    for k in E.CLAVES_DB:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JAX_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("JAX_DB_PORT", "3308")
    monkeypatch.setenv("JAX_DB_USER", "jax_test")
    monkeypatch.setenv("JAX_DB_PASSWORD", "x")
    assert E.credenciales_de_base_de_prueba() == {
        "JAX_DB_HOST": "127.0.0.1", "JAX_DB_PORT": "3308",
        "JAX_DB_USER": "jax_test", "JAX_DB_PASSWORD": "x",
    }


def test_credenciales_de_base_de_prueba_falla_cerrado_si_falta_una(monkeypatch):
    for k in E.CLAVES_DB:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JAX_DB_HOST", "127.0.0.1")
    with pytest.raises(E.CredencialDeCargaFaltante, match="JAX_DB_PORT"):
        E.credenciales_de_base_de_prueba()


def test_secreto_de_produccion_para_comparar_ausente_da_none(monkeypatch):
    monkeypatch.delenv("JAX_JWT_SECRET_DE_PRODUCCION", raising=False)
    assert E.secreto_de_produccion_para_comparar("JAX_JWT_SECRET_DE_PRODUCCION") is None


def test_secreto_de_produccion_para_comparar_presente_lo_devuelve(monkeypatch):
    monkeypatch.setenv("JAX_JWT_SECRET_DE_PRODUCCION", "un-secreto")
    assert E.secreto_de_produccion_para_comparar("JAX_JWT_SECRET_DE_PRODUCCION") == "un-secreto"
