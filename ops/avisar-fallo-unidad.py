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
.venv -- sólo `os`, `subprocess`, `sys` y `urllib` de la biblioteca estándar
de CPython, que existen en cualquier instalación de Python 3 del sistema.

El token del bot NUNCA viaja como argumento de otro proceso: se lee del
entorno (`TELEGRAM_BOT_TOKEN`, poblado por `EnvironmentFile=/etc/jax/.env`
de la propia unidad) DENTRO de este proceso Python, y se usa directo en la
llamada a `urllib.request` -- nunca se arma un comando `curl` con la URL
como argumento, que lo dejaría visible para cualquiera que corra `ps`
mientras el aviso está en vuelo.
"""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
LINEAS_DE_JOURNAL = 20
TIMEOUT_SEGUNDOS = 10


def _ultimas_lineas_de_journal(unidad: str) -> str | None:
    """Best-effort: sin `journalctl` en el PATH, sin permiso de leerlo, o si
    no imprime nada, se devuelve None -- el aviso sale igual, sin esas
    líneas. Nunca revienta el aviso principal por esto."""
    try:
        resultado = subprocess.run(
            ["journalctl", "-u", unidad, "-n", str(LINEAS_DE_JOURNAL), "--no-pager", "--output=cat"],
            capture_output=True, text=True, timeout=TIMEOUT_SEGUNDOS, check=False,
        )
    except Exception:  # fail-soft: sin journalctl/permiso, el aviso principal sale igual, sin estas líneas
        return None
    if resultado.returncode != 0 or not resultado.stdout.strip():
        return None
    return resultado.stdout.strip()


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

    mensaje = f"Catálogo de modelos: falló la unidad {unidad}."
    journal = _ultimas_lineas_de_journal(unidad)
    if journal:
        # Recorte defensivo: Telegram tiene un tope de ~4096 caracteres por
        # mensaje; el journal completo de 20 líneas normalmente entra de
        # sobra, pero una línea gigante no debe tumbar el envío.
        mensaje += f"\n\nÚltimas líneas del journal:\n{journal[-3000:]}"

    return 0 if _enviar_telegram(mensaje) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
