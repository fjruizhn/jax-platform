"""La plataforma no arranca sin saber dónde está el freno (plan
2026-09-16-frente-b-kill-switch, Task 2). Puro: importa en un subproceso, no
arranca la app ni toca la base."""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _importar_main(entorno):
    return subprocess.run(
        [sys.executable, "-c", "import main"], cwd=BACKEND, env=entorno,
        capture_output=True, text=True, timeout=120)


def test_la_suite_no_mira_el_freno_de_produccion():
    import interruptor
    assert not str(interruptor.ruta_del_interruptor()).startswith("/etc/jax")


def test_sin_la_variable_la_plataforma_no_arranca():
    entorno = {k: v for k, v in os.environ.items() if k != "JAX_KILL_SWITCH_PATH"}
    r = _importar_main(entorno)
    assert r.returncode != 0
    assert "JAX_KILL_SWITCH_PATH" in r.stderr


def test_con_la_variable_el_import_pasa():
    r = _importar_main(os.environ.copy())
    assert r.returncode == 0, r.stderr[-2000:]
