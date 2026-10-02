"""Versión de Axioma: fuente única, el archivo VERSION en la raíz del repo.

El frontend lee el mismo archivo al compilar (frontend/vite.config.js). Sin
archivo, o con contenido que no sea X.Y[.Z], el arranque falla: una versión
inventada sería peor que ninguna.
"""
import re
from pathlib import Path

RUTA_VERSION = Path(__file__).resolve().parent.parent / "VERSION"
_FORMA = re.compile(r"^\d+\.\d+(\.\d+)?$")


def leer_version(ruta: Path = RUTA_VERSION) -> str:
    version = ruta.read_text(encoding="utf-8").strip()
    if not _FORMA.match(version):
        raise ValueError(f"{ruta}: se esperaba X.Y o X.Y.Z, hay {version!r}")
    return version
