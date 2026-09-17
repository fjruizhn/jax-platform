"""El interruptor de JAX (kill switch), copia de jax-platform.

Espejo de `jax/core/interruptor.py` (familia `interruptor` de
scripts/check_mirror_sync.py en el repo jax). La plataforma es el único
ESCRITOR del freno; LAS MANOS, Jacobs y el REPL lo leen con el canónico. Los
dos lados tienen que hablar del MISMO archivo con la MISMA semántica: sólo
"no existe" es SUELTO, y la ruta heredada `RUTA_HEREDADA` también frena
(requisito del controlador principal del frente B, 2026-09-17). La
plataforma decide sus escrituras con `pausa_presente` (sólo su archivo) e
informa con `interruptor_activo`; nunca borra la ruta heredada.
`correr_con_interruptor` no está acá: es del REPL y de Jacobs.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

VARIABLE_RUTA = "JAX_KILL_SWITCH_PATH"

logger = logging.getLogger(__name__)

# Compatibilidad, NO configuración: es la ruta que la gente y los scripts ya
# usan para pausar a JAX (requisito del controlador principal del frente B,
# 2026-09-17). Por eso es una constante y no una variable de entorno: una
# variable que la pisara en producción apagaría la compatibilidad en silencio
# (ruling R15). Los tests la desvían con monkeypatch desde el conftest.
RUTA_HEREDADA = Path("/etc/jax/PAUSE")

# Anti-spam del WARNING, por proceso (ruling R12): True desde que se avisó de
# la heredada hasta que una lectura la encuentra ausente.
_heredada_avisada = False


class InterruptorSinConfigurar(RuntimeError):
    """JAX_KILL_SWITCH_PATH falta, está vacía o no es una ruta absoluta."""


def ruta_del_interruptor() -> Path:
    valor = os.environ.get(VARIABLE_RUTA, "").strip()
    if not valor:
        raise InterruptorSinConfigurar(
            f"{VARIABLE_RUTA} no está definida: sin saber dónde está el freno "
            "no se ejecuta nada (se define en /etc/jax/.env)"
        )
    ruta = Path(valor)
    if not ruta.is_absolute():
        raise InterruptorSinConfigurar(
            f"{VARIABLE_RUTA} tiene que ser una ruta absoluta, no {valor!r}"
        )
    return ruta


def pausa_presente(ruta: Path | str) -> bool:
    """¿Está puesto ESTE archivo? Sólo "no existe" es False."""
    try:
        os.stat(ruta)
    except FileNotFoundError:
        return False
    except OSError:  # fail-closed: sin poder mirar el freno (permiso, ENOTDIR, E/S) se lo da por PUESTO
        return True
    return True


def _heredada_activa() -> bool:
    """La ruta heredada, con el WARNING una vez por episodio. El aviso nunca
    decide la lectura: lo que devuelve es siempre `pausa_presente`."""
    global _heredada_avisada
    presente = pausa_presente(RUTA_HEREDADA)
    if not presente:
        _heredada_avisada = False
        return False
    if not _heredada_avisada:
        _heredada_avisada = True
        nueva = os.environ.get(VARIABLE_RUTA, "").strip() or f"({VARIABLE_RUTA} sin definir)"
        logger.warning(
            "kill switch: la ruta VIEJA %s está puesta (o no se puede mirar) y JAX "
            "queda FRENADO por compatibilidad. La ruta del freno es %s: quitá %s "
            "en el servidor para soltarlo.",
            RUTA_HEREDADA, nueva, RUTA_HEREDADA,
        )
    return True


def interruptor_activo(ruta: Path | str | None = None) -> bool:
    objetivo = Path(ruta) if ruta is not None else ruta_del_interruptor()
    return pausa_presente(objetivo) or _heredada_activa()


def _sincronizar_directorio(directorio: Path) -> None:
    descriptor = os.open(directorio, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def escribir_pausa(ruta: Path, contenido: str) -> bool:
    """Pone el freno. True si lo puso esta llamada; False si ya estaba."""
    directorio = ruta.parent
    descriptor, temporal = tempfile.mkstemp(prefix=".pausa-", dir=directorio)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.chmod(temporal, 0o660)
        try:
            os.link(temporal, ruta)
        except FileExistsError:
            return False
        _sincronizar_directorio(directorio)
        return True
    finally:
        os.unlink(temporal)


def borrar_pausa(ruta: Path) -> bool:
    """Quita el freno. True si lo quitó esta llamada; False si no estaba."""
    try:
        os.unlink(ruta)
    except FileNotFoundError:
        return False
    _sincronizar_directorio(ruta.parent)
    return True
