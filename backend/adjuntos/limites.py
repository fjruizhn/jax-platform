"""Límites de los adjuntos del chat (frente D, 2026-09-16).

Salen del entorno (/etc/jax/.env vía EnvironmentFile del unit). Sin default:
un límite que falta no es "sin límite", es un error de configuración, y el
servicio no arranca (main.lifespan lo llama antes de abrir la base).

Sin caché a propósito: son cuatro os.environ.get por llamada (microsegundos)
y el entorno de un proceso no cambia en caliente, así que no hay nada que
invalidar.
"""
import os
from dataclasses import dataclass

VARIABLES = {
    "max_bytes": "JAX_ADJUNTO_MAX_BYTES",
    "max_chars": "JAX_ADJUNTO_MAX_CHARS",
    "max_paginas": "JAX_ADJUNTO_MAX_PAGINAS",
    "max_por_mensaje": "JAX_ADJUNTO_MAX_POR_MENSAJE",
}


class LimitesDeAdjuntosInvalidos(RuntimeError):
    """Falta una variable o no es un entero > 0. Fail-closed."""


@dataclass(frozen=True)
class LimitesDeAdjuntos:
    max_bytes: int
    max_chars: int
    max_paginas: int
    max_por_mensaje: int


def _entero_positivo(crudo: str | None) -> int | None:
    if crudo is None:
        return None
    try:
        valor = int(crudo)
    except ValueError:
        return None
    return valor if valor > 0 else None


def cargar_limites() -> LimitesDeAdjuntos:
    valores: dict[str, int] = {}
    problemas: list[str] = []
    for campo, variable in VARIABLES.items():
        crudo = os.environ.get(variable)
        valor = _entero_positivo(crudo)
        if valor is None:
            problemas.append(f"{variable}={crudo!r}")
        else:
            valores[campo] = valor
    if problemas:
        raise LimitesDeAdjuntosInvalidos(
            "límites de adjuntos sin configurar o inválidos (enteros > 0 en "
            "/etc/jax/.env): " + ", ".join(problemas))
    return LimitesDeAdjuntos(**valores)


VARIABLE_DE_IMAGENES_EN_PROCESO = "JAX_ADJUNTO_IMAGENES_EN_PROCESO"


VARIABLE_DE_SUBIDAS_EN_PROCESO = "JAX_ADJUNTO_SUBIDAS_EN_PROCESO"


def _cargar_tope(variable: str, que: str) -> int:
    crudo = os.environ.get(variable)
    valor = _entero_positivo(crudo)
    if valor is None:
        raise LimitesDeAdjuntosInvalidos(
            f"tope de {que} en proceso sin configurar o inválido (entero > 0 en "
            f"/etc/jax/.env): {variable}={crudo!r}")
    return valor


def cargar_imagenes_en_proceso() -> int:
    """Cuántas imágenes guardadas se leen y codifican a base64 a la vez para
    el proveedor (R16, 2026-09-17; RD3: antes, cuántas validaban el base64
    del cliente; adjuntos/turno.py). Sin default, como los otros límites: si
    falta, el servicio no arranca."""
    return _cargar_tope(VARIABLE_DE_IMAGENES_EN_PROCESO, "imágenes")


def cargar_subidas_en_proceso() -> int:
    """Cuántas subidas de /api/chat/upload hacen su trabajo pesado a la vez
    (clasificar, b64encode, pypdf; revisión final 2026-09-17, adjuntos/turno.py).
    Sin default: si falta, el servicio no arranca."""
    return _cargar_tope(VARIABLE_DE_SUBIDAS_EN_PROCESO, "subidas")


# --- RD1 (2026-09-17): ProcessPoolExecutor de pypdf (adjuntos/pdf_pool.py) --
#
# Estos dos, a diferencia de los de arriba, tienen TECHO además de piso: un
# entero > 0 sin límite superior es tan fail-open como uno ausente. Un valor
# de más en JAX_ADJUNTO_PDF_PROCESOS arrancaría más procesos de pypdf que
# núcleos tiene la máquina (le resta CPU al resto del servicio sin comprar
# throughput); uno de más en JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS deja un PDF
# patológico ocupando un worker minutos enteros antes de que el reciclado
# (pdf_pool._reciclar_tras_timeout) lo note.

VARIABLE_DE_PROCESOS_DE_PDF = "JAX_ADJUNTO_PDF_PROCESOS"
VARIABLE_DE_TIMEOUT_DE_PDF = "JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS"

# Rango 1..8: más de 8 procesos de pypdf compitiendo por CPU en una sola
# instancia de jax-platform no suma throughput (el resto del servicio -- el
# event loop, las subidas de imagen, el resto de los endpoints -- también
# necesita núcleos) y sí suma memoria reservada de más (cada worker es un
# intérprete de Python aparte). No es una medición de carga (eso es RD5):
# es un techo de sensatez para que un .env con un cero de más no se note
# arrancando un pool descomunal.
LIMITE_PROCESOS_DE_PDF = 8

# Rango 1..60 s: JAX_ADJUNTO_MAX_PAGINAS ya acota cuánto pypdf tiene que leer
# (20 páginas en producción); 60 s es margen holgado para un PDF legítimo de
# ese tamaño y corto para no dejar un worker ocupado con uno patológico más
# de un minuto antes de reciclar el pool.
LIMITE_TIMEOUT_DE_PDF_SEGUNDOS = 60


def _cargar_tope_acotado(variable: str, que: str, maximo: int) -> int:
    crudo = os.environ.get(variable)
    valor = _entero_positivo(crudo)
    if valor is None or valor > maximo:
        raise LimitesDeAdjuntosInvalidos(
            f"tope de {que} sin configurar o fuera de rango (entero 1..{maximo} en "
            f"/etc/jax/.env): {variable}={crudo!r}")
    return valor


def cargar_procesos_de_pdf() -> int:
    """Tamaño del ProcessPoolExecutor que extrae texto de PDF (RD1,
    2026-09-17; adjuntos/pdf_pool.py). Rango 1..LIMITE_PROCESOS_DE_PDF. Sin
    default, como el resto: si falta o se pasa del techo, el servicio no
    arranca."""
    return _cargar_tope_acotado(
        VARIABLE_DE_PROCESOS_DE_PDF, "procesos de pdf", LIMITE_PROCESOS_DE_PDF)


def cargar_timeout_de_pdf() -> int:
    """Segundos máximos que se le dan a UNA extracción de PDF antes de darla
    por perdida y reciclar el pool entero (RD1, 2026-09-17;
    adjuntos/pdf_pool.py::extraer_texto_en_pool). Rango
    1..LIMITE_TIMEOUT_DE_PDF_SEGUNDOS. Se lee en cada llamada (no hay nada que
    cachear: el entorno de un proceso no cambia en caliente), pero también se
    valida una vez al arrancar el lifespan para fallar cerrado antes de
    servir una sola request."""
    return _cargar_tope_acotado(
        VARIABLE_DE_TIMEOUT_DE_PDF, "timeout de pdf", LIMITE_TIMEOUT_DE_PDF_SEGUNDOS)
