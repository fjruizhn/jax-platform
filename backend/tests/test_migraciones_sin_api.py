"""Las migraciones no importan la capa de API.

`db/migrations.py` importaba `api.admin.config_admin` para una semilla; ese módulo arrastra
`auth/jwt`, que exige `JAX_JWT_SECRET`. El job de CI de jax que sólo construye el esquema murió
con `RuntimeError: JAX_JWT_SECRET no configurada` antes de crear una sola tabla (2026-09-18).
Construir el esquema no necesita autenticación, ni rutas, ni FastAPI.
"""
import ast
from pathlib import Path

MIGRACIONES = Path(__file__).resolve().parents[1] / "db" / "migrations.py"
PROHIBIDOS = ("api", "main", "auth")


def _modulos_importados(ruta: Path) -> set[str]:
    arbol = ast.parse(ruta.read_text())
    nombres = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.ImportFrom) and n.module:
            nombres.add(n.module)
        elif isinstance(n, ast.Import):
            nombres.update(a.name for a in n.names)
    return nombres


def test_las_migraciones_no_importan_la_api():
    culpables = [m for m in _modulos_importados(MIGRACIONES)
                 if m.split(".")[0] in PROHIBIDOS]
    assert culpables == [], f"las migraciones importan la API: {culpables}"


def test_el_control_ve_la_forma_prohibida(tmp_path):
    falso = tmp_path / "m.py"
    falso.write_text("from api.admin.config_admin import DEFAULT_CONFIG\n")
    assert [m for m in _modulos_importados(falso) if m.split(".")[0] in PROHIBIDOS]
