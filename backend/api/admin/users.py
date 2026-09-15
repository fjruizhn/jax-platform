import asyncio
import logging
import smtplib
from datetime import date

import aiomysql
from pymysql.constants.ER import DUP_ENTRY as ER_DUP_ENTRY
from tiempo import iso_utc, utc_ahora
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

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
from db.transaccion import AISLAMIENTO_ADMIN, transaccion  # noqa: F401 -- reexporta AISLAMIENTO_ADMIN
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
# `user_id == 1` que tenía el viejo delete_user: lo que se protege no es una fila, es
# que el sistema tenga siempre al menos un superadmin activo.

def guarda_auto_accion(actor_id: int, target_id: int) -> None:
    """Nadie se degrada, se desactiva, se da de baja ni se fija la contraseña a
    sí mismo; su contraseña la cambia en "Mi cuenta"."""
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
# tenemos bloqueada. (La constante vive en db/transaccion.py desde U33 y se
# importa arriba con el mismo nombre: users_mod.AISLAMIENTO_ADMIN sigue siendo
# la misma.)
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


# Un dado de baja no aparece (etapa 5). created_at/last_login son TIMESTAMP:
# UNIX_TIMESTAMP da el instante exacto sin pasar por la zona de la sesión
# (SYSTEM = CST); leídas como fecha salían en hora CST sin zona. locked_until
# es DATETIME escrito con utc_ahora() (auth.login). Plan medido con EXPLAIN en
# tests/test_admin_usuarios_baja.py: recorre la PK en orden, sin filesort.
SQL_LISTA_USUARIOS = (
    "SELECT user_id, email, role, status, UNIX_TIMESTAMP(created_at), "
    "UNIX_TIMESTAMP(last_login), failed_attempts, locked_until "
    "FROM jax_users WHERE status <> 'deleted' ORDER BY user_id"
)

# Historial de las bajas visible desde la UI (2026-09-15, Task 2, DEUDA U36).
# `b` es la fila dada de baja: mismo plan que SQL_LISTA_USUARIOS (recorre la
# PK en el orden del ORDER BY, sin filesort ni temporal -- `status = 'deleted'`
# tampoco es un prefijo usable de idx_jax_users_role_status). `a` resuelve
# quién la hizo a su correo ACTUAL con un LEFT JOIN por PK -- eq_ref, una sola
# consulta para todas las filas (no una por fila). Si `a` también está de
# baja, `a.email` es su propio correo renombrado y se deshace igual, con
# email_original. Plan medido con EXPLAIN en
# tests/test_admin_usuarios_baja.py::test_lista_de_bajas_explain_sin_filesort_ni_temporal.
SQL_LISTA_BAJAS = (
    "SELECT b.user_id, b.email, b.role, b.deleted_at, b.deleted_by, a.email AS deleted_by_email "
    "FROM jax_users b LEFT JOIN jax_users a ON a.user_id = b.deleted_by "
    "WHERE b.status = 'deleted' ORDER BY b.user_id"
)


def email_original(email: str) -> str:
    """Inversa de `email_de_baja`: la parte del correo renombrado antes del
    ÚLTIMO '#baja-'. U32 garantiza que un correo VIVO nunca contiene '#', así
    que esto no puede confundir un correo real con uno renombrado. Si la fila
    no tuviera el sufijo (no debería pasar para una fila 'deleted'), se
    devuelve el correo tal cual -- no asume el invariante, sólo lo aprovecha."""
    return email.rsplit("#baja-", 1)[0]


@router.get("/users")
async def list_users(bajas: bool = False, user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            if bajas:
                await cur.execute(SQL_LISTA_BAJAS)
                rows = await cur.fetchall()
                return {
                    "users": [
                        {
                            "user_id": r[0],
                            "email_original": email_original(r[1]),
                            "role": r[2],
                            "deleted_at": iso_utc(r[3]),
                            "deleted_by": r[4],
                            "deleted_by_email": email_original(r[5]) if r[5] is not None else None,
                        }
                        for r in rows
                    ]
                }
            now = utc_ahora()
            await cur.execute(SQL_LISTA_USUARIOS)
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
    # de ignorarse en silencio. La contraseña la cambia el dueño (Mi cuenta o
    # enlace de recuperación, etapa 4) o la fija el admin por
    # POST /users/{id}/password (2026-09-15, U34), nunca por este PUT.
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
    # Mismo orden de bloqueos que el PUT y la baja (_leer_para_actualizar dentro de
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
    # ...y las conexiones WS/SSE ya abiertas, tras el commit (como el PUT y la baja).
    await _cortar_conexiones(user_id)
    return {"ok": True}


@router.get("/users/{user_id}/audit")
async def user_audit_history(user_id: int, user: AuthUser = Depends(require_superadmin)):
    # Últimas 50, más nueva primero; por idx_user_admin_audit_target_ts.
    return {"entries": await user_audit.historial(user_id)}


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
    ip = _ip(request)
    # Ruling U31 (etapa 5, Task 3 fix ronda 1, 2026-09-15): la lectura del
    # usuario y el token van en UNA transacción, con la fila bloqueada por PK.
    # Antes se leía sin bloqueo y el token se creaba en otra conexión: una baja
    # confirmada en ese hueco dejaba un enlace vivo para un dado de baja. Ahora
    # la baja o confirma antes (404) o espera esta fila y, al entrar, borra el
    # token recién creado. Orden usuario -> token: sufijo del de la baja
    # (superadmins -> usuario -> token) y el de /reset-password (U21); no hace
    # falta el conjunto de superadmins (no se decide ningún invariante acá).
    # El envío va DESPUÉS del commit: nunca se retiene una fila durante SMTP.
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await cur.execute(
            "SELECT email, status FROM jax_users WHERE user_id = %s AND status <> 'deleted' FOR UPDATE",
            (user_id,),
        )
        fila = await cur.fetchone()
        if fila is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        email, estado = fila
        if estado != "active":
            raise HTTPException(status_code=409, detail="usuario_no_activo")
        token, enlace = await auth_api._crear_enlace_de_recuperacion(cur, user_id, ip)
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
            except Exception:  # fail-soft: limpieza en finally; la excepción original sigue propagándose y el token expira en 1 h
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
    except Exception:  # fail-soft: Ruling U22 -- el correo ya salió y no se deshace; un 500 empujaría a un segundo envío; transaccion() ya revirtió y el fallo queda en log
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


# ------------------------------------------------------- fijar contraseña
# (2026-09-15, DECISIONES de Fernando que revierten U2; Ruling U34). El admin
# escribe la contraseña de OTRO usuario y ese usuario queda obligado a
# cambiarla en su próximo login (must_change_password, que el backend hace
# cumplir en auth/middleware.py). El enlace de recuperación sigue existiendo.

class FijarPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: str = Field(max_length=1024)


@router.post("/users/{user_id}/password")
async def fijar_password(user_id: int, req: FijarPasswordRequest, request: Request,
                         user: AuthUser = Depends(require_superadmin)):
    """Orden fijo (U11): guarda de auto-acción (la propia va por Mi cuenta),
    regla única, bcrypt en un hilo y ANTES de la transacción; en READ
    COMMITTED (U33): superadmins -> usuario (_leer_para_actualizar; 404 si no
    existe o está de baja) -> UPDATE -> enlaces pendientes -> auditoría. Tras
    el commit, el corte (U9, fail-soft).

    Un inactivo se permite: no le da entrada (el login exige 'active') y deja
    la cuenta lista para reactivarla, con la marca puesta. El bloqueo se
    limpia: protegía la contraseña vieja, y la nueva se entrega para usarla ya
    (igual que /reset-password y Mi cuenta, U16). Los enlaces pendientes se
    borran: uno viejo no puede pisar lo que fijó el admin ni apagar la marca."""
    actor_id = int(user.user_id)
    guarda_auto_accion(actor_id, user_id)
    problema = problema_de_password(req.new_password)
    if problema:
        raise HTTPException(status_code=400, detail=f"password_{problema}")
    nuevo_hash = await asyncio.to_thread(_hash, req.new_password)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        if await _leer_para_actualizar(cur, user_id) is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        await cur.execute(
            "UPDATE jax_users SET password_hash = %s, token_version = token_version + 1, "
            "must_change_password = TRUE, failed_attempts = 0, locked_until = NULL WHERE user_id = %s",
            (nuevo_hash, user_id),
        )
        await cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s AND used = FALSE", (user_id,))
        # Sin detalle: ni la contraseña ni el hash salen de jax_users.
        await user_audit.registrar(cur, actor_id, user_id, "password_set_by_admin", None, _ip(request))
    await _cortar_conexiones(user_id)
    return {"ok": True}


# ------------------------------------------------------------------ baja
# (etapa 5, spec §2 y §3.5, Rulings U10/U11). Reemplaza al DELETE.

def email_de_baja(email: str, user_id: int, fecha: date) -> str:
    """El correo de un dado de baja se renombra para LIBERAR la dirección
    (spec §3.5); el original queda en la auditoría. Cabe en VARCHAR(320):
    254 + len("#baja-") + 10 dígitos + 1 + 8 = 279."""
    return f"{email}#baja-{user_id}-{fecha:%Y%m%d}"


@router.post("/users/{user_id}/baja")
async def dar_de_baja(user_id: int, request: Request, user: AuthUser = Depends(require_superadmin)):
    """Eliminar = dar de baja (decisión de Fernando, spec §2): la cuenta queda
    inutilizable (status='deleted', versión nueva: toda sesión muere en el
    request siguiente), sale de la lista y libera el correo. Se conserva todo
    lo demás, historial incluido. Nada de DELETE.

    Orden fijo (U11): guarda de auto-acción; en transaccion(AISLAMIENTO_ADMIN)
    el conjunto de superadmins y después la fila (_leer_para_actualizar), 404,
    invariante, UPDATE, enlaces pendientes (usuario -> token, como
    /reset-password, U21) y auditoría; el corte de conexiones, tras el commit.
    Un dado de baja ya no existe para _leer_para_actualizar: repetir la baja
    es 404, como cualquier otra acción sobre él."""
    actor_id = int(user.user_id)
    guarda_auto_accion(actor_id, user_id)
    ahora = utc_ahora().replace(microsecond=0)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        actual = await _leer_para_actualizar(cur, user_id)
        if actual is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        rol_actual, estado_actual, email_actual = actual
        await exigir_invariante(cur, user_id, rol_actual, estado_actual, rol_actual, "deleted")
        await cur.execute(
            "UPDATE jax_users SET status = 'deleted', deleted_at = %s, deleted_by = %s, email = %s, "
            "token_version = token_version + 1, failed_attempts = 0, locked_until = NULL WHERE user_id = %s",
            (ahora, actor_id, email_de_baja(email_actual, user_id, ahora.date()), user_id),
        )
        # Ningún enlace de recuperación vivo sobrevive a la baja. Va DESPUÉS
        # de bloquear la fila del usuario (usuario -> token, el orden de
        # /reset-password, U21); por el índice de la FK user_id.
        await cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s AND used = FALSE", (user_id,))
        await user_audit.registrar(cur, actor_id, user_id, "baja", {"email": email_actual}, _ip(request))
    await _cortar_conexiones(user_id)
    return {"ok": True}
