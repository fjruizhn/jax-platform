"""TRIPWIRE -- aiomysql y PyMySQL quedan fijados EXACTOS en requirements.txt.

Incidente (2026-09-17): `backend/requirements.txt` declaraba `aiomysql>=0.2`
sin fijar PyMySQL para nada -- PyMySQL es una dependencia TRANSITIVA de
aiomysql, invisible en este archivo. PyMySQL 1.2.1 (publicada 2026-09-17
08:32 UTC) quito `escape_dict` de `pymysql.converters`; aiomysql 0.3.2 lo
importa a nivel de modulo
(`aiomysql/__init__.py: from pymysql.converters import escape_dict, ...`),
asi que `import aiomysql` revienta con:

    ImportError: cannot import name 'escape_dict' from 'pymysql.converters'

en CUALQUIER entorno que instale hoy `pip install -r requirements.txt` --
rompio el job `jacobs-gobernanza-db` de jax (run 35202422331, que hace
`pip install -r /tmp/jax-platform/backend/requirements.txt`) y habria roto
el propio `pip install -r requirements.txt` de este repo (ver
`backend-tests-con-db` en .github/workflows/policy.yml, que instala
requirements.txt limpio en cada corrida). Produccion NO se cayo porque
`backend/.venv` ya tenia PyMySQL 1.2.0 instalado de antes -- un entorno
VIEJO no pisa una version rota que aparecio despues, pero un entorno NUEVO
si.

Reproducido a mano en un venv temporal (2026-09-17):
  - aiomysql==0.3.2 + PyMySQL==1.2.1 -> ImportError al importar aiomysql.
  - aiomysql==0.3.2 + PyMySQL==1.2.0 -> import limpio, escape_dict presente.

El pin no es "aiomysql>=0.2, PyMySQL<1.2.1" -- eso deja que una version 4.x
futura de aiomysql, que capaz ya no importa escape_dict, siga arrastrando un
PyMySQL viejo para siempre sin que nadie lo note. Se fijan los DOS con `==`
a las mismas versiones que corren en produccion HOY (verificado con
`pip show aiomysql PyMySQL` contra `backend/.venv`) y que corren en el repo
`jax` (mismo par, mismo motivo). Subir cualquiera de los dos es una decision
que se toma probando la combinacion, no un `pip install -U` de rutina --
por eso el mensaje de fallo de abajo lo dice explicito, y por eso el pin es
un test, no un comentario en requirements.txt (un comentario no falla un
`pip install -r requirements.txt` con una combinacion rota).

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# Mismas versiones que producción (backend/.venv, verificado 2026-09-17 con
# `pip show aiomysql PyMySQL`) y que el repo `jax` -- no es una version
# arbitraria "que anda", es la que YA corre.
AIOMYSQL_FIJADO = "0.3.2"
PYMYSQL_FIJADO = "1.2.0"


def _fuente_requirements() -> str:
    return (BACKEND / "requirements.txt").read_text(encoding="utf-8")


class RequirementsPinTest(unittest.TestCase):
    """Puro: lee requirements.txt como texto, no instala nada ni toca la
    red. Sin DB."""

    def test_aiomysql_fijado_exacto(self):
        fuente = _fuente_requirements()
        patron = re.compile(r"(?m)^aiomysql==([^\s#]+)\s*$")
        m = patron.search(fuente)
        self.assertIsNotNone(
            m,
            "requirements.txt tiene que fijar `aiomysql==" + AIOMYSQL_FIJADO
            + "` con `==` exacto -- un `>=` deja que un `pip install -r "
            "requirements.txt` nuevo instale una version de PyMySQL "
            "distinta a la que aiomysql espera y rompa la importacion "
            "(ver escape_dict, docstring de este archivo). Encontrado:\n"
            + fuente,
        )
        self.assertEqual(
            m.group(1), AIOMYSQL_FIJADO,
            f"aiomysql esta fijado a {m.group(1)!r}, no a "
            f"{AIOMYSQL_FIJADO!r} (produccion). Para cambiar esta version "
            "hay que probarla primero contra la version de PyMySQL fijada "
            "abajo -- no es un bump de rutina (ver el incidente de "
            "escape_dict en el docstring de este archivo).",
        )

    def test_pymysql_fijado_exacto(self):
        fuente = _fuente_requirements()
        patron = re.compile(r"(?mi)^PyMySQL==([^\s#]+)\s*$")
        m = patron.search(fuente)
        self.assertIsNotNone(
            m,
            "requirements.txt tiene que declarar `PyMySQL==" + PYMYSQL_FIJADO
            + "` EXPLICITO. PyMySQL es una dependencia TRANSITIVA de "
            "aiomysql -- sin fijarla acá, `pip install -r requirements.txt` "
            "puede traer cualquier version nueva de PyMySQL (como la 1.2.1, "
            "que quito `escape_dict` de `pymysql.converters` y rompe "
            "`import aiomysql`; ver el docstring de este archivo para el "
            "incidente completo). Encontrado:\n" + fuente,
        )
        self.assertEqual(
            m.group(1), PYMYSQL_FIJADO,
            f"PyMySQL esta fijado a {m.group(1)!r}, no a "
            f"{PYMYSQL_FIJADO!r} (produccion, y el mismo par que jax). "
            "Para cambiar esta version hay que probar la combinacion con "
            "aiomysql antes de subirla -- no es un bump de rutina.",
        )


class EscapeDictTripwireTest(unittest.TestCase):
    """Si aiomysql esta instalado en el entorno que corre la suite (lo esta
    en backend/.venv y en el runner de CI, que instala requirements.txt
    limpio), verifica el CONTRATO real que rompio el incidente: que
    `import aiomysql` no truene y que `pymysql.converters.escape_dict`
    exista. Es el freno que se ejercita -- un pin correcto en
    requirements.txt que nadie instalo todavia no prueba nada por si
    solo."""

    def test_import_aiomysql_no_truena_y_escape_dict_existe(self):
        try:
            import aiomysql  # noqa: F401
        except ImportError as exc:
            self.fail(
                "`import aiomysql` fallo en este entorno -- si es el "
                "ImportError de escape_dict, PyMySQL quedo en una version "
                "que no lo tiene (ver el pin de requirements.txt y el "
                f"docstring de este archivo). Error real: {exc!r}"
            )

        import pymysql.converters as conversores

        self.assertTrue(
            hasattr(conversores, "escape_dict"),
            "pymysql.converters ya no tiene escape_dict -- aiomysql 0.3.2 "
            "lo importa a nivel de modulo y esto tendria que haber "
            "reventado en el import de arriba; si este assert es lo unico "
            "que fallo, hay una version de aiomysql instalada que dejo de "
            "necesitarlo y el pin de PyMySQL podria estar mas viejo de lo "
            "necesario (revisar, no bajar el pin sin evidencia).",
        )


if __name__ == "__main__":
    unittest.main()
