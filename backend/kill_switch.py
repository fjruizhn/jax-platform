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
- R9 (fix round 1, 2026-09-17): el archivo es la verdad, nunca la excepción.
  `escribir_pausa`/`borrar_pausa` hacen la operación real (link/unlink) y
  DESPUÉS el fsync del directorio -- si el fsync explota pero la operación
  real ya surtió efecto, `activar`/`reanudar` vuelven a mirar el archivo en
  vez de asumir "nada cambió" por el tipo de excepción.
- R8 (fix round 1, 2026-09-17): la difusión (`event_bus.publicar_a_todos`)
  sigue DENTRO de `_cambio` a propósito (mantiene el orden
  activated/released), pero ya no puede retenerlo: cada suscriptor tiene su
  propio tope y se manda a todos a la vez (ver jax_engine/events.py).
- R10 (fix round 2, 2026-09-17): en `activar`, "antes" (¿ya estaba puesto?)
  se mide ANTES de escribir, no después del fallo. Un fallo de escritura
  con el freno YA puesto de antes nunca cuenta como cambio de ESA llamada,
  aunque el archivo siga estando ahí después -- eso no prueba que esta
  llamada lo haya puesto.
- R13 (Task H, 2026-09-17, requisito del controlador principal): la ruta
  heredada del freno también frena. Lo que la plataforma ESCRIBE se decide
  con `interruptor.pausa_presente(ruta)` -- sólo el archivo que ella escribe:
  el "antes" de R10, el "ya no está" de reanudar y los re-chequeos de R9 --;
  lo que INFORMA (`activo()`, `estado()`, /api/state, `exigir_mesa_libre`,
  la respuesta de activar/reanudar) usa `interruptor_activo()`, que incluye
  la heredada, y agrega `heredada`. La plataforma NUNCA borra la ruta
  heredada (no le pertenece): con ella puesta, reanudar quita el archivo
  propio si estaba (auditado) y responde `activo: true, heredada: true`, sin
  difundir "liberado" -- el freno sigue puesto y decirlo a las pestañas
  sería mentir.
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
    """El cambio no quedó auditado. El freno queda PUESTO -- salvo que el
    log diga lo contrario: si la reposición de emergencia de `reanudar`
    también fracasó DE VERDAD (el freno realmente no quedó puesto, no solo
    que `_escribir` haya lanzado), el mensaje de error asociado lo dice
    explícitamente ("NO pudo reponer") en vez de esconder ese caso extremo
    detrás de este mismo tipo de excepción."""


class _NadaQueQuitar(Exception):
    pass


def activo() -> bool:
    return interruptor.interruptor_activo()


def _informe(cambio: bool) -> dict:
    """La respuesta de activar/reanudar: el freno COMPLETO (con la heredada)."""
    return {"activo": activo(), "cambio": cambio, "heredada": interruptor._heredada_activa()}


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


async def _reponer_de_emergencia(ruta, usuario: AuthUser, motivo: str) -> None:
    """Reintenta poner el freno tras un fallo de auditoría en `reanudar`
    (ante la duda, frenado) -- SOLO logea, nunca relanza: quien llama a esto
    ya está manejando la excepción real y tiene que seguir hasta
    AuditoriaDelInterruptorFallida sin que un segundo fallo la tape.

    Fix round 2 (Minor): el mensaje se decide mirando el archivo DESPUÉS
    del intento, no el tipo de excepción -- mismo principio R9. `_escribir`
    puede lanzar InterruptorNoEscribible con el link YA puesto (solo el
    fsync del directorio explotó de nuevo); en ese caso el freno SÍ quedó
    repuesto, y decir "no pudo reponer" sería la misma mentira que R9 ya
    corrigió del lado de `activar`/el primer fallo de `reanudar`."""
    try:
        await asyncio.to_thread(_escribir, ruta, _contenido("reactivado_sin_auditoria", usuario))
    except InterruptorNoEscribible as repo_exc:  # fail-soft: se logea; el fallo (real o aparente) de REPONER no debe tapar la auditoría fallida real que sigue en el except que llamó a esto
        if interruptor.pausa_presente(ruta):
            logger.error(
                "kill switch: reanudar de user_id=%s (%s) repuso el freno pese al error de fsync: %r",
                usuario.user_id, motivo, repo_exc)
        else:
            logger.error(
                "kill switch: reanudar de user_id=%s (%s) NO pudo reponer el freno: %r",
                usuario.user_id, motivo, repo_exc)


async def estado() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_ULTIMO)
            fila = await cur.fetchone()
    ultimo = None if fila is None else {
        "accion": fila[0], "user_id": fila[1], "email": fila[2], "at": iso_utc(fila[3]),
    }
    return {"activo": activo(), "heredada": interruptor._heredada_activa(), "ultimo": ultimo}


async def activar(usuario: AuthUser) -> dict:
    async with _cambio:
        ruta = interruptor.ruta_del_interruptor()
        # R10 (fix round 2): "antes" se mide ANTES de escribir. Sin esto, un
        # fallo de escritura (mkstemp/chmod/fsync) con el freno YA puesto de
        # ANTES deja el archivo intacto -- y mirar solo el DESPUÉS lo
        # confundía con "esta llamada lo puso", inventando difusión +
        # auditoría + cambio=True sobre un estado que ya era así antes de
        # que esta llamada empezara (hallazgo del re-review, reproducido con
        # freno ya puesto + directorio sin permiso de escritura).
        # R13: sólo el archivo propio; la heredada no es algo que esta
        # llamada ponga ni quite.
        antes = interruptor.pausa_presente(ruta)
        try:
            puesto = await asyncio.to_thread(_escribir, ruta, _contenido("activar", usuario))
        except InterruptorNoEscribible:
            # R9 (fix round 1): el archivo es la verdad, no la excepción.
            # `escribir_pausa` hace os.link y DESPUÉS fsync del directorio --
            # si el link ya surtió efecto (el freno está PUESTO de verdad) y
            # solo el fsync explotó, tratar esto como "nada cambió" (503)
            # sería mentir: seguimos el camino de cambio real. Cuenta como
            # cambio de ESTA llamada solo si "antes" era False: si ya estaba
            # puesto, que el archivo siga ahí después no prueba que esta
            # llamada haya hecho nada. Si tras el fallo `pausa_presente`
            # da True por fail-closed (ni pudo mirar el archivo) también
            # cuenta como cambio a propósito cuando "antes" era False: el
            # resto de JAX (LAS MANOS, Jacobs, REPL) ya lo va a leer como
            # frenado igual en ese mismo instante, así que "no cambió nada"
            # sería la mentira peor.
            if antes or not interruptor.pausa_presente(ruta):
                raise
            puesto = True
        if not puesto:
            return _informe(False)
        await event_bus.publicar_a_todos(EVENTO_ACTIVADO, {"activo": True})
        try:
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await _registrar(cur, "activar", usuario.user_id)
        except Exception as exc:  # fail-closed: el freno ya quedó puesto; la falta de auditoría se relanza como 500 y queda en el journal
            logger.error("kill switch ACTIVADO por user_id=%s sin auditoría: %r", usuario.user_id, exc)
            raise AuditoriaDelInterruptorFallida("activar") from exc
        return _informe(True)


async def reanudar(usuario: AuthUser) -> dict:
    async with _cambio:
        ruta = interruptor.ruta_del_interruptor()
        if not interruptor.pausa_presente(ruta):
            return _informe(False)
        quitado = False
        try:
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await _registrar(cur, "reanudar", usuario.user_id)
                quitado = await asyncio.to_thread(_borrar, ruta)
                if not quitado:
                    raise _NadaQueQuitar
        except _NadaQueQuitar:
            return _informe(False)
        except InterruptorNoEscribible as exc:
            # R9 (fix round 1): el archivo es la verdad. `borrar_pausa` hace
            # os.unlink y DESPUÉS fsync del directorio -- si el unlink ya
            # surtió efecto (el freno está AFUERA de verdad) y solo el fsync
            # explotó, `quitado` nunca se llegó a asignar (la excepción
            # interrumpió esa línea), así que hay que volver a mirar el
            # archivo en vez de confiar en esa variable. Si sigue puesto
            # (el unlink ni llegó a correr, p. ej. permiso denegado), se
            # relanza tal cual: no cambió nada de verdad.
            if interruptor.pausa_presente(ruta):
                raise
            await _reponer_de_emergencia(ruta, usuario, "fsync roto tras borrar")
            logger.error(
                "kill switch: reanudar de user_id=%s dejó el freno sin auditoría (fsync roto tras borrar): %r",
                usuario.user_id, exc)
            raise AuditoriaDelInterruptorFallida("reanudar") from exc
        except Exception as exc:
            # fail-closed: si se borró y no se pudo confirmar la auditoría,
            # el freno se vuelve a poner antes de relanzar (ante la duda,
            # frenado). Nota (fix round 1): si `conn.rollback()` DENTRO de
            # `transaccion()` fallara a su vez, esa excepción de rollback
            # reemplazaría a esta (`raise` sin argumento pierde la original)
            # -- el estado del freno igual queda correcto (se repone acá
            # abajo), pero el `AuditoriaDelInterruptorFallida` que se relanza
            # llevaría la causa del rollback, no la del commit. Y si el
            # COMMIT real del servidor tuvo éxito pero la confirmación se
            # perdió en el cliente (red cortada después), puede quedar una
            # fila de auditoría "reanudar" en la base con el freno repuesto
            # acá -- se erra hacia frenado a propósito; se logea para que
            # quede visible en el journal, no se intenta reconciliar solo.
            if quitado:
                await _reponer_de_emergencia(ruta, usuario, "commit/auditoría fallida")
            logger.error("kill switch: reanudar de user_id=%s sin auditoría, freno repuesto=%s: %r",
                         usuario.user_id, quitado, exc)
            raise AuditoriaDelInterruptorFallida("reanudar") from exc
        informe = _informe(True)
        if not informe["activo"]:
            await event_bus.publicar_a_todos(EVENTO_LIBERADO, {"activo": False})
        return informe


async def exigir_mesa_libre(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Dependencia de las rutas que EJECUTAN (RUTAS_FRENADAS). Después de la
    autenticación: un anónimo recibe 401, no el estado del freno."""
    if activo():
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=KILL_SWITCH_ACTIVO)
    return user
