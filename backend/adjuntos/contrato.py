"""Contrato del adjunto dentro de /api/chat (frente D, 2026-09-16).

El cliente devuelve lo que le dio /api/chat/upload, pero NADA se le cree: el
texto se recorta de nuevo, el base64 se decodifica estricto, la firma tiene
que coincidir con el mime declarado y el tamaño con el límite. El base64
viaja solo hacia el proveedor: ni logs, ni memoria, ni historial.
"""
import asyncio
import base64
import re
from typing import Annotated, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from adjuntos.errores import AdjuntoRechazado
from adjuntos.limites import LimitesDeAdjuntos
from adjuntos.tipos import MIMES_DE_IMAGEN, mime_de_imagen, nombre_seguro

_APERTURA = '<<<ADJUNTO nombre="{nombre}" origen="{origen}">>>'
_CIERRE = "<<<FIN ADJUNTO>>>"


class AdjuntoTexto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["texto"]
    origen: Literal["texto", "pdf"]
    nombre: str
    contenido: str


class AdjuntoImagen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["imagen"]
    nombre: str
    mime: Literal[MIMES_DE_IMAGEN]
    base64: str


Adjunto = Annotated[AdjuntoTexto | AdjuntoImagen, Field(discriminator="tipo")]


class TextoValidado(NamedTuple):
    nombre: str
    origen: str
    contenido: str


class ImagenValidada(NamedTuple):
    nombre: str
    mime: str
    base64: str
    bytes: int


class AdjuntosValidados(NamedTuple):
    textos: tuple[TextoValidado, ...]
    imagenes: tuple[ImagenValidada, ...]


SIN_ADJUNTOS = AdjuntosValidados((), ())


class ImagenNoSoportadaError(ValueError):
    """El modelo resuelto de la faceta no declara 'image' en input_modalities."""

    def __init__(self, facet: str, model: str):
        super().__init__(f"imagen_no_soportada facet={facet} model={model}")
        self.facet = facet
        self.model = model


def exigir_soporte_de_imagen(f, facet: str, imagenes) -> None:
    if imagenes and "image" not in f.input_modalities:
        raise ImagenNoSoportadaError(facet, f.model)


# base64 estricto (lo mismo que acepta b64decode(validate=True)): solo el
# alfabeto, relleno solo al final y de a lo sumo 2, y largo múltiplo de 4
# (eso se mira aparte). Sin grupos repetidos: con `(?:x{4})*` el motor de re
# guarda estado por repetición y medido fueron 445 MB y 119 ms para 10 MB.
_BASE64_ESTRICTO = re.compile(r"[A-Za-z0-9+/]*={0,2}")
# 16 caracteres dan 12 bytes: alcanza para la firma más larga (WEBP, 12).
_PREFIJO_DE_FIRMA = 16


def _validar_imagen(a: AdjuntoImagen, limites: LimitesDeAdjuntos) -> ImagenValidada:
    # Tope del base64 de max_bytes ANTES de mirar nada: 4 caracteres por
    # cada 3 bytes, redondeado hacia arriba.
    if len(a.base64) > ((limites.max_bytes + 2) // 3) * 4:
        raise AdjuntoRechazado(413, "adjunto_demasiado_grande", max_bytes=limites.max_bytes)
    # R16 (2026-09-17): sin decodificar la imagen entera. Decodificar 10 MB
    # para mirar 12 bytes de firma y un largo costaba ~23 MB de pico por
    # pedido en el hilo. La validez se verifica con la expresión (no copia),
    # el largo sale del largo y el relleno, y se decodifica solo el prefijo.
    # Mismo contrato que b64decode(validate=True): lo fija un test diferencial.
    if len(a.base64) % 4 or _BASE64_ESTRICTO.fullmatch(a.base64) is None:
        raise AdjuntoRechazado(422, "adjunto_invalido")
    relleno = 2 if a.base64.endswith("==") else 1 if a.base64.endswith("=") else 0
    largo = len(a.base64) // 4 * 3 - relleno
    if largo == 0:
        raise AdjuntoRechazado(422, "adjunto_vacio")
    if largo > limites.max_bytes:
        raise AdjuntoRechazado(413, "adjunto_demasiado_grande", max_bytes=limites.max_bytes)
    if mime_de_imagen(base64.b64decode(a.base64[:_PREFIJO_DE_FIRMA])) != a.mime:
        raise AdjuntoRechazado(422, "adjunto_invalido")
    return ImagenValidada(nombre_seguro(a.nombre), a.mime, a.base64, largo)


async def validar_adjuntos(adjuntos: list, limites: LimitesDeAdjuntos) -> AdjuntosValidados:
    if len(adjuntos) > limites.max_por_mensaje:
        raise AdjuntoRechazado(422, "adjuntos_demasiados", max=limites.max_por_mensaje)
    textos: list[TextoValidado] = []
    imagenes: list[ImagenValidada] = []
    for a in adjuntos:
        if isinstance(a, AdjuntoImagen):
            imagenes.append(await asyncio.to_thread(_validar_imagen, a, limites))
        else:
            textos.append(TextoValidado(nombre_seguro(a.nombre), a.origen, a.contenido[: limites.max_chars]))
    return AdjuntosValidados(tuple(textos), tuple(imagenes))


def componer_mensaje(mensaje: str, textos) -> str:
    """El texto del adjunto va al modelo entre delimitadores. Un cierre falso
    dentro del contenido se neutraliza: el adjunto no puede "salirse" del
    bloque y hablarle al modelo como si fuera el usuario."""
    bloques = [mensaje]
    for t in textos:
        contenido = t.contenido.replace(_CIERRE, "<<<FIN_ADJUNTO_CITADO>>>")
        bloques.append(f"{_APERTURA.format(nombre=t.nombre, origen=t.origen)}\n{contenido}\n{_CIERRE}")
    return "\n\n".join(bloques)


def _linea_de_imagen(i: ImagenValidada) -> str:
    return f'[adjunto nombre="{i.nombre}" tipo="{i.mime}" bytes={i.bytes}]'


def mensaje_para_historial(mensaje: str, validados: AdjuntosValidados) -> str:
    """Historial en RAM: conserva el texto (la pregunta siguiente sobre el
    documento tiene que funcionar), de la imagen solo los metadatos."""
    return "\n".join([componer_mensaje(mensaje, validados.textos),
                      *(_linea_de_imagen(i) for i in validados.imagenes)])


def metadatos_para_memoria(mensaje: str, validados: AdjuntosValidados) -> str:
    """Memoria persistente (jax_memory.messages): nombre, tipo y tamaño. Ni
    el contenido ni el base64 (spec §D, Discrepancia 3 del plan)."""
    lineas = [mensaje]
    lineas += [f'[adjunto nombre="{t.nombre}" tipo="{t.origen}" caracteres={len(t.contenido)}]'
               for t in validados.textos]
    lineas += [_linea_de_imagen(i) for i in validados.imagenes]
    return "\n".join(lineas)
