"""Tipos de documento que acepta la subida de un proyecto (E2a, T5).

Son exactamente los que el extractor real de jax acepta
(`procesamiento/compuerta.py`: IMAGENES, EXCEL, WORD, mas pdf); una prueba
compara ambos conjuntos para que no se desalineen. Esta es la UNICA lista de la
plataforma: la API la valida y el frontend la recibe por
`GET /api/proyectos/documentos/limites`, sin copiarla.
"""
from __future__ import annotations

EXTENSIONES_ACEPTADAS: frozenset[str] = frozenset(
    {"pdf", "xlsx", "xlsm", "docx", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"})


def tipo_de(nombre: str) -> str | None:
    """Extension en minusculas si esta aceptada; si no, None. Un nombre sin
    extension (`informe`, `.pdf` o `informe.`) no tiene tipo."""
    base, punto, extension = nombre.rpartition(".")
    if not punto or not base:
        return None
    extension = extension.lower()
    return extension if extension in EXTENSIONES_ACEPTADAS else None


# Clases de extension que LAS MANOS frena POR SEPARADO (Jax#335: sin pdfplumber solo fallan los lotes con pdf):
# pdf / excel / word / otro (las imagenes y todo lo demas). Una sola tabla para el despachador y para la
# consulta de la cola, que excluye clases frenadas.
CLASE_POR_EXTENSION: dict[str, str] = {"pdf": "pdf", "xlsx": "excel", "xlsm": "excel", "docx": "word"}
CLASES = ("pdf", "excel", "word", "otro")


def clase_de(ruta: str) -> str:
    """pdf / excel / word / otro, por la extension del ULTIMO componente de la ruta (en minusculas)."""
    ultimo = ruta.rsplit("/", 1)[-1]
    extension = ultimo.rsplit(".", 1)[-1].lower() if "." in ultimo else ""
    return CLASE_POR_EXTENSION.get(extension, "otro")
