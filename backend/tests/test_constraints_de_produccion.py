"""constraints.txt fija TODA dependencia directa a la versión de producción.

2026-09-23: Starlette 1.7.0 salió ese día, CI la instaló por el rango
`fastapi>=0.115` y rompió un test en cualquier PR, mientras producción seguía
en 1.3.1. constraints.txt congela el venv de producción; este test impide que
una dependencia nueva de requirements.txt quede sin fijar (volvería a flotar
en CI) y que un pin exacto de requirements.txt contradiga al de producción.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _nombre(linea: str) -> str:
    """PEP 503: minúsculas y `-_.` equivalentes; sin extras ni especificador."""
    nombre = re.split(r"[\[<>=!~;\s]", linea, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", nombre).lower()


def _lineas(archivo: str) -> list[str]:
    texto = (BACKEND / archivo).read_text(encoding="utf-8")
    return [l.strip() for l in texto.splitlines() if l.strip() and not l.strip().startswith("#")]


def _pines(archivo: str) -> dict[str, str]:
    pines = {}
    for linea in _lineas(archivo):
        if "==" in linea:
            pines[_nombre(linea)] = linea.split("==", 1)[1].split(";")[0].strip()
    return pines


def test_toda_dependencia_directa_esta_fijada_a_la_version_de_produccion():
    fijadas = _pines("constraints.txt")
    sin_fijar = [l for l in _lineas("requirements.txt") if _nombre(l) not in fijadas]
    assert sin_fijar == [], (
        f"dependencias de requirements.txt sin versión de producción en constraints.txt: {sin_fijar}. "
        "Instalalas en el venv de producción (vía un PR probado en CI) y volvé a congelar.")


def test_los_pines_exactos_de_requirements_coinciden_con_produccion():
    produccion = _pines("constraints.txt")
    distintos = {n: (v, produccion.get(n)) for n, v in _pines("requirements.txt").items()
                 if produccion.get(n) != v}
    assert distintos == {}, f"requirements.txt fija versiones distintas a producción: {distintos}"


def test_constraints_solo_tiene_pines_exactos():
    # Un rango acá no restringe nada: dejaría flotar justo lo que el archivo
    # existe para fijar.
    no_exactas = [l for l in _lineas("constraints.txt") if "==" not in l]
    assert no_exactas == []
