"""El interruptor de JAX (kill switch), copia de jax-platform.

Espejo de `jax/core/interruptor.py` (familia `interruptor` de
scripts/check_mirror_sync.py en el repo jax). La plataforma es el único
ESCRITOR del freno; LAS MANOS, Jacobs y el REPL lo leen con el canónico. Los
dos lados tienen que hablar del MISMO archivo con la MISMA semántica: sólo
"no existe" es SUELTO. `correr_con_interruptor` no está acá: es del REPL y
de Jacobs.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

VARIABLE_RUTA = "JAX_KILL_SWITCH_PATH"


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


def interruptor_activo(ruta: Path | str | None = None) -> bool:
    objetivo = Path(ruta) if ruta is not None else ruta_del_interruptor()
    try:
        os.stat(objetivo)
    except FileNotFoundError:
        return False
    except OSError:  # fail-closed: sin poder mirar el freno (permiso, ENOTDIR, E/S) se lo da por PUESTO
        return True
    return True


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
