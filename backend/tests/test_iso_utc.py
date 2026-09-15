"""Fechas hacia el navegador con zona explícita (2026-09-15, admin usuarios
etapa 3, ronda de arreglos de la Task 4).

La sesión de MariaDB corre en `SYSTEM` = CST (UTC-6): una fecha sin zona
serializada con `isoformat()` la lee `new Date()` como hora LOCAL del
navegador. `iso_utc` siempre agrega `+00:00` (y milisegundos: el formato que
todo navegador parsea). Recibe una hora UTC sin zona (lo que devuelve
`utc_ahora()` y lo que se guarda con `UTC_TIMESTAMP()`) o un epoch de
`UNIX_TIMESTAMP()` (int para TIMESTAMP, Decimal para DATETIME(6)).
"""
from datetime import datetime
from decimal import Decimal

from tiempo import iso_utc


def test_iso_utc_de_una_hora_utc_sin_zona():
    assert iso_utc(datetime(2026, 9, 15, 9, 36, 47, 581184)) == "2026-09-15T09:36:47.581+00:00"


def test_iso_utc_de_un_epoch_de_unix_timestamp():
    assert iso_utc(0) == "1970-01-01T00:00:00.000+00:00"
    assert iso_utc(Decimal("1.500000")) == "1970-01-01T00:00:01.500+00:00"


def test_iso_utc_de_null_es_none():
    assert iso_utc(None) is None
