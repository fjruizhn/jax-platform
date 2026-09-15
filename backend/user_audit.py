"""Registro de acciones de administración de usuarios (2026-09-12, admin
usuarios etapa 3, spec §3.3).

`registrar` recibe el CURSOR de la transacción del cambio: o quedan el cambio
y su registro, o ninguno. Una acción fuera de ACCIONES es un error de
programación y lanza antes de escribir. La IP la pone quien llama con
auth.rate_limit.client_ip (X-Real-IP solo si viene del proxy de confianza).
"""
from __future__ import annotations

import json

from db.connection import get_pool

ACCIONES = frozenset({
    "create", "update_email", "update_role", "update_status", "reset_link_sent",
    "unlock", "sessions_revoked", "baja", "password_changed_self", "password_reset_completed",
})

# Filtra por target_user_id y ordena por ts: idx_user_admin_audit_target_ts.
# El JOIN a jax_users va por PRIMARY (eq_ref). Verificado con EXPLAIN en
# tests/test_user_audit.py::test_historial_usa_el_indice_target_ts.
SQL_HISTORIAL = (
    "SELECT a.id, a.ts, a.actor_user_id, u.email, a.action, a.detail, a.ip "
    "FROM user_admin_audit a LEFT JOIN jax_users u ON u.user_id = a.actor_user_id "
    "WHERE a.target_user_id = %s ORDER BY a.ts DESC, a.id DESC LIMIT %s"
)


async def registrar(cur, actor_user_id: int, target_user_id: int, action: str,
                    detail: dict | None = None, ip: str | None = None) -> None:
    if action not in ACCIONES:
        raise ValueError(f"acción de auditoría desconocida: {action!r}")
    await cur.execute(
        "INSERT INTO user_admin_audit (actor_user_id, target_user_id, action, detail, ip) "
        "VALUES (%s, %s, %s, %s, %s)",
        (actor_user_id, target_user_id, action,
         json.dumps(detail, ensure_ascii=False) if detail is not None else None, ip),
    )


async def historial(target_user_id: int, limite: int = 50) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_HISTORIAL, (target_user_id, limite))
            filas = await cur.fetchall()
    return [
        {
            "id": f[0],
            "ts": f[1].isoformat() if f[1] else None,
            "actor_user_id": f[2],
            "actor_email": f[3],
            "action": f[4],
            "detail": json.loads(f[5]) if f[5] else None,
            "ip": f[6],
        }
        for f in filas
    ]
