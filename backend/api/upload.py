"""Subida de adjuntos del chat (frente D, 2026-09-16).

El tipo lo deciden los bytes (adjuntos/tipos.py), no el content_type ni la
extensión que manda el cliente. Clasificar/decodificar 10 MB y base64 corren
en asyncio.to_thread; pypdf corre en el ProcessPoolExecutor acotado de
adjuntos/pdf_pool.py (RD1, 2026-09-17), no en un hilo -- retiene el GIL
incluso ahí (medido). Errores con código estable.
"""
import asyncio
import base64

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from adjuntos.errores import AdjuntoRechazado
from adjuntos.limites import cargar_limites
from adjuntos.pdf import PdfIlegible, PdfSinTexto
from adjuntos.pdf_pool import extraer_texto_en_pool
from adjuntos.politica import facetas_con_imagen
from adjuntos.turno import turno_de_subida
from adjuntos.tipos import (AdjuntoVacio, EXTENSIONES_DE_TEXTO, MIME_PDF,
                             MIMES_DE_IMAGEN, TipoNoPermitido, clasificar,
                             nombre_seguro)
from auth.middleware import get_current_user
from auth.models import AuthUser

router = APIRouter(prefix="/api/chat")


def _rechazo(status: int, code: str, **extra) -> HTTPException:
    e = AdjuntoRechazado(status, code, **extra)
    detalle = e.detail
    return HTTPException(status_code=e.status, detail=detalle)


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
):
    limites = cargar_limites()
    # Un byte de más alcanza para saber que se pasó, sin leer el resto.
    datos = await file.read(limites.max_bytes + 1)
    if len(datos) > limites.max_bytes:
        raise _rechazo(413, "adjunto_demasiado_grande", max_bytes=limites.max_bytes)
    nombre = nombre_seguro(file.filename)
    # El trabajo pesado va en hilos pero retiene el GIL: el turno limita
    # cuántas subidas lo hacen a la vez (adjuntos/turno.py). Se libera con
    # cualquier salida, también con los rechazos.
    async with turno_de_subida():
        try:
            clase, mime, texto = await asyncio.to_thread(clasificar, datos)
        except AdjuntoVacio:
            raise _rechazo(422, "adjunto_vacio") from None
        except TipoNoPermitido:
            raise _rechazo(415, "adjunto_tipo_no_permitido") from None

        if clase == "imagen":
            codificado = await asyncio.to_thread(base64.b64encode, datos)
            return {"tipo": "imagen", "nombre": nombre, "mime": mime,
                    "bytes": len(datos), "base64": codificado.decode("ascii")}

        if clase == "pdf":
            try:
                # RD1 (2026-09-17): pypdf corre en un ProcessPoolExecutor
                # aparte, no en un hilo -- no le disputa el GIL al event
                # loop (adjuntos/pdf_pool.py).
                texto, recortado = await extraer_texto_en_pool(
                    datos, limites.max_paginas, limites.max_chars)
            except PdfSinTexto:
                raise _rechazo(422, "pdf_sin_texto") from None
            except PdfIlegible:
                raise _rechazo(422, "pdf_ilegible") from None
            return {"tipo": "texto", "origen": "pdf", "nombre": nombre,
                    "bytes": len(datos), "contenido": texto, "recortado": recortado}

    return {"tipo": "texto", "origen": "texto", "nombre": nombre, "bytes": len(datos),
            "contenido": texto[: limites.max_chars],
            "recortado": len(texto) > limites.max_chars}


@router.get("/adjuntos")
async def politica_de_adjuntos(user: AuthUser = Depends(get_current_user)):
    """Lo que el frontend necesita para no adivinar: límites, `accept` del
    <input> y qué facetas ven imágenes. El servidor sigue validando todo."""
    limites = cargar_limites()
    return {
        "max_bytes": limites.max_bytes,
        "max_chars": limites.max_chars,
        "max_por_mensaje": limites.max_por_mensaje,
        "mimes_de_imagen": list(MIMES_DE_IMAGEN),
        "accept": [*MIMES_DE_IMAGEN, MIME_PDF, *EXTENSIONES_DE_TEXTO],
        "facetas_con_imagen": await facetas_con_imagen(),
    }
