import asyncio
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import get_current_user
from auth.models import AuthUser
from jax_engine.events import event_bus
from jax_engine.schemas import JAXEvent
from jax_engine.state import engine_state

router = APIRouter(prefix="/api")

MISSIONS_DIR = Path.home() / "jax" / "missions"
JAX_BIN = Path.home() / ".local" / "bin" / "jax"


def _owner_file(task_id: str) -> Path:
    return MISSIONS_DIR / f"web-task-{task_id}_owner.json"


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

    start_event = JAXEvent(
        event_type="command_started",
        tenant_id=tenant_id,
        user_id=user_id,
        payload={"task_id": task_id, "command_preview": req.command[:100]},
    )
    await event_bus.publish(start_event)

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
        raise HTTPException(status_code=400, detail="task_id inválido")

    # 404 (no 403) para no confirmarle a un no-dueño que el task_id existe.
    # Tareas creadas antes de este cambio no tienen owner file y también
    # devuelven 404 — costo único de la migración, no un bug.
    try:
        owner = json.loads(_owner_file(task_id).read_text())
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    if owner.get("user_id") != user.user_id or owner.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")

    result_file = MISSIONS_DIR / f"web-task-{task_id}_result.md"
    if result_file.exists():
        return {"status": "completed", "result": result_file.read_text()}
    return {"status": "running"}


async def _run_command(
    task_id: str,
    mission_file: Path,
    result_file: Path,
    tenant_id: str,
    user_id: str,
    mode: str,
):
    try:
        if mode == "dry_run":
            result_file.write_text(f"[DRY RUN] Tarea registrada:\n\n{mission_file.read_text()}")
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
            result_text = result_file.read_text()
        else:
            result_text = "[Sin resultado — JAX no produjo output]"
            result_file.write_text(result_text)

        done_event = JAXEvent(
            event_type="command_completed",
            tenant_id=tenant_id,
            user_id=user_id,
            payload={
                "task_id": task_id,
                "status": "completed",
                "result": result_text,
                "result_preview": result_text[:500],
            },
        )
        await event_bus.publish(done_event)
        await engine_state.set_facet_status("hyde", "idle", tenant_id, user_id)

    except Exception as e:
        err = str(e)
        result_file.write_text(f"Error ejecutando tarea: {err}")
        fail_event = JAXEvent(
            event_type="command_completed",
            tenant_id=tenant_id,
            user_id=user_id,
            payload={
                "task_id": task_id,
                "status": "failed",
                "result": f"Error: {err}",
                "result_preview": f"Error: {err[:400]}",
            },
        )
        await event_bus.publish(fail_event)
        await engine_state.set_facet_status("hyde", "idle", tenant_id, user_id)
