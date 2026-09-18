import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import List
import ajustes
import config_audit
from auth import rate_limit
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool
from db.transaccion import AISLAMIENTO_ADMIN, transaccion

router = APIRouter(prefix="/api/admin")

# Frente C (2026-09-16): los cinco ajustes que mandan (session_timeout_min,
# max_pipelines, web_task_retention_days, lang_default, system_name) NO van
# acá. Sus filas las crea una vez db/migrations.py::_ajustes_que_mandan_v1, y
# si faltan, la respuesta es 503 ajuste_ilegible (ajustes.py): un GET de esta
# pantalla no puede recrearlas en silencio con un default.
# ws_notifications tampoco: se retiró (A-17, 2026-09-16) y su fila la borra,
# una sola vez, la misma migración -- si siguiera acá, _ensure_defaults la
# recrearía en cada GET.
# theme_default tampoco lo siembra este módulo desde el 2026-09-18: la fila la
# pone db/migrations.py::_apariencia_default_v1 en cada arranque (INSERT
# IGNORE, sin pisar lo del admin). Antes la creaba el GET de acá -- una LECTURA
# que escribía configuración por fuera del camino auditado, que es justo lo que
# esta rama cerró. Si la fila faltara, GET /api/apariencia sigue respondiendo
# con este mismo valor de respaldo.
DEFAULT_CONFIG = {
    "theme_default": "dark",
}


# smtp.* tiene su propia pantalla (api/admin/smtp.py): la contraseña va
# cifrada. Por acá el PUT la guardaría en claro y el GET devolvería el texto
# cifrado (spec §3.1). Se excluye por prefijo, no por lista. config_key usa
# collation utf8mb4_uca1400_ai_ci: "SMTP.password" ES la fila smtp.password
# (el ON DUPLICATE KEY la pisaría en claro). GET y PUT deciden con la MISMA
# regla, la de la base (_reservadas_para_la_base).
PREFIJO_RESERVADO = "smtp."


@router.get("/config")
async def get_config(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Tabla chica de configuración, recorrida por la PK para el ORDER BY.
            # Antes filtraba con NOT LIKE 'smtp.%', que compara carácter a
            # carácter y dejaba pasar una fila legada "\u200bsmtp.x" (medido
            # 2026-09-13): ahora excluye con la misma regla que el PUT.
            await cur.execute("SELECT config_key, config_value FROM axioma_config ORDER BY config_key")
            rows = await cur.fetchall()
            reservadas = await _reservadas_para_la_base(cur, [r[0] for r in rows])
    return {
        "config": [{"key": r[0], "value": r[1]} for r in rows if r[0] not in reservadas],
        # Los rangos los decide el servidor (ajustes.py); la pantalla los usa,
        # no los copia (spec 2026-09-16 §C).
        "limites": ajustes.limites(),
    }


class ConfigItem(BaseModel):
    key: str
    value: str


_IDENTIFICADOR = re.compile(r"[A-Za-z0-9_]+")


async def _collation(cur) -> str:
    await cur.execute(
        "SELECT COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'axioma_config' AND COLUMN_NAME = 'config_key'")
    fila = await cur.fetchone()
    if fila is None or not fila[0] or not _IDENTIFICADOR.fullmatch(fila[0]):
        # Fail-closed: sin collation verificable no se escribe nada.
        raise HTTPException(status_code=503, detail="config_collation_desconocida")
    return fila[0]  # identificador validado arriba: no es texto del cliente


async def _ajustes_para_la_base(cur, claves: list[str]) -> dict[str, str]:
    """{clave pedida: clave de ajuste que la base considera IGUAL}. Misma
    autoridad que _reservadas_para_la_base: la collation de la columna decide
    la colisión de la PRIMARY KEY, así que "MAX_PIPELINES" o "máx_pipelines"
    escribirían la fila max_pipelines y tienen que validarse como tal."""
    if not claves:
        return {}
    collation = await _collation(cur)
    pedidas = " UNION ALL ".join(["SELECT %s AS k"] * len(claves))
    conocidas = " UNION ALL ".join(["SELECT %s AS a"] * len(ajustes.CLAVES))
    await cur.execute(
        f"SELECT p.k, c.a FROM ({pedidas}) p JOIN ({conocidas}) c "
        f"ON WEIGHT_STRING(p.k COLLATE {collation}) = WEIGHT_STRING(c.a COLLATE {collation})",
        (*claves, *ajustes.CLAVES),
    )
    return {pedida: conocida for pedida, conocida in await cur.fetchall()}


async def _reservadas_para_la_base(cur, claves: list[str]) -> set[str]:
    """Las claves de `claves` que caen en smtp.* (la usan el GET y el PUT).
    La BASE decide si una clave cae en smtp.*, con la collation de la
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
    if not claves:
        return set()
    collation = await _collation(cur)
    peso = f"WEIGHT_STRING(%s COLLATE {collation})"
    union = " UNION ALL ".join(["SELECT %s AS k"] * len(claves))
    await cur.execute(
        f"SELECT k FROM ({union}) t "
        f"WHERE LEFT(WEIGHT_STRING(k COLLATE {collation}), LENGTH({peso})) = {peso}",
        (*claves, PREFIJO_RESERVADO, PREFIJO_RESERVADO),
    )
    return {k for (k,) in await cur.fetchall()}


@router.put("/config")
async def update_config(items: List[ConfigItem], request: Request,
                        user: AuthUser = Depends(require_superadmin)):
    # Antes de escribir NADA: un lote con una clave reservada no se aplica a
    # medias. Primero el filtro barato (mayúsculas, espacios alrededor); la
    # autoridad es la base, con su collation.
    if any(item.key.strip().lower().startswith(PREFIJO_RESERVADO) for item in items):
        raise HTTPException(status_code=400, detail="config_clave_reservada")
    if not items:
        return {"ok": True}
    # Todo en UNA transacción (2026-09-18): el cambio y su auditoría, o
    # ninguno. Si el INSERT de config_audit falla, el UPDATE se revierte con
    # él -- fail-closed, sin try/except que lo tape. Las guardas de abajo
    # también revierten: `transaccion` deshace ante CUALQUIER excepción,
    # incluida la HTTPException. READ COMMITTED, como el resto de las
    # escrituras de admin.
    try:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            claves = [item.key for item in items]
            if await _reservadas_para_la_base(cur, claves):
                raise HTTPException(status_code=400, detail="config_clave_reservada")
            # Antes de escribir NADA: un lote con un ajuste inválido no se aplica a medias.
            canonicas = await _ajustes_para_la_base(cur, claves)
            for item in items:
                canonica = canonicas.get(item.key)
                if canonica is None:
                    continue
                try:
                    ajustes.interpretar(canonica, item.value)
                except ajustes.ValorInvalido:
                    raise HTTPException(status_code=400,
                                        detail={"code": "config_valor_invalido", "clave": canonica}) from None
            await config_audit.escribir(
                cur, {item.key: item.value for item in items}, int(user.user_id), "config",
                rate_limit.client_ip(request, rate_limit.TRUSTED_PROXIES))
    finally:
        # FUERA del `async with`: adentro correría ANTES del commit, y una
        # lectura en esa ventana volvería a cachear el valor viejo. Acá corre
        # con la transacción ya cerrada, haya confirmado o revertido -- en los
        # dos casos el caché tiene que dejar de mandar.
        ajustes.invalidar()
    return {"ok": True}
