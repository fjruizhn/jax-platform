from pydantic import BaseModel, Field
from typing import Any, Literal
from tiempo import utc_ahora
import uuid

EventType = Literal[
    "facet_status_changed",
    "pipeline_step_changed",
    "human_gate_requested",
    "kill_switch_activated",
    "kill_switch_released",
    "las_manos_health_changed",
    "facet_response_completed",
    "heartbeat",
    "command_completed",
]

FacetStatus = Literal["idle", "thinking", "error", "offline"]

PipelineStatus = Literal["pending", "running", "waiting_gate", "completed", "failed"]


class JAXEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    tenant_id: str
    user_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: utc_ahora().isoformat() + "Z")


class FacetState(BaseModel):
    name: str
    status: FacetStatus = "idle"
    last_message: str = ""
    last_update: str = Field(default_factory=lambda: utc_ahora().isoformat() + "Z")
    # Tabla `facet` (Bloque C), cargado al arrancar (A-48). El color no vive
    # acá: el frontend deriva el token de la clave (tema-tokens §7.3, A-42).
    display_name: str | None = None


class PipelineStep(BaseModel):
    step_id: str
    name: str
    status: str = "pending"
    facet: str = ""
    duration_ms: int = 0
    output: str = ""


class PipelineState(BaseModel):
    pipeline_id: str
    tenant_id: str
    user_id: str = ""
    name: str
    status: PipelineStatus = "pending"
    steps: list[PipelineStep] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: utc_ahora().isoformat() + "Z")
    updated_at: str = Field(default_factory=lambda: utc_ahora().isoformat() + "Z")


class UserSession(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    connected_at: str = Field(default_factory=lambda: utc_ahora().isoformat() + "Z")


class EcosystemState(BaseModel):
    facets: dict[str, FacetState] = Field(default_factory=dict)
    active_pipelines: dict[str, PipelineState] = Field(default_factory=dict)
    las_manos_alive: bool = False
    connected_users: dict[str, UserSession] = Field(default_factory=dict)
    last_health_check: str = ""
