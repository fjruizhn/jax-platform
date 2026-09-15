import asyncio
import logging
import smtplib

import aiomysql
from pymysql.constants.ER import DUP_ENTRY as ER_DUP_ENTRY
from tiempo import iso_utc, utc_ahora
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

import user_audit
import smtp_config
from api import auth as auth_api
from auth import rate_limit
from auth.conexiones import _cortar_conexiones
from auth.password_rules import problema_de_password
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool
from db.seed import _hash
from db.transaccion import transaccion
from validacion import email_valido

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin")

VALID_ROLES = {"superadmin", "operator", "viewer"}
# `deleted` NO se pone por acá: solo la baja (etapa 5).
ESTADOS_EDITABLES = {"active", "inactive"}


def _ip(request: Request) -> str:
    return rate_limit.client_ip(request, rate_limit.TRUSTED_PROXIES)


# --------------------------------------------------------------- guardas
# (2026-09-12, admin usuarios etapa 3, spec §3.3). Reemplazan al literal
# `user_id == 1` que tenía delete_user: lo que se protege no es una fila, es
# que el sistema tenga siempre al menos un superadmin activo.

def guarda_auto_accion(actor_id: int, target_id: int) -> None:
    """Nadie se degrada, se desactiva ni se da de baja a sí mismo; su
    contraseña la cambia en "Mi cuenta"."""
    if actor_id == target_id:
        raise HTTPException(status_code=403, detail="auto_accion_prohibida")


def pierde_superadmin_activo(rol_actual: str, estado_actual: str, nuevo_rol: str, nuevo_estado: str) -> bool:
    return rol_actual == "superadmin" and estado_actual == "active" and (
        nuevo_rol != "superadmin" or nuevo_estado != "active")


# ORDEN FIJO DE BLOQUEOS (fix ronda 1, 2026-09-15). Toda escritura de admin
# sobre jax_users bloquea PRIMERO el conjunto de superadmins activos (esta
# consulta) y DESPUÉS la fila destino (_leer_para_actualizar). Antes cada
# transacción bloqueaba su destino y luego pedía el conjunto: A degradando a B
# y B degradando a A se esperaban en orden opuesto -> InnoDB 1213 -> 500. Con
# el conjunto primero, la segunda espera en la primera fila del conjunto sin
# tener nada tomado, y al entrar ve (lectura con bloqueo = lectura actual) el
# resultado de la primera -> 409 ultimo_superadmin. InnoDB bloquea en el orden
# del recorrido: por idx_jax_users_role_status, cuya cola es la PK, o sea por
# user_id (el ORDER BY lo documenta y no agrega filesort). Verificado con
# EXPLAIN en tests/test_user_audit.py::test_conteo_de_superadmins_usa_el_indice_role_status.
#
# READ COMMITTED en esas transacciones: en REPEATABLE READ el FOR UPDATE del
# rango toma next-key locks (fila + hueco), y la petición EN ESPERA de la
# segunda transacción sobre el hueco choca con el INSERT de la entrada nueva
# (operator, active, B) que hace el UPDATE de la primera en el índice -> 1213
# igual, aun con orden fijo (medido: el test de degradación mutua lo reproducía).
# READ COMMITTED bloquea solo filas. Sigue siendo correcto para la invariante:
# un fantasma solo puede SUMAR superadmins; quitar uno exige una fila que ya
# tenemos bloqueada.
AISLAMIENTO_ADMIN = "READ COMMITTED"
SQL_SUPERADMINS_ACTIVOS = (
    "SELECT user_id FROM jax_users WHERE role = 'superadmin' AND status = 'active' "
    "ORDER BY user_id FOR UPDATE"
)


async def _bloquear_superadmins_activos(cur) -> list[int]:
    await cur.execute(SQL_SUPERADMINS_ACTIVOS)
    return [fila[0] for fila in await cur.fetchall()]


async def otros_superadmins_activos(cur, excluido: int) -> int:
    # Misma consulta (y mismos bloqueos, ya tomados por _leer_para_actualizar
    # en esta transacción): el destino se excluye acá, no en el SQL, para que
    # el recorrido y el orden de bloqueo sean idénticos en los dos puntos.
    return sum(1 for user_id in await _bloquear_superadmins_activos(cur) if user_id != excluido)


async def exigir_invariante(cur, target_id: int, rol_actual: str, estado_actual: str,
                            nuevo_rol: str, nuevo_estado: str) -> None:
    if pierde_superadmin_activo(rol_actual, estado_actual, nuevo_rol, nuevo_estado) \
            and await otros_superadmins_activos(cur, target_id) == 0:
        raise HTTPException(status_code=409, detail="ultimo_superadmin")


async def _leer_para_actualizar(cur, user_id: int):
    # Orden fijo: el conjunto de superadmins activos antes que el destino,
    # SIEMPRE (también si el destino no es superadmin: el costo es serializar
    # las escrituras de admin, que son raras). Ver SQL_SUPERADMINS_ACTIVOS.
    # Un dado de baja no existe para ninguna acción (etapa 5): 404.
    await _bloquear_superadmins_activos(cur)
    await cur.execute(
        "SELECT role, status, email FROM jax_users WHERE user_id = %s AND status <> 'deleted' FOR UPDATE",
        (user_id,),
    )
    return await cur.fetchone()


@router.get("/users")
async def list_users(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    now = utc_ahora()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                # created_at/last_login son TIMESTAMP: UNIX_TIMESTAMP da el
                # instante exacto sin pasar por la zona de la sesión (SYSTEM =
                # CST); leídas como fecha salían en hora CST sin zona.
                # locked_until es DATETIME escrito con utc_ahora() (auth.login).
                "SELECT user_id, email, role, status, UNIX_TIMESTAMP(created_at), "
                "UNIX_TIMESTAMP(last_login), failed_attempts, locked_until "
                "FROM jax_users ORDER BY user_id"
            )
            rows = await cur.fetchall()
    return {
        "users": [
            {
                "user_id": r[0],
                "email": r[1],
                "role": r[2],
                "status": r[3],
                "created_at": iso_utc(r[4]),
                "last_login": iso_utc(r[5]),
                "failed_attempts": r[6] or 0,
                "locked_until": iso_utc(r[7]),
                "is_locked": bool(r[7] and r[7] > now),
            }
            for r in rows
        ]
    }


class CreateUserRequest(BaseModel):
    email: str
    role: str
    password: str


@router.post("/users")
async def create_user(req: CreateUserRequest, request: Request, user: AuthUser = Depends(require_superadmin)):
    # Códigos estables (etapa 5, Task 2, Ruling U12): el frontend los traduce
    # con mensajeDeError; nada de texto para el usuario acá.
    email = req.email.strip()
    if not email_valido(email):
        raise HTTPException(status_code=400, detail="email_invalido")
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="rol_invalido")

    # Regla única (etapa 4, spec §3.4). El _hash de db/seed.py lanza con más
    # de 72 bytes; el local de antes no tenía tope y bcrypt 5 daba un 500.
    problema = problema_de_password(req.password)
    if problema:
        raise HTTPException(status_code=400, detail=f"password_{problema}")
    # bcrypt de costo 12 (~150 ms de CPU): fuera del event loop y antes de
    # abrir la transacción, para no tener filas bloqueadas mientras hashea.
    ph = await asyncio.to_thread(_hash, req.password)
    # El alta y su registro de auditoría van juntos (etapa 3, spec §3.3).
    async with transaccion() as cur:
        await cur.execute("SELECT COUNT(*) FROM jax_users WHERE email = %s", (email,))
        (count,) = await cur.fetchone()
        if count > 0:
            raise HTTPException(status_code=409, detail="email_ya_existe")
        # El chequeo de arriba no bloquea: dos altas simultáneas con el mismo
        # email pueden pasarlo las dos. La que pierde choca con el UNIQUE de
        # email (1062) -> el mismo 409, y la transacción revierte sin auditoría.
        try:
            await cur.execute(
                "INSERT INTO jax_users (tenant_id, email, password_hash, role, status) VALUES (1, %s, %s, %s, 'active')",
                (email, ph, req.role),
            )
        except aiomysql.IntegrityError as e:
            if e.args and e.args[0] == ER_DUP_ENTRY:
                raise HTTPException(status_code=409, detail="email_ya_existe") from e
            raise
        new_id = cur.lastrowid
        await user_audit.registrar(cur, int(user.user_id), new_id, "create",
                                   {"email": email, "role": req.role}, _ip(request))
    return {"user_id": new_id, "email": email, "role": req.role, "status": "active"}


class UpdateUserRequest(BaseModel):
    # extra="forbid": un campo que ya no existe (password) responde 422 en vez
    # de ignorarse en silencio. La contraseña la cambia el dueño en Mi cuenta
    # o por enlace de recuperación (etapa 4).
    model_config = ConfigDict(extra="forbid")
    email: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    req: UpdateUserRequest,
    request: Request,
    user: AuthUser = Depends(require_superadmin),
):
    # email (etapa 5, Task 2): se recorta y valida ACÁ, antes de tocar la DB,
    # igual que rol y estado.
    email_pedido = req.email.strip() if req.email is not None else None
    if email_pedido is not None and not email_valido(email_pedido):
        raise HTTPException(status_code=400, detail="email_invalido")
    if req.role is not None and req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="rol_invalido")
    if req.status is not None and req.status not in ESTADOS_EDITABLES:
        raise HTTPException(status_code=400, detail="estado_invalido")
    actor_id = int(user.user_id)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        actual = await _leer_para_actualizar(cur, user_id)
        if actual is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        rol_actual, estado_actual, email_actual = actual
        nuevo_rol = req.role if req.role is not None else rol_actual
        nuevo_estado = req.status if req.status is not None else estado_actual
        nuevo_email = email_pedido if email_pedido is not None else email_actual
        cambia_rol, cambia_estado = nuevo_rol != rol_actual, nuevo_estado != estado_actual
        cambia_email = nuevo_email != email_actual
        if not (cambia_rol or cambia_estado or cambia_email):
            return {"ok": True}
        # Rol o estado nuevos cortan TODAS las sesiones (spec §3.2): ahí es
        # donde aplican la guarda de auto-acción y el invariante de
        # superadmin. Un cambio de email solo (U11 de la Task 2 del
        # controller): la identidad sigue siendo el user_id, no corta
        # sesiones ni pide el invariante, y el admin puede editarse su
        # propio email (no es la acción que la guarda prohíbe).
        corta_sesiones = cambia_rol or cambia_estado
        if corta_sesiones:
            guarda_auto_accion(actor_id, user_id)
            await exigir_invariante(cur, user_id, rol_actual, estado_actual, nuevo_rol, nuevo_estado)
        if cambia_email:
            # No bloquea (como el alta): el respaldo es el UNIQUE de email
            # capturado abajo como IntegrityError -> email_ya_existe.
            await cur.execute("SELECT 1 FROM jax_users WHERE email = %s AND user_id <> %s", (nuevo_email, user_id))
            if await cur.fetchone():
                raise HTTPException(status_code=409, detail="email_ya_existe")
        try:
            await cur.execute(
                "UPDATE jax_users SET email = %s, role = %s, status = %s, "
                "token_version = token_version + %s WHERE user_id = %s",
                (nuevo_email, nuevo_rol, nuevo_estado, 1 if corta_sesiones else 0, user_id),
            )
        except aiomysql.IntegrityError as e:
            if e.args and e.args[0] == ER_DUP_ENTRY:
                raise HTTPException(status_code=409, detail="email_ya_existe") from e
            raise
        ip = _ip(request)
        if cambia_email:
            await user_audit.registrar(cur, actor_id, user_id, "update_email",
                                       {"from": email_actual, "to": nuevo_email}, ip)
        if cambia_rol:
            await user_audit.registrar(cur, actor_id, user_id, "update_role", {"from": rol_actual, "to": nuevo_rol}, ip)
        if cambia_estado:
            await user_audit.registrar(cur, actor_id, user_id, "update_status",
                                       {"from": estado_actual, "to": nuevo_estado}, ip)
    # ...y las conexiones ya abiertas, ahora (Step 4b), tras el commit.
    if corta_sesiones:
        await _cortar_conexiones(user_id)
    return {"ok": True}


@router.post("/users/{user_id}/unlock")
async def unlock_user(user_id: int, request: Request, user: AuthUser = Depends(require_superadmin)):
    # Mismo orden de bloqueos que PUT/DELETE (_leer_para_actualizar dentro de
    # transaccion(AISLAMIENTO_ADMIN)): ninguna escritura de admin puede formar
    # un ciclo de espera con otra.
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        if await _leer_para_actualizar(cur, user_id) is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        await cur.execute(
            "UPDATE jax_users SET failed_attempts = 0, locked_until = NULL WHERE user_id = %s",
            (user_id,),
        )
        await user_audit.registrar(cur, int(user.user_id), user_id, "unlock", None, _ip(request))
    return {"ok": True}


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_sessions(user_id: int, request: Request, user: AuthUser = Depends(require_superadmin)):
    # Sube la versión: todos los tokens del usuario (access, refresh y el
    # próximo handshake de WS) quedan inválidos en el request siguiente.
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        if await _leer_para_actualizar(cur, user_id) is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        await cur.execute("UPDATE jax_users SET token_version = token_version + 1 WHERE user_id = %s", (user_id,))
        await user_audit.registrar(cur, int(user.user_id), user_id, "sessions_revoked", None, _ip(request))
    # ...y las conexiones WS/SSE ya abiertas, tras el commit (como PUT/DELETE).
    await _cortar_conexiones(user_id)
    return {"ok": True}


@router.get("/users/{user_id}/audit")
async def user_audit_history(user_id: int, user: AuthUser = Depends(require_superadmin)):
    # Últimas 50, más nueva primero; por idx_user_admin_audit_target_ts.
    return {"entries": await user_audit.historial(user_id)}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, user: AuthUser = Depends(require_superadmin)):
    # Sigue existiendo hasta la etapa 5 (que lo reemplaza por la baja). El
    # literal `user_id == 1` ya no está: lo reemplazan las guardas.
    guarda_auto_accion(int(user.user_id), user_id)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        actual = await _leer_para_actualizar(cur, user_id)
        if actual is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        rol_actual, estado_actual, _email = actual
        await exigir_invariante(cur, user_id, rol_actual, estado_actual, rol_actual, "deleted")
        await cur.execute("DELETE FROM jax_users WHERE user_id = %s", (user_id,))
    await _cortar_conexiones(user_id)
    return {"ok": True}


async def _borrar_enlace_no_entregado(token: str) -> None:
    """U17: un enlace que no salió no puede quedar vivo. Se borra por token
    EXACTO (fix ronda 1, 2026-09-15), no por user_id: un forgot-password
    concurrente del mismo usuario, creado DESPUÉS del nuestro, tiene su propio
    token y no tiene que perderlo por un fallo de envío que no es el suyo."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM password_reset_tokens WHERE token = %s", (token,))


@router.post("/users/{user_id}/reset-link")
async def send_reset_link(user_id: int, request: Request, user: AuthUser = Depends(require_superadmin)):
    """Reset por admin = enlace por correo (spec §3.4). Reusa el núcleo de la
    recuperación pública (_crear_enlace_de_recuperacion + _send_reset_email),
    pero NO su envoltorio fail-soft: acá el admin ESPERA el envío (en un hilo)
    y ve el error. Un 200 sin correo sería el éxito falso que el spec prohíbe."""
    try:
        settings = await smtp_config.cargar_settings()
    except smtp_config.SmtpNoDisponible as exc:
        raise HTTPException(status_code=503, detail=exc.codigo) from exc
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT email, status FROM jax_users WHERE user_id = %s", (user_id,))
            fila = await cur.fetchone()
    if fila is None:
        raise HTTPException(status_code=404, detail="usuario_no_encontrado")
    email, estado = fila
    if estado != "active":
        raise HTTPException(status_code=409, detail="usuario_no_activo")
    ip = _ip(request)
    token, enlace = await auth_api._crear_enlace_de_recuperacion(user_id, ip)
    # Mismo juego de excepciones que /smtp/test (etapa 1, api/admin/smtp.py):
    # ValueError ANTES que (OSError, SMTPException), y UnicodeEncodeError
    # ANTES que ValueError -- es subclase suya (smtplib codifica el AUTH en
    # ascii). En los tres casos: el enlace recién creado no puede quedar vivo
    # (fix ronda 1) y no se audita un envío que no salió.
    #
    # Fix ronda 2 (2026-09-15, hallazgo 3): la limpieza se movió a un
    # `finally` con la bandera `enviado`, en vez de repetirla en cada except.
    # `finally` corre ante CUALQUIER salida del `try` que no haya puesto
    # `enviado = True` -- incluida una que ningún `except` de acá atrapa,
    # como `asyncio.CancelledError` (BaseException, no Exception: el cliente
    # cierra la conexión o el servidor se apaga a mitad del envío). Sin este
    # cambio, una cancelación se saltaba los tres `except` Y la limpieza, y
    # dejaba un token vivo sin que nadie lo hubiera mandado.
    enviado = False
    try:
        await asyncio.to_thread(auth_api._send_reset_email, settings, email, enlace)
        enviado = True
    except UnicodeEncodeError as exc:
        # NUNCA se loguea `exc` acá -- smtplib codifica el AUTH (usuario Y
        # CONTRASEÑA) en ascii, y `exc.object` trae el valor completo que no
        # pudo codificarse (medido: para una contraseña con un caracter no
        # ASCII, `exc.object` es la contraseña entera). Mensaje fijo, sin
        # interpolar la excepción.
        logger.warning("Enlace de recuperación (admin) a %s: la contraseña SMTP guardada no es ASCII (AUTH)", email)
        raise HTTPException(status_code=502, detail={"code": "smtp_password_no_ascii", "server": ""}) from exc
    except ValueError as exc:
        # construir_mensaje rechaza encabezados con caracteres de control:
        # misma red de estado corrupto que /smtp/test, mismo código.
        logger.warning("Enlace de recuperación (admin): no se pudo armar el mensaje: %s", exc)
        raise HTTPException(status_code=503, detail="smtp_config_corrupta") from exc
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("Enlace de recuperación (admin) a %s falló: %s", email, exc)
        raise HTTPException(status_code=502, detail={"code": "smtp_envio_fallido", "server": str(exc)}) from exc
    finally:
        if not enviado:
            try:
                await _borrar_enlace_no_entregado(token)
            except Exception:
                # fail-soft SOLO para la limpieza: si el DELETE mismo falla,
                # se loguea (con user_id, NUNCA el token -- es la credencial)
                # y la excepción ORIGINAL (la del envío, o la cancelación)
                # sigue propagándose sola -- no se relanza esta ni se pierde
                # aquella. Tapar el error real con uno de limpieza sería peor
                # que dejar un token huérfano, que además expira en 1 hora.
                logger.exception(
                    "Enlace de recuperación (admin): no se pudo borrar el token no entregado (user_id=%s)", user_id)
    try:
        async with transaccion() as cur:
            await user_audit.registrar(cur, int(user.user_id), user_id, "reset_link_sent", {"to": email}, ip)
    except Exception:
        # Ruling U22 (fix ronda 2, 2026-09-15): fail-soft. `transaccion()` ya
        # revirtió (su propio `except BaseException: rollback(); raise`) antes
        # de que esto la atrape -- acá solo se decide la RESPUESTA. El correo
        # YA SALIÓ y no se puede deshacer: un 500 no lo cambiaría, solo
        # empujaría al admin a reintentar y mandar un SEGUNDO enlace
        # innecesario. Se responde 200 igual; el fallo de auditoría queda en
        # el log con quién lo pidió, a quién y para qué usuario.
        logger.exception(
            "Enlace de recuperación (admin): se envió pero no se pudo auditar (user_id=%s, actor=%s, to=%s)",
            user_id, user.user_id, email)
    return {"ok": True, "to": email}
