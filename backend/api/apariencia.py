"""Apariencia pública de la instancia (spec 2026-09-14-tema-tokens §5.2).

GET /api/apariencia, SIN autenticación: el Login y el Reset lo necesitan antes
de que haya sesión, y la config de admin exige superadmin. Devuelve una sola
clave, por lista blanca (no por prefijo excluido): smtp.* vive en la misma
tabla y este endpoint no puede devolverlo aunque se agreguen claves. El valor
se valida contra una lista cerrada porque termina en un atributo del DOM.

Sin caché en el backend: una lectura por PRIMARY KEY de una tabla chica no
justifica un caché con invalidación entre procesos (LAS CUATRO: sin medición
previa, no hay caché nuevo). La prueba de carga del PR 1 lo mide.
"""
from fastapi import APIRouter, Response

from api.admin.config_admin import DEFAULT_CONFIG
from db.connection import get_pool

router = APIRouter()

CLAVE = "theme_default"
TEMAS = ("dark", "light")
# Por PRIMARY KEY (config_key). test_apariencia.py corre EXPLAIN sobre esta constante.
CONSULTA = "SELECT config_value FROM axioma_config WHERE config_key = %s"


@router.get("/api/apariencia")
async def apariencia(response: Response):
    response.headers["Cache-Control"] = "no-cache"
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(CONSULTA, (CLAVE,))
            fila = await cur.fetchone()
    valor = fila[0] if fila else None
    return {"theme_default": valor if valor in TEMAS else DEFAULT_CONFIG[CLAVE]}
