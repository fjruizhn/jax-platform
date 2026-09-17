"""Qué es un adjunto, decidido por sus BYTES (frente D, 2026-09-16).

Allowlist cerrada, fail-closed: imagen PNG/JPEG/WebP por firma, PDF por
`%PDF-`, texto si es UTF-8 estricto sin NUL. Todo lo demás es
TipoNoPermitido (415). SVG es texto: XML activo, no entrada de visión.
GIF queda fuera: Gemini no lo acepta como imagen (Discrepancia 4 del plan).
"""
import unicodedata
from typing import Literal

MIMES_DE_IMAGEN: tuple[str, ...] = ("image/png", "image/jpeg", "image/webp")
MIME_PDF = "application/pdf"
# Solo una pista para el <input accept> del navegador: el servidor decide por
# bytes, no por extensión.
EXTENSIONES_DE_TEXTO: tuple[str, ...] = (
    ".txt", ".md", ".csv", ".json", ".py", ".js", ".jsx", ".ts", ".tsx",
    ".html", ".css", ".toml", ".yml", ".yaml", ".sh", ".svg",
)
_NOMBRE_MAX = 255  # NAME_MAX de los sistemas de archivos: el nombre es de archivo

Clase = Literal["imagen", "pdf", "texto"]


class AdjuntoVacio(ValueError):
    """Cero bytes."""


class TipoNoPermitido(ValueError):
    """Ni imagen permitida, ni PDF, ni texto UTF-8."""


def mime_de_imagen(datos: bytes) -> str | None:
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if datos.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(datos) >= 12 and datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return None


def clasificar(datos: bytes) -> tuple[Clase, str, str | None]:
    """(clase, mime, texto). `texto` solo viene para la clase "texto", ya
    decodificado, para no decodificar 10 MB dos veces. Síncrona: el llamador
    la corre en asyncio.to_thread."""
    if not datos:
        raise AdjuntoVacio()
    mime = mime_de_imagen(datos)
    if mime is not None:
        return "imagen", mime, None
    if datos.startswith(b"%PDF-"):
        return "pdf", MIME_PDF, None
    # Rechazo de formatos binarios conocidos que no se aceptan: GIF
    # (Gemini no lo soporta como imagen). La firma de GIF es de 6 bytes:
    # GIF87a o GIF89a, no solo "GIF" (3 bytes).
    if datos.startswith(b"GIF87a") or datos.startswith(b"GIF89a"):
        raise TipoNoPermitido("binario (GIF)")
    if b"\x00" in datos:
        raise TipoNoPermitido("binario (NUL)")
    try:
        texto = datos.decode("utf-8")
    except UnicodeDecodeError as e:
        raise TipoNoPermitido("no es UTF-8") from e
    return "texto", "text/plain", texto


def nombre_seguro(crudo: str | None) -> str:
    """Solo el último componente, sin caracteres de control ni comillas ni
    <>: el nombre viaja dentro de delimitadores del prompt y de la línea de
    metadatos de memoria. Vacío si no queda nada (el frontend muestra su
    texto i18n en ese caso)."""
    base = (crudo or "").replace("\\", "/").rsplit("/", 1)[-1]
    limpio = "".join(
        c for c in base
        if unicodedata.category(c)[0] != "C" and c not in '"<>'
    )
    return limpio.strip()[:_NOMBRE_MAX]
