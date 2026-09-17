"""Funciones de nivel de módulo para los tests de adjuntos/pdf_pool.py
(RD1, 2026-09-17).

Tienen que vivir en un módulo importable por nombre (no adentro de una
función de test): el worker del ProcessPoolExecutor corre en OTRO proceso
(mp_context="spawn") y reconstruye la llamada re-importando la función por su
`__module__`/`__qualname__` -- una función definida dentro de un `def test_...`
no es alcanzable así."""
import time


def dormir(datos: bytes, max_paginas: int, max_chars: int) -> tuple[str, bool]:
    """Simula un PDF patológico que pypdf nunca termina de leer. El valor de
    sleep es mucho mayor que cualquier timeout usado en los tests (todos <= 3
    s): lo que importa es que el proceso siga vivo hasta que el test lo mate
    a propósito (extraer_texto_en_pool, tras el timeout)."""
    time.sleep(120)
    return "nunca-llega", False
