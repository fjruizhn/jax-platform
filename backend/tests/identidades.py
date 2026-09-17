"""Identidades REALES para los tests que pasan por la autenticación
(2026-09-12, administración de usuarios, etapa 2).

Desde esta etapa, get_current_user consulta jax_users por clave primaria en
cada request: un token firmado para un id inventado ("test-keys-pooling-user")
responde 401, y uno firmado para user_id=1 con rol "operator" recibe el rol
REAL de la base (superadmin). Cada test que se autentica pide acá una fila
verdadera.

- `uid`/`token_de`/`cabeceras`: UNA fila por (etiqueta, rol), creada una vez
  por sesión y reutilizada. No se borra: un id nunca queda colgando de un token
  que otro test todavía usa.
- `crear_usuario`/`borrar_usuario` (y el fixture `usuarios` de conftest.py):
  filas descartables para los tests que cambian estado, rol o versión.
- `tenant_id` sigue saliendo del token. Desde la Task 7 (2026-09-15)
  /api/chat y /api/image/generate rechazan con 400 `ids_de_uso_invalidos` un
  tenant o user no numérico, ANTES del LLM. Los tests de chat pasan un tenant
  numérico y, para no entrar al camino de memoria semántica, piden el fixture
  `chat_sin_memoria` (conftest.py) en vez de usar un tenant no numérico.
- user_id=1 (el superadmin sembrado) no se toca desde ningún test.
"""
import secrets
import uuid

import bcrypt

from auth.jwt import create_access_token, create_refresh_token

_CACHE: dict[tuple[str, str], str] = {}


async def sql(consulta, args=(), fetch=False):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(consulta, args)
            if fetch:
                return await cur.fetchall()
            return cur.lastrowid


def _hash(password):
    # rounds=4: el costo real (12) no le agrega nada a un test y cuesta ~150 ms.
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


async def crear_usuario(role="operator", status="active", password=None, token_version=0):
    email = f"test-u-{uuid.uuid4().hex[:12]}@example.invalid"
    user_id = await sql(
        "INSERT INTO jax_users (tenant_id, email, password_hash, role, status, token_version) "
        "VALUES (1, %s, %s, %s, %s, %s)",
        (email, _hash(password or secrets.token_urlsafe(12)), role, status, token_version))
    return user_id, email


async def borrar_usuario(user_id):
    # La auditoría no tiene FK (a propósito): se limpia a mano lo que dejó el test.
    await sql("DELETE FROM user_admin_audit WHERE target_user_id = %s OR actor_user_id = %s", (user_id, user_id))
    await sql("DELETE FROM password_reset_tokens WHERE user_id = %s", (user_id,))
    await sql("DELETE FROM jax_users WHERE user_id = %s", (user_id,))


async def _obtener_o_crear(etiqueta, role):
    email = f"test-ident-{etiqueta}-{role}@example.invalid"
    filas = await sql("SELECT user_id FROM jax_users WHERE email = %s", (email,), True)
    if filas:
        user_id = filas[0][0]
        await sql("UPDATE jax_users SET role = %s, status = 'active', token_version = 0 WHERE user_id = %s",
                  (role, user_id))
        return user_id
    return await sql(
        "INSERT INTO jax_users (tenant_id, email, password_hash, role, status) VALUES (1, %s, %s, %s, 'active')",
        (email, _hash(secrets.token_urlsafe(12)), role))


def uid(client, etiqueta, role="operator"):
    clave = (etiqueta, role)
    if clave not in _CACHE:
        _CACHE[clave] = str(client.portal.call(_obtener_o_crear, etiqueta, role))
    return _CACHE[clave]


def token_de(client, etiqueta, role="operator", tenant_id="1"):
    return create_access_token(uid(client, etiqueta, role), tenant_id, role)


def cabeceras(client, etiqueta, role="operator", tenant_id="1"):
    return auth(token_de(client, etiqueta, role, tenant_id))


# Vida de los refresh que firman los tests (frente C, 2026-09-16: la firma
# exige decirla). 7 días = la vida vigente; los tests que la miden fijan el
# ajuste por su cuenta (tests/test_ajuste_sesion.py).
VIDA_DE_REFRESH_EN_TESTS_S = 7 * 24 * 3600


def token_para(user_id, role="operator", tv=0, tenant_id="1", tipo="access"):
    if tipo == "access":
        return create_access_token(str(user_id), tenant_id, role, tv)
    return create_refresh_token(str(user_id), tenant_id, role, tv, vida_segundos=VIDA_DE_REFRESH_EN_TESTS_S)


def auth(token):
    return {"Authorization": f"Bearer {token}"}
