"""Qué es un adjunto, decidido por sus BYTES (frente D, 2026-09-16).

Allowlist cerrada, fail-closed: imagen PNG/JPEG/WebP por firma, PDF por
`%PDF-`, texto si es UTF-8 estricto sin NUL. Todo lo demás es
TipoNoPermitido (415). SVG es texto: XML activo, no entrada de visión.
GIF queda fuera: Gemini no lo acepta como imagen (Discrepancia 4 del plan).
"""
import codecs
import unicodedata
from dataclasses import dataclass
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
# Tope del nombre CRUDO (antes de limpiar). Cuatro veces NAME_MAX: deja pasar
# un nombre de 255 caracteres aunque traiga comillas, controles o una ruta
# corta delante, y acota el trabajo del filtro carácter a carácter. Lo usa
# nombre_seguro (recorte previo de file.filename del upload). Desde RD3 el
# chat ya no recibe nombres: los toma del sidecar.
NOMBRE_CRUDO_MAX = 1024

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


# RD2 (2026-09-17): el texto se valida por bloques, sin cargar el archivo
# entero. 256 KB: el mismo tramo que ya usan los escaneos del loop (R18).
BLOQUE_DE_TEXTO = 256 * 1024
_CABECERA = 12  # lo que necesita la firma más larga (WebP: RIFF....WEBP)


@dataclass(frozen=True)
class Clasificacion:
    clase: Clase
    mime: str
    texto: str | None  # solo "texto": ya recortado a max_chars
    recortado: bool


def _leer_bloque(f, n: int) -> bytes:
    return f.read(n)


def clasificar_archivo(ruta, max_chars: int) -> Clasificacion:
    """Clasifica el archivo subido por sus bytes. SÍNCRONA: el llamador la
    corre en asyncio.to_thread.

    Imagen y PDF se deciden por la cabecera (12 bytes). El texto se valida
    ENTERO -- UTF-8 estricto y sin NUL en todo el archivo, no solo en lo que
    se guarda -- con un decodificador incremental de a BLOQUE_DE_TEXTO, y
    solo se retienen los primeros max_chars caracteres. Memoria: un bloque
    y el recorte, nunca los 10 MB."""
    with open(ruta, "rb") as f:
        cabecera = _leer_bloque(f, _CABECERA)
        if not cabecera:
            raise AdjuntoVacio()
        mime = mime_de_imagen(cabecera)
        if mime is not None:
            return Clasificacion("imagen", mime, None, False)
        if cabecera.startswith(b"%PDF-"):
            return Clasificacion("pdf", MIME_PDF, None, False)
        # Rechazo de formatos binarios conocidos que no se aceptan: GIF
        # (Gemini no lo soporta como imagen). La firma de GIF es de 6 bytes:
        # GIF87a o GIF89a, no solo "GIF" (3 bytes).
        if cabecera.startswith(b"GIF87a") or cabecera.startswith(b"GIF89a"):
            raise TipoNoPermitido("binario (GIF)")
        decodificador = codecs.getincrementaldecoder("utf-8")("strict")
        partes: list[str] = []
        retenidos = 0
        recortado = False
        bloque = cabecera
        while True:
            if b"\x00" in bloque:
                raise TipoNoPermitido("binario (NUL)")
            try:
                trozo = decodificador.decode(bloque, final=not bloque)
            except UnicodeDecodeError as e:
                raise TipoNoPermitido("no es UTF-8") from e
            if trozo and not recortado:
                falta = max_chars - retenidos
                if len(trozo) > falta:
                    partes.append(trozo[:falta])
                    retenidos = max_chars
                    recortado = True
                else:
                    partes.append(trozo)
                    retenidos += len(trozo)
            if not bloque:
                break
            bloque = _leer_bloque(f, BLOQUE_DE_TEXTO)
    return Clasificacion("texto", "text/plain", "".join(partes), recortado)


def nombre_seguro(crudo: str | None) -> str:
    """Solo el último componente, sin caracteres de control ni comillas ni
    <> ni []: el nombre viaja dentro de delimitadores del prompt y de la línea
    de metadatos de memoria ('[adjunto nombre="..." ...]'; un corchete podría
    cerrarla y fingir otra). Vacío si no queda nada (el frontend muestra su
    texto i18n en ese caso).

    La entrada se recorta a NOMBRE_CRUDO_MAX ANTES de todo: el filtro va
    carácter a carácter en Python y un nombre de 10 MB bloqueaba el loop
    ~455 ms. Es un prefijo: un nombre crudo más largo que el tope ya no es un
    nombre de archivo razonable, y /api/chat lo rechaza antes con 422."""
    base = (crudo or "")[:NOMBRE_CRUDO_MAX].replace("\\", "/").rsplit("/", 1)[-1]
    limpio = "".join(
        c for c in base
        if unicodedata.category(c)[0] != "C" and c not in '"<>[]'
    )
    return limpio.strip()[:_NOMBRE_MAX]
