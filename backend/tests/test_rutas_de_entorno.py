"""Frente A (2026-09-16).

A-55: AUDIT_LOG, MISSIONS_DIR, JAX_BIN, REPO_BASE y el sys.path de ~/jax
estaban atados a $HOME sin variable. JAX_REPO_PATH y JAX_CONFIG_PATH tenian
default ~/jax (produccion corria con el default: no estan en /etc/jax/.env,
inventario 2026-08-09). Ahora las seis son obligatorias: sin la variable el
modulo no se importa y el servicio no arranca (fail-closed).
A-41: el audit se lee con deque(maxlen=20), filtrando vacias ANTES, en un hilo.
A-54: email y tenant de la semilla desde el entorno; si faltan y hay que
sembrar, error explicito. Puros salvo el ultimo (pide client)."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import api.audit as audit_mod
from auth.models import AuthUser
from config_de_entorno import ruta_requerida

BACKEND = Path(__file__).resolve().parent.parent
SUPER = AuthUser(user_id="1", tenant_id="1", role="superadmin")


def test_ruta_requerida_sin_variable_es_error_con_el_nombre(monkeypatch):
    monkeypatch.delenv("JAX_PRUEBA_RUTA", raising=False)
    with pytest.raises(RuntimeError, match="JAX_PRUEBA_RUTA"):
        ruta_requerida("JAX_PRUEBA_RUTA")


def test_ruta_requerida_relativa_es_error(monkeypatch):
    monkeypatch.setenv("JAX_PRUEBA_RUTA", "jax/repo")
    with pytest.raises(RuntimeError, match="absoluta"):
        ruta_requerida("JAX_PRUEBA_RUTA")


def test_ruta_requerida_absoluta_se_devuelve(monkeypatch, tmp_path):
    monkeypatch.setenv("JAX_PRUEBA_RUTA", str(tmp_path))
    assert ruta_requerida("JAX_PRUEBA_RUTA") == tmp_path


@pytest.mark.parametrize("modulo, variable", [
    ("api.audit", "JAX_AUDIT_LOG_PATH"),
    ("api.command", "JAX_MISSIONS_DIR"),
    ("api.command", "JAX_BIN"),
    ("api.admin.repository", "JAX_REPO_BASE"),
    ("governance_context", "JAX_REPO_PATH"),
    ("api.chat", "JAX_CONFIG_PATH"),
])
def test_sin_la_variable_el_modulo_no_se_importa(modulo, variable):
    entorno = {k: v for k, v in os.environ.items() if k != variable}
    r = subprocess.run([sys.executable, "-c", f"import {modulo}"], cwd=BACKEND, env=entorno,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0
    assert variable in r.stderr, r.stderr[-2000:]


# Fuera, con motivo: model_catalog.py (~/.claude/.credentials.json, ubicacion
# que define Claude Code) y el cwd del subproceso jax en api/command.py (el
# directorio de trabajo de la CLI, no una ruta de datos).
PERMITIDOS = {("model_catalog.py", "expanduser"), ("api/command.py", "Path.home()")}


def test_ningun_modulo_de_produccion_arma_rutas_desde_home():
    hallazgos = []
    for ruta in BACKEND.rglob("*.py"):
        rel = ruta.relative_to(BACKEND).as_posix()
        if rel.startswith((".venv/", "tests/")):
            continue
        texto = ruta.read_text(encoding="utf-8")
        for patron in ("expanduser", "Path.home()"):
            if patron in texto and (rel, patron) not in PERMITIDOS:
                hallazgos.append(f"{rel}: {patron}")
    assert hallazgos == []


def _leer(monkeypatch, ruta):
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", ruta)
    return asyncio.run(audit_mod.get_audit(user=SUPER))


def test_audit_con_lineas_vacias_devuelve_las_ultimas_20_con_contenido(monkeypatch, tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text("".join(json.dumps({"event": f"E{i}"}) + "\n\n   \n" for i in range(30)))
    eventos = _leer(monkeypatch, ruta)["events"]
    assert [e["event"] for e in eventos] == [f"E{i}" for i in range(29, 9, -1)]


def test_audit_no_carga_el_archivo_entero_y_lee_en_un_hilo(monkeypatch, tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text(json.dumps({"event": "E"}) + "\n")

    def entero(*a, **k):
        raise AssertionError("read_text carga el log entero en memoria")

    en_hilo = []
    real = asyncio.to_thread

    async def espia(funcion, *a, **k):
        en_hilo.append(funcion)
        return await real(funcion, *a, **k)

    monkeypatch.setattr(Path, "read_text", entero)
    monkeypatch.setattr(asyncio, "to_thread", espia)
    assert _leer(monkeypatch, ruta) == {"events": [{"event": "E"}]}
    assert en_hilo, "la lectura del audit no pasó por asyncio.to_thread"


def test_la_semilla_sin_email_es_error_explicito(monkeypatch):
    from db import seed
    monkeypatch.delenv("JAX_SEED_SUPERADMIN_EMAIL", raising=False)
    with pytest.raises(RuntimeError, match="JAX_SEED_SUPERADMIN_EMAIL"):
        seed.email_de_semilla()


def test_la_semilla_sin_tenant_es_error_explicito(monkeypatch):
    from db import seed
    monkeypatch.delenv("JAX_SEED_TENANT_NAME", raising=False)
    with pytest.raises(RuntimeError, match="JAX_SEED_TENANT_NAME"):
        seed.tenant_de_semilla()


def test_la_semilla_no_trae_datos_de_personas_en_el_codigo():
    fuente = (BACKEND / "db/seed.py").read_text(encoding="utf-8")
    assert "rich-hn" not in fuente
    assert "Diamante" not in fuente


def test_sin_variables_la_semilla_no_falla_si_no_hay_que_sembrar(client, monkeypatch):
    """user_id=1 y tenant 1 ya existen en jax_memory_test: no hace falta sembrar."""
    from db.seed import run_seed
    monkeypatch.delenv("JAX_SEED_SUPERADMIN_EMAIL", raising=False)
    monkeypatch.delenv("JAX_SEED_TENANT_NAME", raising=False)
    client.portal.call(run_seed)
