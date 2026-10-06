"""Chequeo de compatibilidad del par plataforma/jax, al arrancar.

El 2026-10-05 una plataforma que esperaba ``f2-c.domain.6`` arranco contra un jax
que exponia ``.7``: ``api.governed_chat`` fallo cerrado en CADA turno, sin log, y el
usuario veia «No se pudo conectar con la faceta». El par es un limite exacto, no un
rango; si no coincide, el servicio no tiene nada util que servir y tiene que
fallar en el arranque (systemd lo marca failed), no en cada chat.

Compara versiones (F2-C, F2-D, F2-E y las de pipeline-list) y ademas hace una prueba de
humo real: sella y mintea una unidad de transporte sintetica. Solo usa modulos de
``JAX_REPO_PATH``; no toca la base ni la red.
"""
from __future__ import annotations

import logging
import types

logger = logging.getLogger(__name__)


class ParJaxIncompatible(RuntimeError):
    """El jax configurado no es el par con el que esta plataforma fue revisada."""


def verificar_par_jax() -> None:
    """Carga los tres puentes del par; levanta ParJaxIncompatible si alguno falla.

    El mensaje de cada puente ya dice la version esperada y la encontrada.
    """
    from api.governed_chat import GovernedChatUnavailable, _core, _lifecycle_core
    from api.governed_pipeline_list import GovernedPipelineListUnavailable, _paired_core
    from jax_engine.status_resolution import (
        RuntimeStatusBridgeUnavailable, _jax_runtime_status_bridge,
    )

    comprobaciones = (
        ("F2-C renderer/domain", _core, GovernedChatUnavailable),
        ("F2-D lifecycle", _lifecycle_core, GovernedChatUnavailable),
        ("F2-E runtime-status", _jax_runtime_status_bridge, RuntimeStatusBridgeUnavailable),
        ("pipeline-list (F2-C estructurado, F2-D bytes)", _paired_core,
         GovernedPipelineListUnavailable),
    )
    for nombre, cargar, error in comprobaciones:
        try:
            cargar()
        except error as exc:
            causa = f"{exc} (causa: {exc.__cause__!r})" if exc.__cause__ else str(exc)
            logger.critical("par plataforma/jax incompatible en %s: %s", nombre, causa)
            raise ParJaxIncompatible(f"{nombre}: {causa}") from exc
    _prueba_de_humo()
    logger.info("par plataforma/jax compatible (F2-C, F2-D, F2-E, pipeline-list)")


def _prueba_de_humo() -> None:
    """Un turno sintetico por el puente F2-C/F2-D: tiene que producir ``transport_unit``.

    Las versiones iguales no prueban que el par funcione; esto si. Alcance sintetico,
    sin proveedor, base ni red.
    """
    from api.governed_chat import project_provider_contract

    alcance = types.SimpleNamespace(
        tenant_id="par-jax-humo", project_id=None,
        subject_user_id="par-jax-humo", actor_principal="par-jax-humo")
    proyeccion = project_provider_contract(
        None, memory_scope=alcance, user_id="par-jax-humo",
        request_id="par-jax-humo", trace_id="par-jax-humo")
    if proyeccion.transport_unit is None:
        msg = ("prueba de humo: el puente F2-C/F2-D no produjo transport_unit "
               "(ver el error previo de api.governed_chat)")
        logger.critical(msg)
        raise ParJaxIncompatible(msg)
