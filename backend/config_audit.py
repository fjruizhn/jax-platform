"""Auditoría de TODA escritura de configuración (2026-09-18).

Por qué existe: hasta hoy, `PUT /api/admin/config` cambiaba claves de
`axioma_config` sin dejar rastro. El 2026-09-17 la compuerta
`ejecutor.c5_auditor_admite_datos_de_clientes` -- la que deja que el auditor de
nube vea máquinas con datos de clientes -- apareció en `true`, y por la base no
se podía saber quién la había puesto así ni desde cuándo. Un cambio de
configuración sin autor no se puede revisar ni revertir con conocimiento: es el
Principio IX (no se difiere un contrato de auditoría).

`escribir` es el ÚNICO escritor de `axioma_config` fuera de las semillas de
arranque de db/migrations.py (detector en tests/test_config_audit.py). Recibe
el CURSOR de la transacción de quien llama, como user_audit.registrar: o quedan
el cambio y su rastro, o ninguno de los dos. Fail-closed por construcción, no
por un try/except -- si el INSERT de la auditoría falla, revierte también el
UPDATE de la configuración.
"""
from __future__ import annotations

from db.connection import get_pool
from tiempo import iso_utc

# De dónde vino el cambio. `config` = la pantalla Configuración
# (api/admin/config_admin.py); `smtp` = la pantalla Correo (api/admin/smtp.py,
# vía smtp_config.guardar_filas). Lista cerrada: un origen nuevo se declara acá
# y en el CHECK de la tabla (db/migrations.py).
ORIGENES = frozenset({"config", "smtp"})

# smtp.password vive CIFRADA en axioma_config. Copiar el texto cifrado a la
# auditoría sería una segunda copia del secreto en reposo, en una tabla que
# nació para leerse: se guarda esta marca de los dos lados. Qué cambió y quién
# lo cambió se conserva; el valor, no.
REDACTADO = "«redactado»"
CLAVES_REDACTADAS = frozenset({"smtp.password"})

# El valor anterior se lee BAJO BLOQUEO dentro de la misma transacción: sin el
# FOR UPDATE, dos PUT simultáneos podrían leer el mismo "anterior" y auditar
# un cambio que nunca ocurrió. Va por PRIMARY KEY (config_key).
SQL_ANTERIOR = "SELECT config_key, config_value FROM axioma_config WHERE config_key = %s FOR UPDATE"

SQL_ESCRIBIR = (
    "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
    "ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)"
)

# `ts` en UTC explícito (misma razón que user_audit.registrar: el DEFAULT
# CURRENT_TIMESTAMP sigue la zona de la sesión, que en hall9000 es CST, y el
# historial saldría 6 h corrido). Este es el único escritor de la tabla.
SQL_AUDITORIA = (
    "INSERT INTO axioma_config_audit "
    "(ts, actor_user_id, config_key, valor_anterior, valor_nuevo, origen, ip) "
    "VALUES (UTC_TIMESTAMP(6), %s, %s, %s, %s, %s, %s)"
)

# Filtra por config_key y ordena por ts: idx_axioma_config_audit_key_ts. El
# JOIN a jax_users va por PRIMARY (eq_ref). Verificado con EXPLAIN en
# tests/test_config_audit.py::test_el_historial_usa_el_indice_clave_ts.
SQL_HISTORIAL = (
    "SELECT a.id, a.ts, a.actor_user_id, u.email, a.config_key, a.valor_anterior, "
    "a.valor_nuevo, a.origen, a.ip "
    "FROM axioma_config_audit a LEFT JOIN jax_users u ON u.user_id = a.actor_user_id "
    "WHERE a.config_key = %s ORDER BY a.ts DESC, a.id DESC LIMIT %s"
)


def _visible(clave: str, valor: str | None) -> str | None:
    """El valor tal como se guarda en la auditoría. La decisión se toma con la
    clave que la BASE tiene (la PRIMARY KEY real), no con la grafía del
    pedido: es la misma regla con la que la fila se pisa."""
    if valor is None:
        return None
    return REDACTADO if clave in CLAVES_REDACTADAS else valor


async def escribir(cur, filas: dict[str, str], actor_user_id: int | None,
                   origen: str, ip: str | None = None) -> int:
    """Escribe `filas` en axioma_config y su auditoría con el MISMO cursor.

    Devuelve cuántas claves CAMBIARON de verdad. Guardar el mismo valor no
    escribe auditoría: la pantalla manda todas las claves en cada guardado y,
    sin esto, el cambio que importa quedaría enterrado entre filas idénticas
    (misma regla que kill_switch_audit: una fila por cambio real).
    """
    if origen not in ORIGENES:
        raise ValueError(f"origen de auditoría desconocido: {origen!r}")
    if actor_user_id is None:
        # Media auditoría (qué cambió, sin quién) no cierra el agujero.
        raise ValueError("una escritura de configuración sin actor no se audita")
    cambios = 0
    for clave, valor in filas.items():
        await cur.execute(SQL_ANTERIOR, (clave,))
        fila = await cur.fetchone()
        # La collation de config_key (uca1400_ai_ci) decide qué fila se pisa:
        # "MAX_PIPELINES" ES la fila max_pipelines. Se audita la clave REAL de
        # la base, si existe -- si no, el historial de una clave no mostraría
        # el cambio que la pisó.
        clave_real = fila[0] if fila is not None else clave
        anterior = fila[1] if fila is not None else None
        await cur.execute(SQL_ESCRIBIR, (clave, valor))
        if anterior == valor:
            continue
        await cur.execute(SQL_AUDITORIA, (int(actor_user_id), clave_real,
                                          _visible(clave_real, anterior),
                                          _visible(clave_real, valor), origen, ip))
        cambios += 1
    return cambios


async def historial(config_key: str, limite: int = 50) -> list[dict]:
    """Quién cambió esta clave, a qué, y cuándo. Lo que faltaba el 2026-09-17."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_HISTORIAL, (config_key, limite))
            filas = await cur.fetchall()
    return [
        {
            "id": f[0],
            "ts": iso_utc(f[1]),
            "actor_user_id": f[2],
            "actor_email": f[3],
            "config_key": f[4],
            "valor_anterior": f[5],
            "valor_nuevo": f[6],
            "origen": f[7],
            "ip": f[8],
        }
        for f in filas
    ]
