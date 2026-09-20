import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import get_current_user
from auth.models import AuthUser
from kill_switch import exigir_mesa_libre
from config_entorno import ruta_absoluta_requerida
from jax_engine.events import event_bus
from jax_engine.schemas import JAXEvent
from jax_engine.state import engine_state
from redaccion import recortar_redactado

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MISSIONS_DIR = ruta_absoluta_requerida("JAX_MISSIONS_DIR")
JAX_BIN = ruta_absoluta_requerida("JAX_BIN")


def _owner_file(task_id: str) -> Path:
    return MISSIONS_DIR / f"web-task-{task_id}_owner.json"


def _result_file(task_id: str) -> Path:
    return MISSIONS_DIR / f"web-task-{task_id}_result.md"


def _anotar_en_duenio(task_id: str, clave: str, valor) -> None:
    """Agrega una clave al archivo de dueño que ya lee GET, escrito atómico:
    un GET concurrente nunca lee JSON a medias."""
    duenio = _owner_file(task_id)
    datos = json.loads(duenio.read_text())
    datos[clave] = valor
    temporal = duenio.with_suffix(".json.tmp")
    temporal.write_text(json.dumps(datos))
    os.replace(temporal, duenio)


def _escribir_fallo(task_id: str, motivo: str) -> None:
    """El fallo de la tarea queda en el archivo de dueño (A-51)."""
    _anotar_en_duenio(task_id, "fallo", {"code": "comando_fallo", "motivo": motivo})


# Ronda final M5 (2026-09-16): todo el disco de este modulo (mision, dueño,
# resultado) va en un hilo -- LAS CUATRO, async: nada bloqueante dentro de un
# async def. Cada funcion de abajo es UN salto a to_thread por paso del handler.
def _registrar_tarea(task_id: str, mission_file: Path, comando: str, tenant_id: str, user_id: str) -> None:
    MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    mission_file.write_text(f"---\nfaceta: hyde\n---\n\n{comando}\n")
    # Registro de dueño en disco (no sólo en memoria): GET /command/{task_id}
    # no tenía NINGÚN chequeo de autorización — cualquier usuario autenticado
    # que conociera (o adivinara/leyera del localStorage de otro) un task_id
    # ajeno podía leer su resultado completo. Ver ese endpoint más abajo.
    _owner_file(task_id).write_text(json.dumps({"tenant_id": tenant_id, "user_id": user_id}))


def _leer_duenio(task_id: str) -> object:
    """Contenido del archivo de dueño. OSError/ValueError (archivo ausente,
    JSON corrupto) se propagan: el handler los vuelve 404 -- tarea ajena o
    inexistente, sin confirmarle nada a quien no es su dueño."""
    return json.loads(_owner_file(task_id).read_text())


def _leer_resultado_get(task_id: str) -> str | None:
    """Texto del resultado, o None si la tarea sigue corriendo (sin result
    file todavía). R16 (2026-09-16): esto va en un to_thread APARTE del
    dueño -- un OSError/ValueError acá (permisos, encoding, un directorio en
    vez de archivo) NO puede mapearse a 404: el store del frontend trata 404
    como "tarea completada sin resultado" y la saca de pendientes para
    siempre (A-44); un error de LECTURA del resultado se propaga tal cual y
    el handler lo vuelve 500, que el store sí reintenta."""
    result_file = _result_file(task_id)
    return result_file.read_text() if result_file.exists() else None


def _simular(task_id: str, mission_file: Path, result_file: Path) -> str:
    texto = mission_file.read_text()
    # Antes que el resultado: GET no ve un result_file sin la marca (R8).
    _anotar_en_duenio(task_id, "simulado", True)
    result_file.write_text(texto)
    return texto


def _leer_resultado(result_file: Path) -> str:
    # JAX escribe el resultado en result_file internamente.
    # No sobreescribir — solo leer (o dejarlo vacío si no escribió nada).
    if result_file.exists():
        return result_file.read_text()
    result_file.write_text("")
    return ""


class CommandRequest(BaseModel):
    command: str
    mode: str = "execute"



async def _correr_binario(mission_file, result_file) -> str:
    """Corre `JAX_BIN --task ...` y devuelve su stderr redactado.

    Antes iba con stdout Y stderr en DEVNULL: si el binario moria antes de escribir
    el resultado, el turno salia `comando_sin_resultado` y no habia UNA linea que
    dijera por que. Cuarto caso del mismo patron el 2026-09-20.

    Aca `communicate()` SI sirve --a diferencia del vigia y del runner del Ejecutor,
    donde otras tareas ya leen los flujos--: nadie mas los lee y `communicate()` los
    drena solo, asi que no hay riesgo de llenar el pipe.

    El stderr va al LOG, nunca a la respuesta: la respuesta la ve el usuario y una
    traza puede traer rutas o secretos. Redactado igual, por si acaso."""
    proc = await asyncio.create_subprocess_exec(
        str(JAX_BIN), "--task", str(mission_file),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(Path.home()),
    )
    _, err = await proc.communicate()
    texto = recortar_redactado((err or b"").decode(errors="replace"), 2000) or ""
    if proc.returncode != 0 and texto.strip():
        logger.error("command: el binario salio con %s -- stderr: %s", proc.returncode, texto)
    return texto


@router.post("/command")
async def create_command(req: CommandRequest, user: AuthUser = Depends(exigir_mesa_libre)):
    task_id = str(uuid.uuid4())
    mission_file = MISSIONS_DIR / f"web-task-{task_id}.md"
    result_file = MISSIONS_DIR / f"web-task-{task_id}_result.md"

    tenant_id = user.tenant_id
    user_id = user.user_id
    await asyncio.to_thread(_registrar_tarea, task_id, mission_file, req.command, tenant_id, user_id)

    await engine_state.set_facet_status("hyde", "thinking", tenant_id, user_id, req.command[:100])

    asyncio.create_task(
        _run_command(task_id, mission_file, result_file, tenant_id, user_id, req.mode)
    )

    return {
        "task_id": task_id,
        "status": "running",
        "mission_file": mission_file.name,
        "result_file": result_file.name,
    }


@router.get("/command/{task_id}")
async def get_command_result(task_id: str, user: AuthUser = Depends(get_current_user)):
    try:
        uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="task_id_invalido")

    # 404 (no 403) para no confirmarle a un no-dueño que el task_id existe.
    # Tareas creadas antes de este cambio no tienen owner file y también
    # devuelven 404 — costo único de la migración, no un bug.
    try:
        owner = await asyncio.to_thread(_leer_duenio, task_id)
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="tarea_no_encontrada")
    if (
        not isinstance(owner, dict)
        or owner.get("user_id") != user.user_id
        or owner.get("tenant_id") != user.tenant_id
    ):
        raise HTTPException(status_code=404, detail="tarea_no_encontrada")

    fallo = owner.get("fallo")
    if isinstance(fallo, dict):
        return {"status": "failed", "code": fallo.get("code"), "motivo": fallo.get("motivo", "")}

    # R16 (2026-09-16): lectura del RESULTADO en un to_thread separado del
    # dueño -- un fallo acá (permisos, encoding, un directorio en vez de
    # archivo) no es "tarea no encontrada": es un error de lectura real, y
    # se propaga como 500 para que el store del frontend reintente en vez de
    # darla por completada sin resultado para siempre (A-44).
    try:
        texto = await asyncio.to_thread(_leer_resultado_get, task_id)
    except (OSError, ValueError):
        raise HTTPException(status_code=500, detail="tarea_resultado_ilegible")

    if texto is not None:
        if owner.get("simulado") is True:
            return {"status": "completed", "result": texto, "code": "comando_simulado"}
        return {"status": "completed", "result": texto} if texto else {
            "status": "completed", "result": "", "code": "comando_sin_resultado"}
    return {"status": "running"}


async def _run_command(
    task_id: str,
    mission_file: Path,
    result_file: Path,
    tenant_id: str,
    user_id: str,
    mode: str,
):
    # Solo la EJECUCION puede marcar un fallo: si lo que falla es publicar el
    # evento o el estado despues de un resultado valido, la tarea no fallo.
    try:
        codigo = None
        if mode == "dry_run":
            texto = await asyncio.to_thread(_simular, task_id, mission_file, result_file)
            codigo = "comando_simulado"
        else:
            await _correr_binario(mission_file, result_file)
            texto = await asyncio.to_thread(_leer_resultado, result_file)
            if not texto:
                codigo = "comando_sin_resultado"
    except Exception as e:  # fail-soft: tarea de fondo: el fallo se publica como command_completed status='failed' con código y queda en el archivo de dueño; no hay falso éxito
        motivo = recortar_redactado(str(e), 400)
        try:
            await asyncio.to_thread(_escribir_fallo, task_id, motivo)
        except (OSError, ValueError):  # fail-soft: sin archivo de dueño legible GET da 404 o running, pero el evento failed y el estado idle salen igual; queda en el log
            logger.exception("command %s: no se pudo registrar el fallo en el archivo de dueño", task_id)
        await event_bus.publish(JAXEvent(
            event_type="command_completed", tenant_id=tenant_id, user_id=user_id,
            payload={"task_id": task_id, "status": "failed", "code": "comando_fallo",
                     "result": "", "motivo": motivo}))
        await engine_state.set_facet_status("hyde", "idle", tenant_id, user_id)
        return

    payload = {"task_id": task_id, "status": "completed", "result": texto}
    if codigo:
        payload["code"] = codigo
    await event_bus.publish(JAXEvent(event_type="command_completed", tenant_id=tenant_id,
                                     user_id=user_id, payload=payload))
    await engine_state.set_facet_status("hyde", "idle", tenant_id, user_id)
