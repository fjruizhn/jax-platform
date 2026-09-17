"""Frente A (2026-09-16).

A-55: AUDIT_LOG, MISSIONS_DIR, JAX_BIN, REPO_BASE y el sys.path de ~/jax
estaban atados a $HOME sin variable. JAX_REPO_PATH y JAX_CONFIG_PATH tenian
default ~/jax (produccion corria con el default: no estan en /etc/jax/.env,
inventario 2026-08-09). Ahora las seis son obligatorias: sin la variable el
modulo no se importa y el servicio no arranca (fail-closed).
A-41: el audit se lee desde el FINAL del archivo (sin cargarlo entero), filtrando
vacias ANTES de contar las 20, en un hilo.
A-54: email y tenant de la semilla desde el entorno; si faltan y hay que
sembrar, error explicito. Puros salvo el ultimo (pide client)."""
import ast
import asyncio
import json
import os
import re
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


# Ronda final M10 (2026-09-16): el escaneo de arriba busca TEXTO y no ve un
# default nuevo del estilo os.getenv("JAX_X", "/home/fruiz/jax"). Este va por
# AST sobre los defaults de os.getenv / os.environ.get / environ.setdefault.
_LECTORES_DE_ENTORNO = {"getenv", "get", "setdefault"}


def _es_lectura_de_entorno(llamada: ast.Call) -> bool:
    f = llamada.func
    if isinstance(f, ast.Name):
        return f.id == "getenv"
    if not isinstance(f, ast.Attribute) or f.attr not in _LECTORES_DE_ENTORNO:
        return False
    if f.attr == "getenv":
        return True
    duenio = f.value  # os.environ.get / environ.get
    return (isinstance(duenio, ast.Attribute) and duenio.attr == "environ") or (
        isinstance(duenio, ast.Name) and duenio.id == "environ")


def _defaults_de_home(fuente: str) -> list[str]:
    hallazgos = []
    for nodo in ast.walk(ast.parse(fuente)):
        if not (isinstance(nodo, ast.Call) and _es_lectura_de_entorno(nodo)):
            continue
        default = nodo.args[1] if len(nodo.args) > 1 else next(
            (k.value for k in nodo.keywords if k.arg == "default"), None)
        if default is None:
            continue
        texto = ast.unparse(default)
        if re.search(r"(^['\"]~|/home/|['\"]/root(/|['\"])|HOME|\.home\(|expanduser)", texto):
            hallazgos.append(f"{nodo.lineno}: {texto}")
    return hallazgos


@pytest.mark.parametrize("fuente", [
    'import os\nX = os.getenv("JAX_X", "/home/fruiz/jax")',
    'import os\nX = os.environ.get("JAX_X", "~/jax")',
    'from os import environ\nX = environ.get("JAX_X", default="/root/jax")',
    'from os import getenv\nX = getenv("JAX_X", f"{os.environ[\'HOME\']}/jax")',
    'import os\nos.environ.setdefault("JAX_X", str(Path.home() / "jax"))',
])
def test_el_escaneo_de_defaults_ve_una_ruta_de_home(fuente):
    assert _defaults_de_home(fuente)


@pytest.mark.parametrize("fuente", [
    'import os\nX = os.getenv("JAX_X", "")',
    'import os\nX = os.getenv("JAX_FACET_SEAL_PATH", "/srv/jax-data/facet-cache-seal")',
    'import os\nX = os.environ.get("JAX_X")',
    'datos = {"a": 1}\nX = datos.get("a", "/home/no-es-entorno")',
])
def test_el_escaneo_de_defaults_no_marca_lo_que_no_es_home(fuente):
    assert _defaults_de_home(fuente) == []


def test_ningun_default_de_variable_de_entorno_apunta_a_home():
    hallazgos = []
    for ruta in BACKEND.rglob("*.py"):
        rel = ruta.relative_to(BACKEND).as_posix()
        if rel.startswith((".venv/", "tests/")):
            continue
        hallazgos += [f"{rel}:{h}" for h in _defaults_de_home(ruta.read_text(encoding="utf-8"))]
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
