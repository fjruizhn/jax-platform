"""Frente A (2026-09-16).
A-42: el backend no guarda hex de facetas; /api/facets sigue leyendo
facet.color_hex (fuente de verdad, Bloque C).
A-48: el store solo carga /api/state y ahi no habia display_name: los labels
caian al nombre crudo. Ahora /api/state lo trae de la tabla `facet`, leida UNA
vez al arrancar. INVALIDACION: reinicio del proceso -- el unico escritor de
facet.display_name son las migraciones, que corren en el arranque (lo fija
test_nadie_escribe_la_tabla_facet_en_runtime)."""
import re
from pathlib import Path

from jax_engine import state as state_mod
from jax_engine.schemas import FacetState
from tests.identidades import cabeceras, sql

BACKEND = Path(__file__).resolve().parent.parent


def test_facet_state_no_tiene_color():
    assert "color" not in FacetState(name="x").model_dump()


def test_no_hay_hex_de_facetas_en_jax_engine():
    for rel in ("jax_engine/state.py", "jax_engine/schemas.py"):
        assert not re.search(r"#[0-9a-fA-F]{6}\b", (BACKEND / rel).read_text(encoding="utf-8")), rel


class _Cursor:
    def __init__(self, filas):
        self.filas, self.consultas = filas, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, consulta, args=()):
        self.consultas.append(consulta)

    async def fetchall(self):
        return self.filas


class _Conexion:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def cursor(self):
        return self._cursor


class _Pool:
    def __init__(self, cursor):
        self._cursor = cursor

    def acquire(self):
        return _Conexion(self._cursor)


async def test_cargar_nombres_pone_el_display_name_de_la_tabla(monkeypatch):
    cursor = _Cursor([("hipatia", "Hipatia"), ("jekyll", "Dr. Jekyll")])

    async def pool():
        return _Pool(cursor)

    monkeypatch.setattr(state_mod, "get_pool", pool)
    estado = state_mod.JAXEngineState()
    await estado.cargar_nombres_de_facetas()
    facetas = estado.get_state().facets
    assert (facetas["hipatia"].display_name, facetas["jekyll"].display_name) == ("Hipatia", "Dr. Jekyll")
    assert facetas["jacobs"].display_name is None
    assert cursor.consultas == ["SELECT `key`, display_name FROM facet"]


def test_nadie_escribe_la_tabla_facet_en_runtime():
    escritura = re.compile(r"(?i)\b(UPDATE|INSERT\s+(IGNORE\s+)?INTO|DELETE\s+FROM|REPLACE\s+INTO)\s+`?facet`?[\s(]")
    hallazgos = []
    for ruta in BACKEND.rglob("*.py"):
        rel = ruta.relative_to(BACKEND).as_posix()
        if rel.startswith((".venv/", "tests/")) or rel == "db/migrations.py":
            continue
        if escritura.search(ruta.read_text(encoding="utf-8")):
            hallazgos.append(rel)
    assert hallazgos == []


def test_state_y_facets_sirven_los_datos_de_la_tabla(client):
    filas = client.portal.call(sql, "SELECT `key`, display_name, color_hex FROM facet", (), True)
    h = cabeceras(client, "facetas-nombres", "operator")
    estado = client.get("/api/state", headers=h).json()["facets"]
    facetas = client.get("/api/facets", headers=h).json()["facets"]
    for clave, nombre, color in filas:
        if clave in estado:
            assert estado[clave]["display_name"] == nombre
            assert "color" not in estado[clave]
        if clave in facetas:
            assert facetas[clave]["color"] == color
