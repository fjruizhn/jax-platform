"""R4 Task 9 (ultima tarea del plan) — alta de motor/capability_motor desde
Admin. Mismo patron de auth/estructura que api/admin/models.py y
api/admin/facet_bindings.py (require_superadmin, pool via get_pool()).

Alcance original: crear + listar. Editar (PATCH /{key}) se agrego
2026-08-19 — hueco real: un motor sin faceta homonima (jax_local/ada/thot
tienen faceta y ya se sincronizan solos via approve_proposal, ver
api/admin/models.py) no tenia forma de cambiar de modelo sin SQL a mano.
Borrar sigue fuera de alcance.

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
                # motor_resolved (no motor): para una clave con faceta homonima
                # (ada/jax_local/kimi/thot hoy) resuelve el modelo real por
                # facet_binding, no por motor.model_ref -- ver
                # _eliminate_motor_model_ref_denormalization en migrations.py.
                "SELECT m.`key`, mo.provider_id, mo.model_id, m.transport, m.max_tokens, "
                "m.default_timeout_seconds, m.supports_reasoning, m.reasoning_default_visibility, "
                "m.sandbox_only, m.status "
                "FROM motor_resolved m "
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


class UpdateMotorRequest(BaseModel):
    provider_id: str | None = None
    model_id: str | None = None
    transport: str | None = None
    max_tokens: int | None = None
    default_timeout_seconds: int | None = None
    supports_reasoning: bool | None = None
    reasoning_default_visibility: str | None = None
    sandbox_only: bool | None = None
    status: str | None = None


@router.patch("/{key}")
async def update_motor(key: str, req: UpdateMotorRequest, user: AuthUser = Depends(require_superadmin)):
    """Actualizacion parcial de una fila existente de `motor` — solo los
    campos presentes en el body se tocan. provider_id/model_id van juntos
    (o ninguno): hacen falta ambos para resolver un model_ref valido,
    igual que create_motor.

    GUARDA (2026-08-19, actualizado 2026-08-24): si `key` coincide con una
    faceta existente (jax_local/ada/thot/kimi hoy), este endpoint RECHAZA
    un cambio de modelo -- pero desde 2026-08-24 esto es defensa en
    profundidad (evita que un admin crea que cambio algo y no pasa nada),
    NO el mecanismo que impide la divergencia: motor.model_ref ya no es
    fuente de identidad para una clave con faceta homonima, la vista
    `motor_resolved` (migrations.py) resuelve siempre por
    facet_binding.model_ref para esos casos. Aunque este guard se
    borrara manana, un UPDATE motor SET model_ref=... para 'ada' seguiria
    sin producir divergencia observable -- la vista jamas lo lee para esa
    fila. Los demas campos (transport/timeout/etc) si se pueden editar aca
    sin restriccion --
    no los rastrea facet_binding."""
    if (req.provider_id is None) != (req.model_id is None):
        raise HTTPException(
            status_code=422,
            detail="provider_id y model_id van juntos, o ninguno de los dos",
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM motor WHERE `key`=%s", (key,))
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail=f"Motor '{key}' no encontrado")

            if req.model_id is not None:
                await cur.execute("SELECT 1 FROM facet WHERE `key`=%s", (key,))
                if await cur.fetchone():
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"'{key}' tiene una faceta homonima — cambiar el modelo aca "
                            "desincronizaria motor.model_ref de facet_binding.model_ref. "
                            "Usar POST /api/admin/models/proposals + "
                            "POST /api/admin/models/proposals/{id}/approve, que actualiza "
                            "ambas tablas juntas."
                        ),
                    )

            sets: list[str] = []
            params: list = []

            if req.model_id is not None:
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
                            "o elegir otro modelo."
                        ),
                    )
                sets.append("model_ref=%s")
                params.append(row[0])

            for field in (
                "transport", "max_tokens", "default_timeout_seconds", "supports_reasoning",
                "reasoning_default_visibility", "sandbox_only", "status",
            ):
                value = getattr(req, field)
                if value is not None:
                    sets.append(f"{field}=%s")
                    params.append(value)

            if not sets:
                raise HTTPException(status_code=422, detail="Nada para actualizar")

            params.append(key)
            try:
                await cur.execute(f"UPDATE motor SET {', '.join(sets)} WHERE `key`=%s", params)
            except aiomysql.DataError as e:
                await conn.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=f"Dato invalido para motor '{key}' (transport/status fuera del ENUM?): {e}",
                )

            await cur.execute("SELECT transport FROM motor WHERE `key`=%s", (key,))
            (final_transport,) = await cur.fetchone()
        await conn.commit()
    return {"ok": True, "key": key, "dispatchable": final_transport in _DISPATCHABLE_TRANSPORTS}
