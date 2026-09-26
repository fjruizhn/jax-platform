"""Credenciales de base de datos para la suite, desde un archivo LOCAL de
pruebas -- nunca desde `/etc/jax/.env` (producción).

Reemplaza `entorno_de_produccion.py` (retirado 2026-09-25, PASO 0): ese
módulo leía el `.env` de PRODUCCIÓN con `sudo -n cat` en cada corrida de
pytest y volcaba TODAS sus claves al proceso -- entre ellas la contraseña de
la base de producción, el secreto JWT, la clave Fernet y el token de
Telegram. Ningún test necesita esos valores: la base la resuelve
`base_de_test.py` contra su propia base con sufijo, y el JWT/la Fernet de la
sesión son siempre efímeros (ver `conftest.py`).

Este módulo sólo sabe leer un archivo de credenciales DE PRUEBA, con un
usuario dedicado (`jax_test`) que en hall9000 sólo tiene permisos sobre
`jax_memory_test` y `jax_memory_test_%` -- nunca sobre `jax_memory`
(producción), verificado con `SHOW GRANTS FOR CURRENT_USER()`. La ruta por
defecto es `~/.config/jax/test-db.env`, movible con `JAX_TEST_ENV_FILE`.
Sólo se leen las claves de `CLAVES_PERMITIDAS`: cualquier otra línea del
archivo se ignora en silencio -- este mecanismo no es un cargador de entorno
genérico. Si algún día hace falta una clave nueva se agrega a esa lista a
propósito, nunca por default, y nunca un secreto de sesión (ver
`test_conftest_sin_produccion.py`).

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Lista blanca. Deliberadamente NO incluye JAX_JWT_SECRET, FERNET_KEY ni
#: ningún token: esos son siempre efímeros, generados por la sesión (ver
#: conftest.py), nunca leídos de un archivo.
CLAVES_PERMITIDAS = ("JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_USER", "JAX_DB_PASSWORD", "JAX_DB_NAME")

#: `JAX_DB_NAME` (si el archivo la trae) queda sobrescrita igual por
#: `base_de_test.fijar_base_de_test()`, que corre después en `conftest.py` y
#: la fija incondicionalmente a la base de esta sesión.
RUTA_POR_DEFECTO = "~/.config/jax/test-db.env"

#: La variable que elige la ruta del archivo. Sin ella, `RUTA_POR_DEFECTO`.
VARIABLE_DE_LA_RUTA = "JAX_TEST_ENV_FILE"


def parsear(texto: str) -> dict:
    env = {}
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        if clave in CLAVES_PERMITIDAS:
            env[clave] = valor.strip()
    return env


def cargar(ruta: str | None = None) -> dict:
    """Las credenciales de base de PRUEBAS como diccionario. `{}` si el
    archivo no está -- es el caso normal en CI (que fija sus propias
    variables en `policy.yml`) y en cualquier máquina que no lo haya
    configurado. Sin `sudo`, sin reintento: es un archivo del propio
    operador, no uno `root:jaxsvc 640` como el de producción. Un archivo que
    SÍ existe y no se puede leer (permisos rotos) sube el error tal cual: es
    una máquina mal configurada, no un caso normal a silenciar."""
    if ruta is None:
        ruta = os.environ.get(VARIABLE_DE_LA_RUTA, RUTA_POR_DEFECTO)
    ruta_resuelta = Path(ruta).expanduser()
    try:
        texto = ruta_resuelta.read_text(encoding="utf-8")
    except FileNotFoundError:  # fail-soft: no está -> CI, o una máquina sin configurar
        return {}
    return parsear(texto)
