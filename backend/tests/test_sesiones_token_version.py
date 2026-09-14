"""Sesiones que se cortan de verdad (2026-09-12, administración de usuarios, etapa 2).

Antes: get_current_user solo decodificaba el JWT (rol del token) y /refresh
reemitía el access con el rol del refresh token, sin consultar la base.
Desactivar, borrar o bajar de rol NO cortaba la sesión: el usuario seguía
entrando hasta 7 días, y un superadmin degradado seguía siéndolo (spec §1,
hallazgo 1).

Ahora: access y refresh llevan `tv`; cada request lee status, role y
token_version por clave primaria; el rol sale de la base. Un token de antes
del despliegue (sin `tv`) vale como tv=0.
"""
import time

from jose import jwt

from auth.jwt import ALGORITHM, SECRET, create_access_token, create_refresh_token, decode_token


# ------------------------------------------------------------- puros

def test_access_token_lleva_la_version():
    assert decode_token(create_access_token("5", "1", "operator"))["tv"] == 0
    assert decode_token(create_access_token("5", "1", "operator", 3))["tv"] == 3


def test_refresh_token_lleva_la_version():
    payload = decode_token(create_refresh_token("5", "1", "operator", 7))
    assert (payload["tv"], payload["type"]) == (7, "refresh")


# ---------------------------------------------------------- esquema

async def _columna():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jax_users' AND COLUMN_NAME = 'token_version'")
            return [tuple(f) for f in await cur.fetchall()]


def test_columna_token_version(client):
    assert client.portal.call(_columna) == [("int", "NO", "0")]
