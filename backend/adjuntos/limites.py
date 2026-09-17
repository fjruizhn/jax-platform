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
    """Cuántas imágenes validan su base64 a la vez en el event loop (R16,
    2026-09-17; adjuntos/turno.py). Sin default, como los otros límites: si
    falta, el servicio no arranca."""
    return _cargar_tope(VARIABLE_DE_IMAGENES_EN_PROCESO, "imágenes")


def cargar_subidas_en_proceso() -> int:
    """Cuántas subidas de /api/chat/upload hacen su trabajo pesado a la vez
    (clasificar, b64encode, pypdf; revisión final 2026-09-17, adjuntos/turno.py).
    Sin default: si falta, el servicio no arranca."""
    return _cargar_tope(VARIABLE_DE_SUBIDAS_EN_PROCESO, "subidas")
