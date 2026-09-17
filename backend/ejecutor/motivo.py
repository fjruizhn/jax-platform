"""ESPEJO de `Motivo` de jax/ejecutor/cita.py (familia `motivo` de jax
scripts/check_mirror_sync.py). Lo usa backend/ejecutor/prioridad.py, copia de
jax/ejecutor/prioridad.py. Este archivo no tiene otro símbolo: lo exige
tests/test_carril_mesa.py."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Motivo:
    """Por qué algo no vale: un CÓDIGO estable y sus datos, como pares
    `(clave, valor)` (inmutables; `dict(motivo.datos)` para serializar). Sin
    prosa: la frase la pone el frontend con sus traducciones.

    Vive aquí, en el módulo más bajo del Ejecutor (no importa a nadie), para
    que lo compartan `hechos`, `herramientas` y `transporte` sin ciclos:
    `hechos` importa `captura`, que importa `cita`."""
    codigo: str
    datos: tuple[tuple[str, object], ...] = ()
