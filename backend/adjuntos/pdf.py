"""Texto de un PDF adjunto (frente D, 2026-09-16), con pypdf.

SÍNCRONA y CPU-bound: el llamador (adjuntos/pdf_pool.py::extraer_texto_en_pool)
la corre en un worker del ProcessPoolExecutor, nunca en el proceso web (RD1:
pypdf retiene el GIL aun en un hilo). Acotada por
JAX_ADJUNTO_MAX_PAGINAS (páginas leídas) y JAX_ADJUNTO_MAX_CHARS (se corta
en cuanto se juntan suficientes caracteres). Nunca devuelve "" como éxito:
un PDF sin texto es PdfSinTexto (un escaneo no se le manda vacío al modelo).
"""
import io
import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)


class PdfIlegible(ValueError):
    """Dañado, cifrado o con una estructura que pypdf no lee."""


class PdfSinTexto(ValueError):
    """Se leyó, pero no tiene texto extraíble."""


def extraer_texto(datos: bytes | str, max_paginas: int, max_chars: int) -> tuple[str, bool]:
    """`datos`: los bytes del PDF o la RUTA (str) al archivo. RD2
    (2026-09-17): la subida le pasa la ruta del temporal a este worker del
    ProcessPoolExecutor -- lo que viaja entre procesos es una cadena corta,
    no 10 MB serializados, y el proceso web nunca tiene el PDF en memoria.
    pypdf abre la ruta él mismo, dentro del worker."""
    try:
        lector = PdfReader(datos if isinstance(datos, str) else io.BytesIO(datos))
        if lector.is_encrypted:
            raise PdfIlegible("cifrado")
        paginas = lector.pages
        total_paginas = len(paginas)
        partes: list[str] = []
        acumulado = 0
        leidas = 0
        for indice in range(min(total_paginas, max_paginas)):
            texto = paginas[indice].extract_text() or ""
            leidas += 1
            if texto:
                partes.append(texto)
                acumulado += len(texto)
            if acumulado >= max_chars:
                break
    except PdfIlegible:
        raise
    except Exception as e:
        # Fail-closed: re-lanza como PdfIlegible (422 pdf_ilegible), nunca
        # sigue con texto parcial. Amplio porque un PDF malformado puede
        # reventar pypdf con casi cualquier excepción. Solo se loguea el TIPO:
        # el mensaje de la excepción puede traer fragmentos del archivo del
        # usuario, y el archivo no va a los logs.
        logger.warning("pdf adjunto ilegible: %s", type(e).__name__)
        raise PdfIlegible(type(e).__name__) from e
    texto = "\n\n".join(partes).strip()
    if not texto:
        raise PdfSinTexto()
    recortado = len(texto) > max_chars or leidas < total_paginas
    return texto[:max_chars], recortado
