"""ESPEJO de la pausa del Ejecutor de jax (jax/ejecutor/contratos/pausa.py), familia
`pausa_ejecutor` de jax/scripts/check_mirror_sync.py: VARIABLE_RUTA, PausaSinConfigurar,
pausa_puesta, _sincronizar_directorio y poner_pausa son copia verbatim (los compara ese
script). No se importa jax: api/chat.py pone en sys.path el checkout de producción de jax,
que puede ir detrás, y el runner de CI de esta plataforma no lo tiene (mismo motivo que
prioridad.py).

La plataforma es el kill switch del modo Ejecutor (SP2, 2026-09-17): PONE la pausa del
Ejecutor —no el interruptor global de JAX— y la QUITA cuando un superadmin, habiendo leído
el motivo, lo decide. El proxy de C3 responde 423 mientras exista; el arranque no abre una
misión con ella puesta. Lo propio de la plataforma: `ruta_de_la_pausa` (desde su entorno),
`leer_pausa` (fail-closed) y `quitar_pausa`.

Sólo biblioteca estándar.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


VARIABLE_RUTA = "JAX_EJECUTOR_PAUSA"


class PausaSinConfigurar(RuntimeError):
    """La variable falta, está vacía o no es una ruta absoluta. `args[0]` es la variable."""


def ruta_de_la_pausa() -> Path:
    valor = os.environ.get(VARIABLE_RUTA, "").strip()
    if not valor or not Path(valor).is_absolute():
        raise PausaSinConfigurar(VARIABLE_RUTA)
    return Path(valor)


def pausa_puesta(ruta) -> bool:
    try:
        os.stat(ruta)
    except FileNotFoundError:
        return False
    except OSError:  # fail-closed: sin poder mirar la pausa (permiso, ENOTDIR, E/S) se la da por PUESTA
        return True
    return True


def _sincronizar_directorio(directorio: Path) -> None:
    descriptor = os.open(directorio, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def poner_pausa(ruta: Path, datos: dict) -> bool:
    """Pone la pausa. True si la puso esta llamada; False si ya estaba (no la pisa).
    Un error de E/S se propaga: quien llama no puede creer que frenó."""
    ruta = Path(ruta)
    contenido = json.dumps({**datos, "momento": datetime.now(timezone.utc).isoformat()},
                           ensure_ascii=True, sort_keys=True)
    descriptor, temporal = tempfile.mkstemp(prefix=".pausa-", dir=ruta.parent)
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
        _sincronizar_directorio(ruta.parent)
        return True
    finally:
        os.unlink(temporal)


def quitar_pausa(ruta) -> bool:
    """Quita la pausa. True si la quitó esta llamada; False si no estaba. Un error de E/S se
    propaga (la pausa sigue puesta): quien llama no puede creer que soltó el freno."""
    ruta = Path(ruta)
    try:
        os.unlink(ruta)
    except FileNotFoundError:
        return False
    _sincronizar_directorio(ruta.parent)
    return True


def leer_pausa(ruta) -> dict:
    """La pausa como datos para la pantalla. Fail-closed como `pausa_puesta`: existe pero no se
    puede leer → PUESTA con `legible=False`. Un campo con un tipo inesperado se omite (None),
    no se inventa."""
    vacia = {"origen": None, "motivo": None, "paso": None, "momento": None}
    if not pausa_puesta(ruta):
        return {"puesta": False, "legible": True, **vacia}
    try:
        doc = json.loads(Path(ruta).read_bytes())
    except (OSError, ValueError):  # fail-soft sobre la LECTURA del motivo; la pausa se informa PUESTA (fail-closed)
        return {"puesta": True, "legible": False, **vacia}
    if not isinstance(doc, dict):
        return {"puesta": True, "legible": False, **vacia}
    texto = {k: doc[k] if isinstance(doc.get(k), str) else None for k in ("origen", "motivo", "momento")}
    paso = doc.get("paso")
    paso = paso if isinstance(paso, int) and not isinstance(paso, bool) else None
    return {"puesta": True, "legible": True, **texto, "paso": paso}
