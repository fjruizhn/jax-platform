"""Leer `/etc/jax/.env` para la suite, sin abrirlo directo.

Desde el 2026-09-17 el archivo es `root:jaxsvc 640`: lo lee la cuenta de servicio (o root), no el
operador. La suite lo obtiene con `sudo -n cat`. `-n` a propósito: un sudo que pidiera contraseña
colgaría la corrida esperando una respuesta que nadie va a escribir; así falla en el acto.

Vive en su propio módulo, no dentro de `conftest.py`, porque un test que importara el conftest lo
volvería a EJECUTAR: re-envolvería `builtins.open` sobre la versión ya envuelta y ensuciaría la
sesión entera (45 tests de otros archivos en rojo, visto el 2026-09-17).
"""
from __future__ import annotations

import subprocess

RUTA = "/etc/jax/.env"


def parsear(texto: str) -> dict:
    env = {}
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            env[k.strip()] = v.strip()
    return env


def _abrir_directo(ruta: str) -> str:
    with open(ruta) as f:
        return f.read()


def cargar(ruta: str = RUTA, *, abrir=_abrir_directo, corredor=subprocess) -> dict:
    """El entorno de producción como diccionario. `{}` si el archivo no está (el runner de CI es
    otro entorno, no un error). Si está y no se puede leer, se dice: seguir sin credenciales haría
    fallar la suite mucho más lejos y con otro síntoma."""
    try:
        return parsear(abrir(ruta))
    except FileNotFoundError:  # fail-soft: no está -> otro entorno (CI), no un error
        return {}
    except PermissionError:  # fail-soft: no es 'no se puede leer', es 'no es mío': abajo se reintenta con sudo -n y ahí sí se falla ruidoso
        pass
    r = corredor.run(["sudo", "-n", "cat", ruta], capture_output=True, text=True)
    if r.returncode == 0:
        return parsear(r.stdout)
    if "No such file" in r.stderr:  # fail-soft: igual que arriba
        return {}
    raise RuntimeError(
        f"no se pudo leer {ruta}: es root:jaxsvc 640 y `sudo -n` falló ({r.stderr.strip()[:120]}). "
        "Corré la suite con sudo sin contraseña, o como la cuenta de servicio.")
