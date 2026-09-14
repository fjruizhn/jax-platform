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
