"""Apariencia pública de la instancia (spec 2026-09-14-tema-tokens §5.2;
frente C del spec 2026-09-16-hallazgos-auditoria).

GET /api/apariencia, SIN autenticación: el Login y el Reset lo necesitan antes
de que haya sesión, y la config de admin exige superadmin. Devuelve una lista
BLANCA de claves (no por prefijo excluido): smtp.* vive en la misma tabla y
este endpoint no puede devolverlo aunque se agreguen claves.

  - theme_default: validado contra una lista cerrada (termina en un atributo
    del DOM), con respaldo DEFAULT_CONFIG. Sin caché: una lectura por PRIMARY
    KEY (EXPLAIN en test_apariencia.py).
  - lang_default y system_name: por ajustes.py (caché con TTL, invalidado por
    el PUT de admin). Ilegibles -> 503 ajuste_ilegible, sin respaldo: el
    frontend se queda con el último conocido.
"""
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

import ajustes
from api.admin.config_admin import DEFAULT_CONFIG
from db.connection import get_pool

router = APIRouter()

CLAVE = "theme_default"
TEMAS = ("dark", "light")
# Por PRIMARY KEY (config_key). test_apariencia.py corre EXPLAIN sobre esta constante.
CONSULTA = "SELECT config_value FROM axioma_config WHERE config_key = %s"


class Apariencia(BaseModel):
    """Contrato de salida (M-1, revisión final del PR 1, 2026-09-14): un dict
    más grande (o un valor fuera de contrato) falla ruidoso en la validación
    de FastAPI en vez de salir al cliente."""

    theme_default: Literal["dark", "light"]
    lang_default: Literal[ajustes.IDIOMAS]
    system_name: str


@router.get("/api/apariencia", response_model=Apariencia)
async def apariencia(response: Response):
    response.headers["Cache-Control"] = "no-cache"
    idioma = await ajustes.valor(ajustes.IDIOMA)
    nombre = await ajustes.valor(ajustes.NOMBRE)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(CONSULTA, (CLAVE,))
            fila = await cur.fetchone()
    valor = fila[0] if fila else None
    return {
        "theme_default": valor if valor in TEMAS else DEFAULT_CONFIG[CLAVE],
        "lang_default": idioma,
        "system_name": nombre,
    }
