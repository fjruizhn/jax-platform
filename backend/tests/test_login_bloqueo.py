"""Frente A, A-50 (2026-09-16): Login.jsx sacaba los minutos con una regex del
texto español "Cuenta bloqueada. Intenta de nuevo en N minuto(s)." Ahora el 423
lleva codigo estable, segundos y Retry-After (como el 429). Puro."""
from datetime import datetime, timedelta

from api.auth import _cuenta_bloqueada

AHORA = datetime(2026, 9, 16, 12, 0, 0)


def test_el_423_trae_codigo_segundos_y_retry_after():
    e = _cuenta_bloqueada(AHORA + timedelta(minutes=10), AHORA)
    assert (e.status_code, e.detail) == (423, {"code": "cuenta_bloqueada", "retry_after_seconds": 600})
    assert e.headers == {"Retry-After": "600"}


def test_una_fraccion_de_segundo_redondea_hacia_arriba_y_nunca_es_cero():
    assert _cuenta_bloqueada(AHORA + timedelta(milliseconds=200), AHORA).detail["retry_after_seconds"] == 1
    assert _cuenta_bloqueada(AHORA, AHORA).detail["retry_after_seconds"] == 1
