"""Credencial de servicio de jax-platform ante LAS MANOS (2026-09-17).

LAS MANOS (repo jax, `las_manos/auth_servicio.py`) exige credencial en toda
ruta salvo `GET /health`, y la identidad del llamador sale de la credencial, no
del cuerpo. jax-platform es la identidad `plataforma`: la única que puede crear,
reanudar, aprobar y cancelar pipelines de la Mesa y consultar el gate de facetas.

Todo pedido de este backend a LAS MANOS (salvo /health) pasa
`headers=encabezados_las_manos()`. No va como cabecera por defecto del cliente
HTTP compartido: ese cliente también habla con Ollama y con proveedores de
afuera, y la credencial no puede salir de la máquina.

Fail-closed: sin la variable (o con un valor que no puede ser una credencial)
levanta EntornoInvalido y el pedido no sale. Se lee en cada llamada (un
os.environ.get: sin caché que invalidar); cambiarla exige reiniciar el servicio
igual, porque el entorno viene de /etc/jax/.env vía systemd.

`ENCABEZADO` y `VARIABLE` tienen que coincidir con `ENCABEZADO` y
`VARIABLES["plataforma"]` de jax/las_manos/auth_servicio.py.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import os
import re

from config_entorno import EntornoInvalido

ENCABEZADO = "X-Jax-Credencial-Servicio"
VARIABLE = "JAX_LAS_MANOS_CREDENCIAL_PLATAFORMA"

#: El mismo mínimo que exige LAS MANOS: secrets.token_urlsafe(32).
LARGO_MINIMO = 43
_ALFABETO = re.compile(r"^[A-Za-z0-9_\-]+$")


def encabezados_las_manos() -> dict[str, str]:
    valor = os.environ.get(VARIABLE, "").strip()
    if not valor:
        raise EntornoInvalido(
            f"{VARIABLE} no está seteada: agregala a /etc/jax/.env (sin default silencioso)."
        )
    if len(valor) < LARGO_MINIMO or not _ALFABETO.fullmatch(valor):
        raise EntornoInvalido(
            f"{VARIABLE} tiene que tener al menos {LARGO_MINIMO} caracteres [A-Za-z0-9_-]."
        )
    return {ENCABEZADO: valor}
