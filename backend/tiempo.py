"""
Hora UTC sin zona -- reemplazo de `datetime.utcnow()` (2026-09-14).

`utcnow()` está deprecado desde Python 3.12. El reemplazo obvio,
`datetime.now(timezone.utc)`, NO sirve tal cual: devuelve una hora CON zona, y
este backend la compara contra columnas DATETIME que aiomysql entrega SIN zona
(`password_reset_tokens.expires_at`, `jax_users.locked_until`). Una comparación
entre las dos lanza TypeError, que rompería el reset de contraseña y el bloqueo
por intentos. Por eso se le quita la zona: misma semántica exacta que utcnow().

Un solo lugar para esto, y el guard de tests/test_utc_ahora.py impide que
`utcnow` vuelva a aparecer en el backend.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_ahora() -> datetime:
    """Hora UTC actual, sin tzinfo (lo mismo que devolvía `datetime.utcnow()`)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(valor) -> str | None:
    """Fecha para el navegador: ISO 8601 con `+00:00` explícito y milisegundos
    (2026-09-15, admin usuarios etapa 3).

    La sesión de MariaDB de la app corre en `SYSTEM` = CST (UTC-6, medido el
    2026-09-15): un `isoformat()` sin zona lo lee `new Date()` como hora local
    del navegador. Acepta:
    - una hora UTC SIN zona (`utc_ahora()`, o una columna escrita con
      `UTC_TIMESTAMP()`): se le pone la zona UTC;
    - un epoch de `UNIX_TIMESTAMP()` (int para TIMESTAMP, Decimal para
      DATETIME(6)): para columnas TIMESTAMP es la única lectura que no pasa
      por la zona de la sesión.
    NUNCA una hora de `NOW()`/`CURRENT_TIMESTAMP` leída como DATETIME: esa es
    hora CST sin zona y saldría 6 h corrida.
    """
    if valor is None:
        return None
    if isinstance(valor, datetime):
        dt = valor.replace(tzinfo=timezone.utc)
    else:
        dt = datetime.fromtimestamp(float(valor), timezone.utc)
    return dt.isoformat(timespec="milliseconds")
