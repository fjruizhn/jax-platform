"""Funciones de nivel de módulo para los tests de adjuntos/pdf_pool.py
(RD1, 2026-09-17).

Tienen que vivir en un módulo importable por nombre (no adentro de una
función de test): el worker del ProcessPoolExecutor corre en OTRO proceso
(mp_context="spawn") y reconstruye la llamada re-importando la función por su
`__module__`/`__qualname__` -- una función definida dentro de un `def test_...`
no es alcanzable así."""
import os
import time


def dormir(datos: bytes, max_paginas: int, max_chars: int) -> tuple[str, bool]:
    """Simula un PDF patológico que pypdf nunca termina de leer. El valor de
    sleep es mucho mayor que cualquier timeout usado en los tests (todos <= 3
    s): lo que importa es que el proceso siga vivo hasta que el test lo mate
    a propósito (extraer_texto_en_pool, tras el timeout)."""
    time.sleep(120)
    return "nunca-llega", False


def dormir_reportando_pid(datos: bytes, max_paginas: int, max_chars: int) -> tuple[str, bool]:
    """Como `dormir`, pero antes de dormir escribe su propio pid en la ruta
    que llega en `datos` (un path de texto, no un PDF real) -- así el test
    puede confirmar que ESE proceso, y no otro, es el que termina de verdad
    (ronda de corrección 1, item 3: os.kill(pid, 0) después de reciclar)."""
    with open(datos.decode(), "w") as f:
        f.write(str(os.getpid()))
        f.flush()
    time.sleep(120)
    return "nunca-llega", False


def morir(datos: bytes, max_paginas: int, max_chars: int) -> tuple[str, bool]:
    """Simula un worker que muere sin avisar (OOM real, segfault de pypdf):
    os._exit no ejecuta ningún cleanup, así que el ProcessPoolExecutor lo ve
    como el proceso murió abruptamente y queda BrokenProcessPool (ronda de
    corrección 1, item 1; verificado con probe_pool.py contra este mismo
    venv antes de escribir el fix)."""
    os._exit(1)


def pid_como_texto(datos: bytes, max_paginas: int, max_chars: int) -> tuple[str, bool]:
    """Devuelve el pid del worker COMO SI fuera el texto extraído -- permite
    verificar, a través de `extraer_texto_en_pool` (el camino real, no el
    pool en crudo), que la extracción corrió en otro proceso (ronda de
    corrección 1, item 6)."""
    return str(os.getpid()), False
