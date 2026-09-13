import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin")

DEFAULT_CONFIG = {
    "lang_default": "es",
    "theme_default": "dark",
    "session_timeout_min": "60",
    "max_pipelines": "1",
    "web_task_retention_days": "7",
    "ws_notifications": "true",
    "system_name": "Axioma",
}


async def _ensure_defaults(conn):
    async with conn.cursor() as cur:
        for k, v in DEFAULT_CONFIG.items():
            await cur.execute(
                "INSERT IGNORE INTO axioma_config (config_key, config_value) VALUES (%s, %s)",
                (k, v),
            )


# smtp.* tiene su propia pantalla (api/admin/smtp.py): la contraseña va
# cifrada. Por acá el PUT la guardaría en claro y el GET devolvería el texto
# cifrado (spec §3.1). Se excluye por prefijo, no por lista. config_key usa
# collation utf8mb4_uca1400_ai_ci: "SMTP.password" ES la fila smtp.password
# (el ON DUPLICATE KEY la pisaría en claro), así que el PUT compara en
# minúsculas y sin espacios; el NOT LIKE del GET ya es insensible por la
# collation (verificado en test_config_generico_no_lista_smtp_con_mayusculas).
PREFIJO_RESERVADO = "smtp."


@router.get("/config")
async def get_config(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_defaults(conn)
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT config_key, config_value FROM axioma_config "
                "WHERE config_key NOT LIKE %s ORDER BY config_key",
                (PREFIJO_RESERVADO + "%",),
            )
            rows = await cur.fetchall()
    return {"config": [{"key": r[0], "value": r[1]} for r in rows]}


class ConfigItem(BaseModel):
    key: str
    value: str


_IDENTIFICADOR = re.compile(r"[A-Za-z0-9_]+")


async def _alguna_reservada_para_la_base(cur, claves: list[str]) -> bool:
    """La BASE decide si una clave cae en smtp.*, con la collation de la
    columna: es la que decide la colisión de la PRIMARY KEY. uca1400_ai_ci es
    insensible a acentos, ancho y caracteres ignorables: "śmtp.password",
    "ＳＭＴＰ.password", "\u200bsmtp.password" y "ſmtp.password" SON
    smtp.password (medido contra jax_memory_test el 2026-09-13).
    Por qué pesos y no LIKE: LIKE compara carácter a carácter y no salta los
    ignorables ("\u200bsmtp.password" LIKE 'smtp.%' da 0 aunque "=" da 1).
    El prefijo de WEIGHT_STRING bajo esa collation es exactamente la regla
    con la que la base iguala. La collation se lee de la columna en cada
    llamada (sin caché: es un PUT de admin, raro) para no depender de que
    sea la misma en todos los entornos."""
    await cur.execute(
        "SELECT COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'axioma_config' AND COLUMN_NAME = 'config_key'")
    fila = await cur.fetchone()
    if fila is None or not fila[0] or not _IDENTIFICADOR.fullmatch(fila[0]):
        # Fail-closed: sin collation verificable no se escribe nada.
        raise HTTPException(status_code=503, detail="config_collation_desconocida")
    collation = fila[0]  # identificador validado arriba: no es texto del cliente
    peso = f"WEIGHT_STRING(%s COLLATE {collation})"
    union = " UNION ALL ".join(["SELECT %s AS k"] * len(claves))
    await cur.execute(
        f"SELECT COUNT(*) FROM ({union}) t "
        f"WHERE LEFT(WEIGHT_STRING(k COLLATE {collation}), LENGTH({peso})) = {peso}",
        (*claves, PREFIJO_RESERVADO, PREFIJO_RESERVADO),
    )
    (cuantas,) = await cur.fetchone()
    return cuantas > 0


@router.put("/config")
async def update_config(items: List[ConfigItem], user: AuthUser = Depends(require_superadmin)):
    # Antes de escribir NADA: un lote con una clave reservada no se aplica a
    # medias. Primero el filtro barato (mayúsculas, espacios alrededor); la
    # autoridad es la base, con su collation.
    if any(item.key.strip().lower().startswith(PREFIJO_RESERVADO) for item in items):
        raise HTTPException(status_code=400, detail="config_clave_reservada")
    if not items:
        return {"ok": True}
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            if await _alguna_reservada_para_la_base(cur, [item.key for item in items]):
                raise HTTPException(status_code=400, detail="config_clave_reservada")
            for item in items:
                await cur.execute(
                    "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE config_value = %s",
                    (item.key, item.value, item.value),
                )
    return {"ok": True}
