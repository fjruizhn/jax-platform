"""Fix wave final (2026-09-15, revision de 0b81ad7..618d83b de la rama
feat/pendientes-2026-09-22). Puros: no piden `client` ni DB.

- Item 2: la plataforma no corre DDL sobre tablas `jacobs_*`. Su dueño es
  jax (jacobs/store.py::init_tables); Ruling T6-6 saco de db/migrations.py el
  `ALTER TABLE jacobs_pipelines ADD INDEX`, y este escaneo impide que vuelva.
- Item 5: el 502 de /api/image/generate redacta ANTES de recortar y con la
  credencial conocida -- antes devolvia `e.response.text[:200]` y
  `str(e)[:200]` crudos.
"""
import ast
import asyncio
import re
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from api import image as image_mod
from api.image import ImageRequest
from auth.models import AuthUser

# --- Item 2: DDL sobre tablas de jax -------------------------------------------
MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations.py"

# Listas de esquema que run_migrations() recorre ejecutando su DDL: una tupla
# cuyo primer elemento sea una tabla `jacobs_*` es DDL sobre jax aunque la
# sentencia se arme con f-string en otro lado.
_LISTAS_DE_ESQUEMA = {"_TABLES", "_COLUMNS", "_ENUM_EXTENSIONS", "_COLUMN_WIDENS", "_INDEXES"}

_DDL_SOBRE_JACOBS = re.compile(r"""(?ix)
    \b(?:
        (?:create|alter|drop)\s+table\s+(?:if\s+(?:not\s+)?exists\s+)?`?jacobs_\w+
      | (?:create|drop)\s+(?:unique\s+|fulltext\s+|spatial\s+)?index\s+
        (?:if\s+(?:not\s+)?exists\s+)?`?\w+`?\s+on\s+`?jacobs_\w+
    )""")


def _textos_literales(arbol):
    """Solo literales de texto (y f-strings con `{}` en los huecos): un
    comentario que nombre el DDL viejo no es DDL y no cuenta."""
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            yield nodo.lineno, nodo.value
        elif isinstance(nodo, ast.JoinedStr):
            partes = [v.value if isinstance(v, ast.Constant) else "{}" for v in nodo.values]
            yield nodo.lineno, "".join(partes)


def violaciones_ddl_jacobs(fuente: str) -> list[str]:
    arbol = ast.parse(fuente)
    out = [f"linea {n}: {m.group(0)!r}"
           for n, texto in _textos_literales(arbol)
           for m in _DDL_SOBRE_JACOBS.finditer(texto)]
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Assign):
            destinos = nodo.targets
        elif isinstance(nodo, ast.AnnAssign):
            destinos = [nodo.target]
        else:
            continue
        if not any(isinstance(d, ast.Name) and d.id in _LISTAS_DE_ESQUEMA for d in destinos):
            continue
        for elt in getattr(nodo.value, "elts", []):
            primero = elt.elts[0] if isinstance(elt, ast.Tuple) and elt.elts else None
            if (isinstance(primero, ast.Constant) and isinstance(primero.value, str)
                    and primero.value.lower().startswith("jacobs_")):
                out.append(f"linea {elt.lineno}: {primero.value!r} en una lista de esquema")
    return sorted(set(out))


def test_migrations_no_corre_ddl_sobre_tablas_de_jacobs():
    fuente = MIGRATIONS.read_text()
    # Control: el escaneo lee el archivo real (tiene DDL propio de sobra).
    assert sum("CREATE TABLE" in t for _, t in _textos_literales(ast.parse(fuente))) > 5
    assert violaciones_ddl_jacobs(fuente) == [], (
        "db/migrations.py corre DDL sobre una tabla jacobs_* -- su dueño es "
        "jax/jacobs/store.py (Ruling T6-6)")


@pytest.mark.parametrize("fuente", [
    'cur.execute("ALTER TABLE jacobs_pipelines ADD INDEX idx_x (user_id)")',
    'x = "CREATE INDEX idx_x ON jacobs_steps (status)"',
    'x = "create unique index idx_x on `jacobs_events` (id)"',
    'x = "CREATE TABLE IF NOT EXISTS jacobs_events (id INT)"',
    'x = "DROP TABLE IF EXISTS jacobs_pipelines"',
    'x = "DROP INDEX idx_x ON jacobs_pipelines"',
    'x = f"ALTER TABLE jacobs_pipelines ADD INDEX {nombre} (a)"',
    'x = ("ALTER TABLE "\n     "jacobs_pipelines ADD INDEX i (a)")',
    '_INDEXES = [("jacobs_pipelines", "idx_x", "ALTER TABLE t ADD INDEX idx_x (a)")]',
    '_COLUMNS: list = [("jacobs_steps", "c", "ALTER TABLE t ADD COLUMN c INT")]',
])
def test_el_escaneo_detecta_ddl_sobre_jacobs(fuente):
    assert violaciones_ddl_jacobs(fuente)


@pytest.mark.parametrize("fuente", [
    'x = "SELECT * FROM jacobs_pipelines WHERE user_id = %s"',
    'x = "INSERT INTO jacobs_events VALUES (1)"',
    '# ALTER TABLE jacobs_pipelines ADD INDEX idx_x (a) -- comentario, no DDL\nx = 1',
    'x = "ALTER TABLE axioma_usage ADD INDEX idx_x (created_at)"',
    '_INDEXES = [("jax_users", "idx_x", "ALTER TABLE jax_users ADD INDEX idx_x (a)")]',
])
def test_el_escaneo_no_marca_lo_que_no_es_ddl_sobre_jacobs(fuente):
    assert violaciones_ddl_jacobs(fuente) == []


# --- Item 5: el 502 de imagen -----------------------------------------------------
KEY = "AIzaFAKE-task6-0123456789abcdef"
# Sin forma reconocible por ninguna regla: solo la tapa pasarla como secreto
# conocido. Es la credencial que image.py resolvio para este pedido.
CRED = "sk-FAKE-imagen-0123456789abcdef"


class _ClienteQueFalla:
    def __init__(self, exc):
        self.exc = exc

    async def post(self, url, **kwargs):
        raise self.exc


def _generar(monkeypatch, exc) -> HTTPException:
    async def credencial(provider_id):
        return CRED

    async def cliente():
        return _ClienteQueFalla(exc)

    monkeypatch.setattr(image_mod, "resolve_credential_instrumented", credencial)
    monkeypatch.setattr(image_mod, "get_http_client", cliente)
    user = AuthUser(user_id="5", tenant_id="1", role="operator")

    async def correr():
        try:
            await image_mod.generate_image(ImageRequest(prompt="un gato"), user=user)
        except HTTPException as e:
            return e
        return None

    error = asyncio.run(correr())
    assert error is not None and error.status_code == 502
    return error


def _status_error(cuerpo: str) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
    return httpx.HTTPStatusError(
        "x", request=req, response=httpx.Response(400, text=cuerpo, request=req))


def test_el_502_de_imagen_no_deja_un_pedazo_de_key_AIza_que_cruza_el_corte(monkeypatch):
    cuerpo = "e" * 190 + " " + KEY + " fin"          # la key empieza en el 191
    assert KEY[:9] in cuerpo[:200]                    # control: el corte cae dentro
    detail = _generar(monkeypatch, _status_error(cuerpo)).detail
    assert detail.startswith("Image API error 400: ")
    assert "AIza" not in detail
    assert len(detail) <= len("Image API error 400: ") + 200


def test_el_502_de_imagen_tapa_la_credencial_conocida_que_cruza_el_corte(monkeypatch):
    cuerpo = "e" * 190 + " " + CRED
    assert CRED[:9] in cuerpo[:200]
    detail = _generar(monkeypatch, _status_error(cuerpo)).detail
    assert "sk-FAKE" not in detail
    assert "***" in detail


def test_el_error_generico_de_imagen_redacta_antes_de_recortar(monkeypatch):
    mensaje = "x" * 190 + " " + CRED + " fin"
    detail = _generar(monkeypatch, RuntimeError(mensaje)).detail
    assert detail.startswith("Error generando imagen: ")
    assert "sk-FAKE" not in detail
    assert len(detail) <= len("Error generando imagen: ") + 200
