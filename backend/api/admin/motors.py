"""R4 Task 9 (ultima tarea del plan) — alta de motor/capability_motor desde
Admin. Mismo patron de auth/estructura que api/admin/models.py y
api/admin/facet_bindings.py (require_superadmin, pool via get_pool()).

Alcance minimo, igual que la referencia de Bloque D (provider/model): crear
+ listar. Editar/borrar quedan fuera — ninguno de los dos routers de
referencia los tiene tampoco.

Cablea el mismo mecanismo ya probado por INSERT directo en Task 4/8 (motor
kimi/ada/jax_local/thot, ver db/migrations.py): una fila en `motor` + N
filas en `capability_motor`, cero codigo de despacho nuevo — el picker de
Pipeline (api/motors.py) y las_manos/motor_registry/catalog.py::from_db()
leen exactamente estas mismas tablas de la DB jax_memory compartida.

LIMITACION CONOCIDA (Task 9 Step 7 del plan): un motor dado de alta acá
solo se despacha realmente en las_manos si su `transport` tiene un
dispatcher en worker.py::_TRANSPORT_DISPATCH — hoy solo
'http_openai_compat' y 'ollama'. El ENUM de la columna motor.transport
acepta tambien 'http_gemini'/'motor_registry'/'subprocess' (paridad con
facet.transport) y este endpoint no los bloquea, pero un job que resuelva
un motor con uno de esos transportes falla en tiempo de ejecucion con
"transport '...' no tiene dispatcher implementado" hasta que se sume su
funcion de dispatch — deuda con nombre, no bloqueante para cerrar R4. Se
expone via el campo `dispatchable` en la respuesta, para que el frontend lo
muestre en vez de fingir que ya funciona.
"""
import aiomysql
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin/motors")

# Mismo mapa que _TRANSPORT_DISPATCH en las_manos/motor_registry/worker.py
# (repo separado, DB compartida) — duplicado a proposito como dato de UI,
# no importado (los dos repos no se importan entre si).
_DISPATCHABLE_TRANSPORTS = {"http_openai_compat", "ollama"}

_TRANSPORT_VALUES = (
    "http_openai_compat", "http_gemini", "motor_registry", "ollama", "subprocess",
)


@router.get("")
async def list_motors(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT m.`key`, mo.provider_id, mo.model_id, m.transport, m.max_tokens, "
                "m.default_timeout_seconds, m.supports_reasoning, m.reasoning_default_visibility, "
                "m.sandbox_only, m.status "
                "FROM motor m "
                "JOIN model mo ON mo.id = m.model_ref "
                "ORDER BY m.`key`"
            )
            rows = await cur.fetchall()
            await cur.execute(
                "SELECT capability_key, motor_key, priority FROM capability_motor "
                "ORDER BY motor_key, priority ASC"
            )
            cap_rows = await cur.fetchall()

    caps_by_motor: dict[str, list[dict]] = {}
    for capability_key, motor_key, priority in cap_rows:
        caps_by_motor.setdefault(motor_key, []).append(
            {"capability_key": capability_key, "priority": priority}
        )

    motors = []
    for (key, provider_id, model_id, transport, max_tokens, timeout, reasoning,
         visibility, sandbox, status) in rows:
        motors.append({
            "key": key,
            "provider_id": provider_id,
            "model_id": model_id,
            "transport": transport,
            "max_tokens": max_tokens,
            "default_timeout_seconds": timeout,
            "supports_reasoning": bool(reasoning),
            "reasoning_default_visibility": visibility,
            "sandbox_only": bool(sandbox),
            "status": status,
            "dispatchable": transport in _DISPATCHABLE_TRANSPORTS,
            "capabilities": caps_by_motor.get(key, []),
        })
    return {"motors": motors, "transport_values": list(_TRANSPORT_VALUES),
            "dispatchable_transports": sorted(_DISPATCHABLE_TRANSPORTS)}


class CapabilityAttachment(BaseModel):
    capability_key: str
    priority: int = 0


class CreateMotorRequest(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    provider_id: str
    model_id: str
    transport: str
    max_tokens: int = 0
    default_timeout_seconds: int = 600
    supports_reasoning: bool = False
    reasoning_default_visibility: str = "audit_only"
    sandbox_only: bool = True
    status: str = "active"
    capabilities: list[CapabilityAttachment] = []


@router.post("")
async def create_motor(req: CreateMotorRequest, user: AuthUser = Depends(require_superadmin)):
    """Un motor nuevo = una fila en `motor` (FK a `model`, ya sembrado por
    sync/seed) + N filas en `capability_motor` (a que capabilities queda
    asignado, y con que prioridad). Sin esto ultimo el motor existe pero
    MotorPolicy._resolve_motor() nunca lo elige — ninguna capability lo
    listaria en su allowed_motors.

    NOTA: esta fila se escribe en la DB de inmediato, pero el motor no
    queda dispatchable via jax-las-manos.service hasta que ESE servicio
    (proceso separado) se reinicie — carga su catalogo de motores una
    sola vez al arrancar (Task 2). No hay forma de forzar un reload en
    caliente desde aqui; el operador debe reiniciar jax-las-manos.service
    para que el motor recien dado de alta quede activo."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM model WHERE provider_id=%s AND model_id=%s",
                (req.provider_id, req.model_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"No existe el modelo '{req.model_id}' para el provider "
                        f"'{req.provider_id}' en el catalogo (tabla `model`) — sincronizar "
                        "o elegir otro modelo antes de dar de alta el motor."
                    ),
                )
            model_ref = row[0]

            for cap in req.capabilities:
                await cur.execute("SELECT 1 FROM capability WHERE `key`=%s", (cap.capability_key,))
                if not await cur.fetchone():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Capability '{cap.capability_key}' no existe",
                    )

            try:
                await cur.execute(
                    "INSERT INTO motor (`key`, model_ref, transport, max_tokens, "
                    "default_timeout_seconds, supports_reasoning, reasoning_default_visibility, "
                    "sandbox_only, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (req.key, model_ref, req.transport, req.max_tokens, req.default_timeout_seconds,
                     req.supports_reasoning, req.reasoning_default_visibility, req.sandbox_only,
                     req.status),
                )
            except aiomysql.IntegrityError as e:
                await conn.rollback()
                raise HTTPException(status_code=409, detail=f"Motor '{req.key}' ya existe: {e}")
            except aiomysql.DataError as e:
                await conn.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=f"Dato invalido para motor '{req.key}' (transport/status fuera del ENUM?): {e}",
                )

            for cap in req.capabilities:
                await cur.execute(
                    "INSERT INTO capability_motor (capability_key, motor_key, priority) "
                    "VALUES (%s, %s, %s)",
                    (cap.capability_key, req.key, cap.priority),
                )
        await conn.commit()
    return {
        "ok": True,
        "key": req.key,
        "dispatchable": req.transport in _DISPATCHABLE_TRANSPORTS,
    }
