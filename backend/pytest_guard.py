"""Detección común de la ejecución dentro de pytest."""
import os
import sys


def corriendo_bajo_pytest() -> bool:
    """Devuelve si pytest está ejecutando el proceso o el test actual."""
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules
