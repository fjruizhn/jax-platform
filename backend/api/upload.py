"""Subida de adjuntos del chat (frente D, 2026-09-16; RD2 2026-09-17: por
referencia).

DECISIÓN (principal, 2026-09-17): el archivo se guarda en JAX_ADJUNTOS_DIR
(adjuntos/almacen.py) y la respuesta es un JSON chico con su id; el chat lo
pide por id (RD3). Nunca base64, nunca el texto completo.

Camino de una subida:

1. Starlette ya recibió el cuerpo multipart ANTES de llamar a este handler:
   python-multipart lo parsea en streaming y Starlette escribe la parte del
   archivo en un SpooledTemporaryFile (en memoria hasta 1 MB, después a un
   archivo de TMPDIR). Acá no se vuelve a leer entero: `copiar_subida` lo
   copia en un hilo, de a 1 MB, a un temporal 0600 dentro de
   JAX_ADJUNTOS_DIR, contando bytes; pasado max_bytes corta y es 413.
2. El tipo lo deciden los bytes del temporal (adjuntos/tipos.py), en un hilo:
   firma de imagen o PDF por la cabecera; texto validado entero por bloques
   con un decodificador incremental.
3. PDF: pypdf corre en el ProcessPoolExecutor de adjuntos/pdf_pool.py (RD1)
   y recibe la RUTA del temporal, no los bytes.
4. Se guarda: la imagen se renombra al dato; del texto/PDF se guarda solo el
   texto extraído y recortado. El sidecar (dueño, vencimiento) es el commit.
5. El temporal se borra en `finally`: ningún rechazo, error ni cancelación
   deja archivos (lo que un hilo ya lanzado escriba después lo levanta el
   limpiador por edad).

Clasificar y extraer van dentro de turno_de_subida
(JAX_ADJUNTO_SUBIDAS_EN_PROCESO); la copia no, es I/O de disco en un hilo.
Errores con código estable.

RD6 (2026-09-17): antes de copiar, en este orden, tope por archivo (413
`adjunto_demasiado_grande`), cuota del usuario (413 `adjuntos_cuota_excedida`,
reservada bajo candado y vuelta a mirar en el commit) y disco libre (507
`adjuntos_sin_espacio`). Ver adjuntos/cuota.py.
"""
import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from adjuntos import almacen, cuota
from adjuntos.errores import AdjuntoRechazado
from adjuntos.limites import cargar_limites
from adjuntos.pdf import PdfIlegible, PdfSinTexto
from adjuntos.pdf_pool import extraer_texto_en_pool
from adjuntos.politica import facetas_con_imagen
from adjuntos.turno import turno_de_subida
from adjuntos.tipos import (AdjuntoVacio, EXTENSIONES_DE_TEXTO, MIME_PDF,
                             MIMES_DE_IMAGEN, TipoNoPermitido, clasificar_archivo,
                             nombre_seguro)
from auth.middleware import get_current_user
from auth.models import AuthUser

router = APIRouter(prefix="/api/chat")

# Cuánto texto vuelve para que la interfaz muestre de qué se trata. Acotado a
# propósito: el texto completo ya está en el servidor, y devolverlo invitaría
# a que el cliente lo reenvíe en el chat -- justo lo que RD3 deja de aceptar.
VISTA_PREVIA_CARACTERES = 200


def _http(e: AdjuntoRechazado) -> HTTPException:
    detalle = e.detail
    return HTTPException(status_code=e.status, detail=detalle)


def _rechazo(status: int, code: str, **extra) -> HTTPException:
    return _http(AdjuntoRechazado(status, code, **extra))


def _tamano_recibido(archivo) -> int:
    """Síncrona (to_thread). Bytes del archivo que Starlette ya volcó (el
    SpooledTemporaryFile de UploadFile): seek al final y vuelta al principio,
    sin leer datos."""
    archivo.seek(0, os.SEEK_END)
    tamano = archivo.tell()
    archivo.seek(0)
    return tamano


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
):
    limites = cargar_limites()
    directorio = almacen.cargar_directorio()
    ttl_horas = almacen.cargar_ttl_horas()
    cuota_bytes = cuota.cargar_cuota_por_usuario()
    nombre = nombre_seguro(file.filename)

    # RD6: orden de los rechazos por tamaño, del más barato y accionable al
    # global. (1) Tope por archivo: sin estado; un archivo que no entra ni con
    # la cuota vacía tiene que recibir ESE motivo, no "cuota excedida".
    # (2) Cuota del usuario: depende solo de lo suyo. (3) Disco libre: global,
    # lo último antes de escribir. Todo antes de la copia: Starlette ya
    # recibió el cuerpo entero y su tamaño es un hecho, no una declaración
    # del cliente.
    recibido = await asyncio.to_thread(_tamano_recibido, file.file)
    if recibido > limites.max_bytes:
        raise _rechazo(413, "adjunto_demasiado_grande", max_bytes=limites.max_bytes)

    temporal: Path = directorio / almacen.nombre_temporal()
    try:
        async with cuota.reserva(directorio, user, recibido, cuota_bytes) as reserva:
            await cuota.exigir_disco_libre(directorio, recibido)
            try:
                tamano = await asyncio.to_thread(almacen.copiar_subida, file.file, temporal, limites.max_bytes)
            except almacen.SubidaDemasiadoGrande:
                raise _rechazo(413, "adjunto_demasiado_grande", max_bytes=limites.max_bytes) from None

            # Clasificar 10 MB de texto y pypdf compiten por CPU/GIL: el turno
            # limita cuántas subidas lo hacen a la vez (adjuntos/turno.py). Se
            # libera con cualquier salida, también con los rechazos.
            async with turno_de_subida():
                try:
                    clase = await asyncio.to_thread(clasificar_archivo, temporal, limites.max_chars)
                except AdjuntoVacio:
                    raise _rechazo(422, "adjunto_vacio") from None
                except TipoNoPermitido:
                    raise _rechazo(415, "adjunto_tipo_no_permitido") from None

                if clase.clase == "pdf":
                    try:
                        texto, recortado = await extraer_texto_en_pool(
                            str(temporal), limites.max_paginas, limites.max_chars)
                    except PdfSinTexto:
                        raise _rechazo(422, "pdf_sin_texto") from None
                    except PdfIlegible:
                        raise _rechazo(422, "pdf_ilegible") from None
                    origen = "pdf"
                else:
                    texto, recortado, origen = clase.texto, clase.recortado, "texto"

            # El commit (sidecar) vuelve a mirar la cuota bajo el candado del
            # usuario, con el tamaño copiado (adjuntos/cuota.py).
            if clase.clase == "imagen":
                meta = await reserva.confirmar(tamano, lambda: asyncio.to_thread(
                    almacen.guardar_imagen, directorio, temporal, user=user, mime=clase.mime,
                    nombre=nombre, bytes_=tamano, ttl_horas=ttl_horas))
                return {"id": meta["id"], "tipo": "imagen", "nombre": nombre, "mime": clase.mime,
                        "bytes": tamano}

            meta = await reserva.confirmar(tamano, lambda: asyncio.to_thread(
                almacen.guardar_texto, directorio, texto, user=user, origen=origen, nombre=nombre,
                bytes_=tamano, recortado=recortado, ttl_horas=ttl_horas))
            return {"id": meta["id"], "tipo": "texto", "origen": origen, "nombre": nombre,
                    "bytes": tamano, "caracteres": meta["caracteres"], "recortado": recortado,
                    "vista_previa": texto[:VISTA_PREVIA_CARACTERES]}
    except AdjuntoRechazado as e:
        # Cuota (413 adjuntos_cuota_excedida) y disco (507 adjuntos_sin_espacio).
        raise _http(e) from None
    finally:
        # La imagen guardada ya no está acá (se renombró): idempotente.
        await asyncio.to_thread(almacen.borrar_temporal, temporal)


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
