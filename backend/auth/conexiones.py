"""Corte de conexiones vivas (2026-09-15, admin usuarios etapa 3/4, Ruling U5).

Módulo neutro: lo usan `api/admin/users.py` (rol/estado, baja, revocación) y
`api/auth.py` (Mi cuenta y el reset por enlace, que suben `token_version` y
tienen que cerrar lo que ya estaba abierto con la versión vieja). Antes vivía
en `api/admin/users.py`, y `api/auth.py` lo importaba de ahí -- cuando
`admin/users.py` empezó a necesitar `api.auth` (reset-link, etapa 4 Task 3)
eso cerraba un ciclo import-time frágil (rompía según qué módulo cargara
primero: medido con `pytest tests/test_admin_usuarios_guardas.py` en rojo,
`ImportError: cannot import name '_cortar_conexiones' from partially
initialized module`). `auth/conexiones.py` no importa ni `api.auth` ni
`api.admin.users`, así que ninguno de los dos lo puede volver a formar.
"""
import logging

from api.events import close_user_streams
from jax_engine.websocket_hub import ws_hub

logger = logging.getLogger(__name__)


async def _cortar_conexiones(user_id: int) -> None:
    """Cierra el WS (4001) y los streams SSE ya abiertos del usuario. Se llama
    DESPUÉS del commit, nunca dentro de la transacción: un rollback no debe
    haber cortado sesiones. Cada canal por separado y tolerante: el cambio ya
    está confirmado, así que un fallo acá no puede volverse un 500 de algo que
    sí se hizo."""
    try:
        await ws_hub.close_user(str(user_id))
    except Exception:  # fail-soft: el cambio ya está confirmado; cortar conexiones es best-effort (la sesión igual muere en el request siguiente por token_version)
        logger.exception("No se pudieron cerrar los WebSocket del usuario %s", user_id)
    try:
        await close_user_streams(str(user_id))
    except Exception:  # fail-soft: el cambio ya está confirmado; cortar conexiones es best-effort (el SSE reconecta y verificar_sesion lo rechaza)
        logger.exception("No se pudieron cerrar los streams SSE del usuario %s", user_id)
