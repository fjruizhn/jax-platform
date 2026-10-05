"""Chequeo de compatibilidad del par plataforma/jax, al arrancar.

El 2026-10-05 una plataforma que esperaba ``f2-c.domain.6`` arranco contra un jax
que exponia ``.7``: ``api.governed_chat`` fallo cerrado en CADA turno, sin log, y el
usuario veia «No se pudo conectar con la faceta». El par es un limite exacto, no un
rango; si no coincide, el servicio no tiene nada util que servir y tiene que
fallar en el arranque (systemd lo marca failed), no en cada chat.

Solo lee modulos de ``JAX_REPO_PATH``; no toca la base ni la red.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ParJaxIncompatible(RuntimeError):
    """El jax configurado no es el par con el que esta plataforma fue revisada."""


def verificar_par_jax() -> None:
    """Carga los tres puentes del par; levanta ParJaxIncompatible si alguno falla.

    El mensaje de cada puente ya dice la version esperada y la encontrada.
    """
    from api.governed_chat import GovernedChatUnavailable, _core, _lifecycle_core
    from jax_engine.status_resolution import (
        RuntimeStatusBridgeUnavailable, _jax_runtime_status_bridge,
    )

    comprobaciones = (
        ("F2-C renderer/domain", _core, GovernedChatUnavailable),
        ("F2-D lifecycle", _lifecycle_core, GovernedChatUnavailable),
        ("F2-E runtime-status", _jax_runtime_status_bridge, RuntimeStatusBridgeUnavailable),
    )
    for nombre, cargar, error in comprobaciones:
        try:
            cargar()
        except error as exc:
            causa = f"{exc} (causa: {exc.__cause__!r})" if exc.__cause__ else str(exc)
            logger.critical("par plataforma/jax incompatible en %s: %s", nombre, causa)
            raise ParJaxIncompatible(f"{nombre}: {causa}") from exc
    logger.info("par plataforma/jax compatible (F2-C, F2-D, F2-E)")
