"""Tipos de documento que acepta la subida de un proyecto (E2a, T5).

Son los que `procesamiento/` sabe extraer. Esta es la UNICA lista de la
plataforma: la API la valida y el frontend la recibe por
`GET /api/proyectos/documentos/limites`, sin copiarla.
"""
from __future__ import annotations

EXTENSIONES_ACEPTADAS: frozenset[str] = frozenset(
    {"pdf", "xlsx", "xls", "docx", "png", "jpg", "jpeg", "tif", "tiff", "csv", "txt", "md"})


def tipo_de(nombre: str) -> str | None:
    """Extension en minusculas si esta aceptada; si no, None. Un nombre sin
    extension (`informe`, `.pdf` o `informe.`) no tiene tipo."""
    base, punto, extension = nombre.rpartition(".")
    if not punto or not base:
        return None
    extension = extension.lower()
    return extension if extension in EXTENSIONES_ACEPTADAS else None
