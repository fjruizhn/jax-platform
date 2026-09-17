"""Kill switch de la Mesa (2026-09-16, frente B).

La plataforma es el único ESCRITOR del freno (interruptor.py, espejo de
jax/core/interruptor.py). LAS MANOS, Jacobs y el REPL lo leen del mismo
archivo. Reglas:

- activar: el freno PRIMERO (escritura atómica), después el aviso a todos y
  al final la auditoría. Si la auditoría falla, el freno queda PUESTO y se
  lanza AuditoriaDelInterruptorFallida (500 + journal): una base caída no
  impide frenar.
- reanudar: auditoría y borrado en la MISMA transacción. Si la confirmación
  falla después de borrar, se vuelve a poner el freno. Ante la duda, frenado.
- una fila de auditoría por CAMBIO real; pedir lo que ya está no escribe.
- sin caché: el freno se mira con un stat por pedido (medido en la carga del
  frente B). Un caché lo retrasaría su TTL.
- exigir_mesa_libre: dependencia de las rutas que ejecutan (RUTAS_FRENADAS);
  423 `kill_switch_activo`.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Depends, HTTPException, status

import interruptor
from auth.middleware import get_current_user
from auth.models import AuthUser
from db.connection import get_pool
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from jax_engine.events import event_bus
from tiempo import iso_utc, utc_ahora

logger = logging.getLogger(__name__)

ACCIONES = frozenset({"activar", "reanudar"})
KILL_SWITCH_ACTIVO = "kill_switch_activo"
NO_ESCRIBIBLE = "kill_switch_no_escribible"
AUDITORIA_FALLIDA = "kill_switch_auditoria_fallida"
EVENTO_ACTIVADO = "kill_switch_activated"
EVENTO_LIBERADO = "kill_switch_released"

RUTAS_FRENADAS = frozenset({
    ("POST", "/api/chat"),
    ("POST", "/api/image/generate"),
    ("POST", "/api/command"),
    ("POST", "/api/pipelines"),
    ("POST", "/api/pipelines/{pipeline_id}/resume"),
})

SQL_REGISTRAR = "INSERT INTO kill_switch_audit (accion, user_id, at) VALUES (%s, %s, UTC_TIMESTAMP(6))"
# Ordena por idx_kill_switch_audit_at (con el id de desempate, que InnoDB ya
# guarda en el índice). El JOIN va por PRIMARY. EXPLAIN en
# tests/test_kill_switch_endpoints.py.
SQL_ULTIMO = (
    "SELECT a.accion, a.user_id, u.email, a.at FROM kill_switch_audit a "
    "LEFT JOIN jax_users u ON u.user_id = a.user_id "
    "ORDER BY a.at DESC, a.id DESC LIMIT 1"
)

_cambio = asyncio.Lock()


class InterruptorNoEscribible(RuntimeError):
    """El archivo del freno no se pudo escribir ni borrar: nada cambió."""


class AuditoriaDelInterruptorFallida(RuntimeError):
    """El cambio no quedó auditado; el freno quedó PUESTO."""


class _NadaQueQuitar(Exception):
    pass


def activo() -> bool:
    return interruptor.interruptor_activo()


def _escribir(ruta, contenido: str) -> bool:
    try:
        return interruptor.escribir_pausa(ruta, contenido)
    except OSError as exc:
        raise InterruptorNoEscribible(str(exc)) from exc


def _borrar(ruta) -> bool:
    try:
        return interruptor.borrar_pausa(ruta)
    except OSError as exc:
        raise InterruptorNoEscribible(str(exc)) from exc


def _contenido(accion: str, usuario: AuthUser) -> str:
    return json.dumps({"accion": accion, "user_id": str(usuario.user_id), "at": iso_utc(utc_ahora())})


async def _registrar(cur, accion: str, user_id) -> None:
    if accion not in ACCIONES:
        raise ValueError(f"acción de kill switch desconocida: {accion!r}")
    await cur.execute(SQL_REGISTRAR, (accion, int(user_id)))


async def estado() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_ULTIMO)
            fila = await cur.fetchone()
    ultimo = None if fila is None else {
        "accion": fila[0], "user_id": fila[1], "email": fila[2], "at": iso_utc(fila[3]),
    }
    return {"activo": activo(), "ultimo": ultimo}


async def activar(usuario: AuthUser) -> dict:
    async with _cambio:
        ruta = interruptor.ruta_del_interruptor()
        puesto = await asyncio.to_thread(_escribir, ruta, _contenido("activar", usuario))
        if not puesto:
            return {"activo": True, "cambio": False}
        await event_bus.publicar_a_todos(EVENTO_ACTIVADO, {"activo": True})
        try:
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await _registrar(cur, "activar", usuario.user_id)
        except Exception as exc:  # fail-closed: el freno ya quedó puesto; la falta de auditoría se relanza como 500 y queda en el journal
            logger.error("kill switch ACTIVADO por user_id=%s sin auditoría: %r", usuario.user_id, exc)
            raise AuditoriaDelInterruptorFallida("activar") from exc
        return {"activo": True, "cambio": True}


async def reanudar(usuario: AuthUser) -> dict:
    async with _cambio:
        ruta = interruptor.ruta_del_interruptor()
        if not interruptor.interruptor_activo(ruta):
            return {"activo": False, "cambio": False}
        quitado = False
        try:
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await _registrar(cur, "reanudar", usuario.user_id)
                quitado = await asyncio.to_thread(_borrar, ruta)
                if not quitado:
                    raise _NadaQueQuitar
        except _NadaQueQuitar:
            return {"activo": False, "cambio": False}
        except InterruptorNoEscribible:
            raise
        except Exception as exc:  # fail-closed: si se borró y no se pudo confirmar la auditoría, el freno se vuelve a poner antes de relanzar
            if quitado:
                await asyncio.to_thread(_escribir, ruta, _contenido("reactivado_sin_auditoria", usuario))
            logger.error("kill switch: reanudar de user_id=%s sin auditoría, freno repuesto=%s: %r",
                         usuario.user_id, quitado, exc)
            raise AuditoriaDelInterruptorFallida("reanudar") from exc
        await event_bus.publicar_a_todos(EVENTO_LIBERADO, {"activo": False})
        return {"activo": False, "cambio": True}


async def exigir_mesa_libre(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Dependencia de las rutas que EJECUTAN (RUTAS_FRENADAS). Después de la
    autenticación: un anónimo recibe 401, no el estado del freno."""
    if activo():
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=KILL_SWITCH_ACTIVO)
    return user
