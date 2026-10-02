"""La versión de Axioma sale del archivo VERSION de la raíz del repo (2026-10-02)."""
from pathlib import Path

import pytest

import app_version

RAIZ = Path(__file__).resolve().parents[2]


def test_app_version_sale_del_archivo():
    import main

    esperado = (RAIZ / "VERSION").read_text(encoding="utf-8").strip()
    assert esperado
    assert main.app.version == esperado


def test_leer_version_rechaza_contenido_invalido(tmp_path):
    f = tmp_path / "VERSION"
    f.write_text("dos punto cinco\n")
    with pytest.raises(ValueError):
        app_version.leer_version(f)


def test_leer_version_sin_archivo_falla(tmp_path):
    with pytest.raises(FileNotFoundError):
        app_version.leer_version(tmp_path / "no-existe")


def test_leer_version_ignora_salto_de_linea(tmp_path):
    f = tmp_path / "VERSION"
    f.write_text("3.0\n")
    assert app_version.leer_version(f) == "3.0"


def test_main_usa_la_version_del_archivo_aunque_no_sea_la_real(tmp_path):
    """Con un VERSION distinto del real, app.version tiene que ser ese: un
    version="2.5" escrito a mano en main.py (que hoy coincide) no pasa."""
    import os
    import subprocess
    import sys

    falso = tmp_path / "VERSION"
    falso.write_text("9.9.9\n")
    codigo = (
        "import pathlib, sys, app_version;"
        "app_version.RUTA_VERSION = pathlib.Path(sys.argv[1]);"
        "import main; print(main.app.version)"
    )
    r = subprocess.run(
        [sys.executable, "-c", codigo, str(falso)], cwd=RAIZ / "backend",
        env=os.environ.copy(), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().splitlines()[-1] == "9.9.9"


def test_endpoint_version_exige_token_y_devuelve_la_inyectada(tmp_path):
    """Sin Authorization: 401. Con un usuario autenticado (override de
    get_current_user, sin base): 200 y el VERSION inyectado (9.9.9), no un
    número escrito a mano. Sin lifespan."""
    import os
    import subprocess
    import sys

    falso = tmp_path / "VERSION"
    falso.write_text("9.9.9\n")
    codigo = (
        "import pathlib, sys, app_version;"
        "app_version.RUTA_VERSION = pathlib.Path(sys.argv[1]);"
        "import main; from fastapi.testclient import TestClient;"
        "from auth.middleware import get_current_user; from auth.models import AuthUser;"
        "c = TestClient(main.app);"
        "sin = c.get('/api/version').status_code;"
        "main.app.dependency_overrides[get_current_user] = lambda: AuthUser(user_id='1', tenant_id='1', role='operator');"
        "r = c.get('/api/version');"
        "print(sin, r.status_code, r.json()['version'])"
    )
    r = subprocess.run(
        [sys.executable, "-c", codigo, str(falso)], cwd=RAIZ / "backend",
        env=os.environ.copy(), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().splitlines()[-1] == "401 200 9.9.9"


def test_endpoint_version_real_con_y_sin_token(client):
    from tests.identidades import cabeceras

    esperado = (RAIZ / "VERSION").read_text(encoding="utf-8").strip()
    assert client.get("/api/version").status_code == 401
    assert client.get("/api/version", headers={"Authorization": "Bearer basura"}).status_code == 401
    r = client.get("/api/version", headers=cabeceras(client, "version-operador"))
    assert r.status_code == 200
    assert r.json() == {"version": esperado}
