"""Contrato del adjunto dentro de /api/chat (frente D, 2026-09-16; RD3,
2026-09-17: por referencia).

DECISIÓN (principal, 2026-09-17): el chat recibe `adjuntos: [{"id": ...}]`
y nada más (extra='forbid'). El contrato en línea (base64 o texto dentro del
JSON) ya no existe, y con él se fue todo lo que servía solo para desconfiar
de ese base64: el barrido del alfabeto por tramos en el loop (R18), la
decodificación del prefijo para mirar la firma y el tope del largo. El tipo
lo decidieron los bytes al subir (api/upload.py) y el dato vive en un
directorio 0700 del servicio (adjuntos/almacen.py).

Qué hace el servidor con cada id, ANTES de memoria, estado y proveedor:
1. tope por mensaje (JAX_ADJUNTO_MAX_POR_MENSAJE);
2. `buscar_adjuntos`: el sidecar de cada id, atado al dueño; cualquier "no"
   es el mismo 404 `adjunto_no_encontrado`, sin decir cuál id falló;
3. el llamador chequea visión contra la faceta resuelta (sin codificar nada);
4. `leer_adjuntos`: texto guardado (ya extraído y recortado) y la imagen
   codificada a base64 por tramos en un hilo, de a
   JAX_ADJUNTO_IMAGENES_EN_PROCESO a la vez (adjuntos/turno.py).

El base64 viaja solo hacia el proveedor (http_client.LiteralJsonCrudo): ni
logs, ni memoria, ni historial. El id tampoco se loguea: para su dueño es
una llave de su adjunto.
"""
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from adjuntos import almacen
from adjuntos.errores import AdjuntoRechazado
from adjuntos.limites import LimitesDeAdjuntos
from adjuntos.turno import turno_de_imagen

_APERTURA = '<<<ADJUNTO nombre="{nombre}" origen="{origen}">>>'
_CIERRE = "<<<FIN ADJUNTO>>>"


class AdjuntoRef(BaseModel):
    """Lo único que el cliente manda de un adjunto: el id que le devolvió
    /api/chat/upload. Largo y alfabeto exactos en el borde (mismo formato que
    almacen.id_valido): un id malformado es 422 de pydantic y nunca llega al
    disco. Uno bien formado que no es del usuario es el 404 del almacén."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=almacen.LARGO_ID, max_length=almacen.LARGO_ID,
                    pattern=rf"^{almacen.PATRON_ID}$")


class TextoValidado(NamedTuple):
    nombre: str
    origen: str
    contenido: str


class ImagenValidada(NamedTuple):
    """`tramos_base64`: el base64 de la imagen en pedazos de bytes ASCII que
    concatenados son el base64 entero (almacen.leer_imagen_en_base64). Nunca
    se juntan en un str: van tal cual al cuerpo del proveedor."""
    nombre: str
    mime: str
    tramos_base64: tuple[bytes, ...]
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
    """`imagenes`: las imágenes de este turno, en cualquier forma con
    verdad de colección -- los metadatos de sidecar (`imagenes_de`, antes de
    leerlas) o las `ImagenValidada` ya leídas (dispatch). Vacía, no exige."""
    if imagenes and "image" not in f.input_modalities:
        raise ImagenNoSoportadaError(facet, f.model)


async def buscar_adjuntos(refs: list[AdjuntoRef], user, limites: LimitesDeAdjuntos) -> list[dict]:
    """Tope por mensaje y los sidecars de cada id, del `user` y vigentes.
    Sin leer datos: alcanza para decidir visión antes de codificar una
    imagen. AdjuntoNoEncontrado (404, sin extras) ante cualquier id que no
    sirva; el primero corta y la respuesta no dice cuál fue."""
    if len(refs) > limites.max_por_mensaje:
        raise AdjuntoRechazado(422, "adjuntos_demasiados", max=limites.max_por_mensaje)
    return [await almacen.obtener(ref.id, user) for ref in refs]


def imagenes_de(metadatos: list[dict]) -> list[dict]:
    """Los metadatos de sidecar que son imágenes (Final fix wave #2, item 9:
    antes `hay_imagenes` devolvía un bool y /api/chat le pasaba `True` a
    `exigir_soporte_de_imagen`)."""
    return [m for m in metadatos if m.get("tipo") == "imagen"]


async def leer_adjuntos(metadatos: list[dict], user, limites: LimitesDeAdjuntos) -> AdjuntosValidados:
    """Lee cada adjunto ya buscado. `leer` y `leer_imagen_en_base64` vuelven a
    validar dueño y vencimiento y exigen que el dato exista: uno que el
    limpiador borró entre `buscar_adjuntos` y acá es el mismo 404."""
    textos: list[TextoValidado] = []
    imagenes: list[ImagenValidada] = []
    for m in metadatos:
        if m.get("tipo") == "imagen":
            # Tope de codificaciones a la vez (adjuntos/turno.py): binascii
            # retiene el GIL por tramo y 25 hilos a la vez atrasan el loop.
            async with turno_de_imagen():
                meta, tramos = await almacen.leer_imagen_en_base64(m["id"], user)
            imagenes.append(ImagenValidada(meta["nombre"], meta["mime"], tramos, meta["bytes"]))
            continue
        meta, datos = await almacen.leer(m["id"], user)
        try:
            # <= JAX_ADJUNTO_MAX_CHARS caracteres: decodificar en el loop es
            # microsegundos. Se recorta otra vez por si el límite bajó
            # después de la subida.
            contenido = datos.decode("utf-8")[: limites.max_chars]
        except UnicodeDecodeError:
            raise almacen.AdjuntoNoEncontrado() from None
        textos.append(TextoValidado(meta["nombre"], meta.get("origen", "texto"), contenido))
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
    el contenido, ni el base64, ni el id (spec §D, Discrepancia 3 del plan)."""
    lineas = [mensaje]
    lineas += [f'[adjunto nombre="{t.nombre}" tipo="{t.origen}" caracteres={len(t.contenido)}]'
               for t in validados.textos]
    lineas += [_linea_de_imagen(i) for i in validados.imagenes]
    return "\n".join(lineas)
