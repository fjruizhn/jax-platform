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
from config_de_entorno import ruta_requerida
from jax_engine.events import event_bus
from jax_engine.schemas import JAXEvent
from jax_engine.state import engine_state
from redaccion import recortar_redactado

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MISSIONS_DIR = ruta_requerida("JAX_MISSIONS_DIR")
JAX_BIN = ruta_requerida("JAX_BIN")


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


class CommandRequest(BaseModel):
    command: str
    mode: str = "execute"


@router.post("/command")
async def create_command(req: CommandRequest, user: AuthUser = Depends(get_current_user)):
    task_id = str(uuid.uuid4())
    mission_file = MISSIONS_DIR / f"web-task-{task_id}.md"
    result_file = MISSIONS_DIR / f"web-task-{task_id}_result.md"

    MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    mission_file.write_text(f"---\nfaceta: hyde\n---\n\n{req.command}\n")

    tenant_id = user.tenant_id
    user_id = user.user_id
    # Registro de dueño en disco (no sólo en memoria): GET /command/{task_id}
    # no tenía NINGÚN chequeo de autorización — cualquier usuario autenticado
    # que conociera (o adivinara/leyera del localStorage de otro) un task_id
    # ajeno podía leer su resultado completo. Ver ese endpoint más abajo.
    _owner_file(task_id).write_text(json.dumps({"tenant_id": tenant_id, "user_id": user_id}))

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
        owner = json.loads(_owner_file(task_id).read_text())
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
    result_file = _result_file(task_id)
    if result_file.exists():
        texto = result_file.read_text()
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
            texto = mission_file.read_text()
            # Antes que el resultado: GET no ve un result_file sin la marca.
            _anotar_en_duenio(task_id, "simulado", True)
            result_file.write_text(texto)
            codigo = "comando_simulado"
        else:
            proc = await asyncio.create_subprocess_exec(
                str(JAX_BIN), "--task", str(mission_file),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(Path.home()),
            )
            await proc.wait()
            # JAX escribe el resultado en result_file internamente.
            # No sobreescribir — solo leer.
            if result_file.exists():
                texto = result_file.read_text()
            else:
                texto = ""
                result_file.write_text("")
            if not texto:
                codigo = "comando_sin_resultado"
    except Exception as e:  # fail-soft: tarea de fondo: el fallo se publica como command_completed status='failed' con código y queda en el archivo de dueño; no hay falso éxito
        motivo = recortar_redactado(str(e), 400)
        try:
            _escribir_fallo(task_id, motivo)
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
