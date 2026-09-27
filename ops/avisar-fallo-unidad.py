#!/usr/bin/env python3
"""Aviso mínimo de fallo de una unidad systemd -- SOLO biblioteca estándar
(MAJOR-2, segunda auditoría adversarial, 2026-09-27).

Se invoca desde `OnFailure=` de `jax-catalogo-modelos.service`, con
`/usr/bin/python3 -I` (aislado: ignora PYTHONPATH/PYTHONHOME y no busca el
directorio del script en sys.path para paquetes de sitio) -- es el ÚLTIMO
recurso para los fallos que el propio ejecutor (catalogo_modelos_ejecutor.py)
NO puede avisar por sí mismo:

- Un SIGTERM por TimeoutStartSec matando el proceso ANTES de que llegue a su
  propio try/except (no hay tiempo ni CPU para que Python corra el
  `except` -- systemd ya lo mató).
- Un .venv roto (una dependencia faltante, un intérprete corrupto) que ni
  siquiera deja arrancar el Python de la app.

Por eso este script NO importa nada de jax-platform, no usa httpx ni el
.venv -- sólo `os`, `sys` y `urllib` de la biblioteca estándar de CPython,
que existen en cualquier instalación de Python 3 del sistema.

El token del bot NUNCA viaja como argumento de otro proceso: se lee del
entorno (`TELEGRAM_BOT_TOKEN`, poblado por `EnvironmentFile=/etc/jax/.env`
de la propia unidad) DENTRO de este proceso Python, y se usa directo en la
llamada a `urllib.request` -- nunca se arma un comando `curl` con la URL
como argumento, que lo dejaría visible para cualquiera que corra `ps`
mientras el aviso está en vuelo.

Tercera auditoría adversarial (2026-09-27), dos cambios sobre la versión
anterior:

1. Ya NO lee el journal. `jaxsvc` no tiene permiso de leerlo (haría falta
   pertenecer a `systemd-journal` o ser superusuario) -- depender de eso era
   una VÍA DE FUGA: si algún día se le diera ese permiso, el contenido del
   journal (que puede incluir salida de otras unidades, no sólo la del
   catálogo) viajaría a Telegram sin ningún control adicional de este
   script. El aviso ahora es sólo "esta unidad terminó de una forma que el
   propio ejecutor no pudo avisar", con los datos que systemd YA exporta --
   sin intentar leer algo que este script no tiene forma segura de leer.

2. Sólo avisa si `MONITOR_SERVICE_RESULT` -- que systemd exporta a toda
   unidad de `OnFailure=`, junto con `MONITOR_EXIT_CODE`,
   `MONITOR_EXIT_STATUS` y `MONITOR_INVOCATION_ID` (ver systemd.exec(5) /
   systemd.unit(5), sección de manejo de fallos) -- es DISTINTO de
   `exit-code`. Con `exit-code` el proceso SÍ llegó a correr y terminar con
   un código de salida propio, que es justo el camino que
   `catalogo_modelos_ejecutor.py::main()` ya cubre desde ADENTRO (con
   contexto real: qué proveedor falló, qué faceta quedó en riesgo) -- avisar
   de nuevo acá sería un segundo aviso, más pobre, del mismo hecho. Esta
   unidad es el backstop para lo que NUNCA llega a ese código: `timeout`
   (`TimeoutStartSec` mató el proceso), `signal` (una señal externa lo
   tumbó), `core-dump`, `watchdog`, `start-limit-hit`, o que ni siquiera
   pudo arrancar (`exec`, `protocol`, etc.). Si la variable viniera vacía o
   ausente (una versión de systemd vieja que no la exporta, o correrlo a
   mano) se trata como "no es exit-code": mejor un aviso de más que uno de
   menos en un camino que sólo se dispara cuando algo ya salió mal.
"""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
TIMEOUT_SEGUNDOS = 10

#: Las cuatro variables que systemd exporta a toda unidad de `OnFailure=`
#: (ver systemd.unit(5)) -- las que pide la tercera auditoría adversarial
#: (2026-09-27).
MONITOR_SERVICE_RESULT_ENV = "MONITOR_SERVICE_RESULT"
MONITOR_EXIT_CODE_ENV = "MONITOR_EXIT_CODE"
MONITOR_EXIT_STATUS_ENV = "MONITOR_EXIT_STATUS"
MONITOR_INVOCATION_ID_ENV = "MONITOR_INVOCATION_ID"

#: Con este resultado, `catalogo_modelos_ejecutor.py::main()` YA corrió
#: hasta el final de su propio try/except y ya avisó lo que había que avisar
#: (o decidió, correctamente, que no había nada que avisar) -- esta unidad
#: se queda callada para no duplicar ese aviso con MENOS contexto.
RESULTADO_YA_CUBIERTO_POR_EL_EJECUTOR = "exit-code"


def _enviar_telegram(mensaje: str) -> bool:
    """Devuelve True sólo si Telegram respondió 200. Sin variables de
    entorno, False sin intentar nada."""
    token = os.environ.get(TELEGRAM_TOKEN_ENV, "").strip()
    chat_id = os.environ.get(TELEGRAM_CHAT_ID_ENV, "").strip()
    if not token or not chat_id:
        return False

    datos = urllib.parse.urlencode({"chat_id": chat_id, "text": mensaje}).encode("ascii")
    peticion = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=datos,
        method="POST",
    )
    try:
        with urllib.request.urlopen(peticion, timeout=TIMEOUT_SEGUNDOS) as resp:
            return resp.status == 200
    except Exception:  # fail-soft: sin red o con Telegram caído, este último recurso también sale en rojo, no revienta
        return False


def main(argv: list[str]) -> int:
    if len(argv) < 2 or not argv[1].strip():
        print("uso: avisar-fallo-unidad.py <unidad>", file=sys.stderr)
        return 2
    unidad = argv[1].strip()

    resultado = os.environ.get(MONITOR_SERVICE_RESULT_ENV, "").strip()
    if resultado == RESULTADO_YA_CUBIERTO_POR_EL_EJECUTOR:
        return 0

    codigo = os.environ.get(MONITOR_EXIT_CODE_ENV, "").strip() or "?"
    estado = os.environ.get(MONITOR_EXIT_STATUS_ENV, "").strip() or "?"
    invocation_id = os.environ.get(MONITOR_INVOCATION_ID_ENV, "").strip() or "?"

    mensaje = (
        f"Catálogo de modelos: la unidad {unidad} terminó de una forma que el "
        "propio ejecutor no pudo avisar (lo mató systemd, o ni llegó a "
        f"correr). MONITOR_SERVICE_RESULT={resultado or '?'} "
        f"MONITOR_EXIT_CODE={codigo} MONITOR_EXIT_STATUS={estado} "
        f"MONITOR_INVOCATION_ID={invocation_id}."
    )
    return 0 if _enviar_telegram(mensaje) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
