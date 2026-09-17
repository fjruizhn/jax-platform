"""Rutas de datos desde el entorno (frente A, A-55, 2026-09-16).

Una sola forma de leerlas, fail-closed: sin la variable, o con una ruta
relativa, el módulo que la pide no se importa y el servicio no arranca. Un
default relativo a $HOME ataba el backend a la máquina de Fernando y, en los
tests, hacía escribir en ~/jax/missions REAL (test_command_path_traversal)."""
import os
from pathlib import Path


def ruta_requerida(nombre: str) -> Path:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise RuntimeError(f"{nombre} no configurada en /etc/jax/.env (ruta absoluta obligatoria)")
    ruta = Path(valor)
    if not ruta.is_absolute():
        raise RuntimeError(f"{nombre} tiene que ser una ruta absoluta, no {valor!r}")
    return ruta
