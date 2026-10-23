"""Project-private Office agents and their single-provider execution path."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from virtual_office.agent_broker import OfficeBrokerUnavailable, dispatch, load_dispatch_target_in_cursor
from virtual_office.audit import record_authorization_event
from virtual_office.authority import authorize_office_in_cursor
from virtual_office.capabilities import OfficeOperation
from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_in_cursor

router = APIRouter(prefix="/api/virtual-office/projects")


class AgentCreate(BaseModel):
    agent_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=128, pattern=r".*\S.*")
    engine_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    data_classification: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    instructions: str = Field(min_length=1, max_length=32000, pattern=r".*\S.*")


class AgentBindingUpdate(BaseModel):
    engine_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    expected_version: int = Field(ge=1)


class AgentRunRequest(BaseModel):
    input: str = Field(min_length=1, max_length=32000, pattern=r".*\S.*")
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def _actor_id(user: AuthUser) -> int:
    try:
        actor = int(user.user_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=401, detail="sesion_invalida") from error
    if actor < 1:
        raise HTTPException(status_code=401, detail="sesion_invalida")
    return actor


async def _decision(cur, actor: int, operation: OfficeOperation, project_id: int):
    decision = await authorize_office_in_cursor(cur, actor, operation, project_id)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail="office_agent_denied")
    return decision


async def _agent_row(cur, project_id: int, agent_id: int, *, lock: bool = False):
    await cur.execute(
        "SELECT a.agent_id,a.agent_key,a.display_name,a.instructions,a.data_classification,a.status,a.version,"
        "b.binding_id,b.binding_version,b.engine_key,e.model_ref,m.model_id FROM office_agent a "
        "JOIN office_agent_binding b ON b.agent_id=a.agent_id AND b.active=TRUE "
        "JOIN office_engine e ON e.engine_key=b.engine_key JOIN model m ON m.id=e.model_ref "
        "WHERE a.project_id=%s AND a.agent_id=%s" + (" FOR UPDATE" if lock else ""), (project_id, agent_id))
    return await cur.fetchone()


def _agent_response(row) -> dict:
    return {"agent_id": int(row[0]), "agent_key": str(row[1]), "display_name": str(row[2]),
            "engine_key": str(row[9]), "model_id": str(row[11]), "data_classification": str(row[4]),
            "version": int(row[6]), "status": str(row[5])}


@router.get("/{project_id}/engines")
async def list_engines(project_id: int, user: AuthUser = Depends(get_current_user)):
    """Small selector catalogue; it intentionally contains no route or secret."""
    actor = _actor_id(user)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await _decision(cur, actor, OfficeOperation.MANAGE_PROJECT, project_id)
        await cur.execute(
            "SELECT e.engine_key,e.display_name_key,m.model_id,e.allowed_data_classifications FROM office_engine e "
            "JOIN model m ON m.id=e.model_ref WHERE e.enabled=TRUE ORDER BY e.engine_key")
        rows = await cur.fetchall()
    return {"items": [{"engine_key": str(row[0]), "display_name": str(row[1]), "model_id": str(row[2]),
                       "allowed_data_classifications": json.loads(row[3])} for row in rows]}


@router.get("/{project_id}/agents")
async def list_agents(project_id: int, user: AuthUser = Depends(get_current_user)):
    actor = _actor_id(user)
    if user.account_class == "EXTERNAL_CLIENT":
        raise HTTPException(status_code=403, detail="office_agent_denied")
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await _decision(cur, actor, OfficeOperation.VIEW_PROJECT, project_id)
        await cur.execute(
            "SELECT a.agent_id,a.agent_key,a.display_name,a.instructions,a.data_classification,a.status,a.version,"
            "b.binding_id,b.binding_version,b.engine_key,e.model_ref,m.model_id FROM office_agent a JOIN office_agent_binding b "
            "ON b.agent_id=a.agent_id AND b.active=TRUE JOIN office_engine e ON e.engine_key=b.engine_key "
            "JOIN model m ON m.id=e.model_ref WHERE a.project_id=%s ORDER BY a.agent_id", (project_id,))
        rows = await cur.fetchall()
    return {"items": [_agent_response(row) for row in rows]}


@router.post("/{project_id}/agents", status_code=201)
async def create_agent(project_id: int, payload: AgentCreate, user: AuthUser = Depends(get_current_user)):
    actor = _actor_id(user)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await _decision(cur, actor, OfficeOperation.MANAGE_PROJECT, project_id)
        try:
            await resolve_office_engine_in_cursor(cur, engine_key=payload.engine_key,
                                                  data_classification=payload.data_classification)
        except OfficeEngineUnavailable as error:
            raise HTTPException(status_code=409, detail="office_engine_unavailable") from error
        try:
            await cur.execute("INSERT INTO office_agent (project_id,agent_key,display_name,instructions,data_classification,created_by) VALUES (%s,%s,%s,%s,%s,%s)",
                              (project_id, payload.agent_key, payload.display_name, payload.instructions, payload.data_classification, actor))
            agent_id = int(cur.lastrowid)
            await cur.execute("INSERT INTO office_agent_binding (agent_id,engine_key,binding_version,bound_by) VALUES (%s,%s,1,%s)",
                              (agent_id, payload.engine_key, actor))
        except Exception as error:
            if getattr(error, "args", ()) and error.args[0] == 1062:
                raise HTTPException(status_code=409, detail="office_agent_key_conflict") from error
            raise
        row = await _agent_row(cur, project_id, agent_id)
    return _agent_response(row)


@router.patch("/{project_id}/agents/{agent_id}/binding")
async def change_agent_binding(project_id: int, agent_id: int, payload: AgentBindingUpdate,
                               user: AuthUser = Depends(get_current_user)):
    actor = _actor_id(user)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await _decision(cur, actor, OfficeOperation.MANAGE_PROJECT, project_id)
        row = await _agent_row(cur, project_id, agent_id, lock=True)
        if row is None:
            raise HTTPException(status_code=404, detail="office_agent_not_found")
        if int(row[6]) != payload.expected_version:
            raise HTTPException(status_code=409, detail="office_agent_version_conflict")
        try:
            await resolve_office_engine_in_cursor(cur, engine_key=payload.engine_key,
                                                  data_classification=str(row[4]))
        except OfficeEngineUnavailable as error:
            raise HTTPException(status_code=409, detail="office_engine_unavailable") from error
        await cur.execute("UPDATE office_agent_binding SET active=FALSE WHERE agent_id=%s AND active=TRUE", (agent_id,))
        await cur.execute("INSERT INTO office_agent_binding (agent_id,engine_key,binding_version,bound_by) VALUES (%s,%s,%s,%s)",
                          (agent_id, payload.engine_key, int(row[6]) + 1, actor))
        await cur.execute("UPDATE office_agent SET version=version+1 WHERE agent_id=%s AND version=%s", (agent_id, payload.expected_version))
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="office_agent_version_conflict")
        updated = await _agent_row(cur, project_id, agent_id)
    return _agent_response(updated)


@router.post("/{project_id}/agents/{agent_id}/runs", status_code=202)
async def run_agent(project_id: int, agent_id: int, payload: AgentRunRequest,
                    user: AuthUser = Depends(get_current_user)):
    """Create one durable run before egress; retries never make a second call."""
    actor = _actor_id(user)
    if user.account_class == "EXTERNAL_CLIENT":
        raise HTTPException(status_code=403, detail="office_agent_denied")
    raw_input = payload.input.encode("utf-8")
    digest = hashlib.sha256(b"office-agent-run/v1\0" + raw_input).digest()
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        decision = await _decision(cur, actor, OfficeOperation.VIEW_PROJECT, project_id)
        row = await _agent_row(cur, project_id, agent_id, lock=True)
        if row is None or row[5] != "ACTIVE":
            raise HTTPException(status_code=404, detail="office_agent_not_found")
        await cur.execute("SELECT run_id,input_sha256,status,output_text,model_id FROM office_agent_run WHERE requested_by=%s AND agent_id=%s AND idempotency_key=%s FOR UPDATE", (actor, agent_id, payload.idempotency_key))
        prior = await cur.fetchone()
        if prior is not None:
            if prior[1] != digest:
                raise HTTPException(status_code=409, detail="office_run_idempotency_conflict")
            return {"run_id": str(prior[0]), "status": str(prior[2]), "output": prior[3], "model_id": str(prior[4])}
        await cur.execute("SELECT config_version,max_output_tokens,max_input_bytes,max_instructions_bytes,max_response_bytes,window_seconds,actor_runs_per_window,project_runs_per_window,actor_concurrency,project_concurrency,timeout_seconds,stale_seconds FROM office_agent_runtime_config WHERE config_id=1 FOR UPDATE")
        config = await cur.fetchone()
        if config is None:
            raise HTTPException(status_code=503, detail="office_agent_runtime_unavailable")
        if len(raw_input) > int(config[2]) or len(str(row[3]).encode("utf-8")) > int(config[3]):
            raise HTTPException(status_code=422, detail="office_agent_input_too_large")
        await cur.execute("SELECT COUNT(*) FROM office_agent_run WHERE requested_by=%s AND accepted_at>=UTC_TIMESTAMP(6)-INTERVAL %s SECOND", (actor, int(config[5])))
        if int((await cur.fetchone())[0]) >= int(config[6]):
            raise HTTPException(status_code=429, detail="office_agent_quota_exceeded")
        await cur.execute("SELECT COUNT(*) FROM office_agent_run WHERE project_id=%s AND accepted_at>=UTC_TIMESTAMP(6)-INTERVAL %s SECOND", (project_id, int(config[5])))
        if int((await cur.fetchone())[0]) >= int(config[7]):
            raise HTTPException(status_code=429, detail="office_agent_quota_exceeded")
        await cur.execute("SELECT COUNT(*) FROM office_agent_run WHERE requested_by=%s AND status='DISPATCHING' AND deadline_at>UTC_TIMESTAMP(6)", (actor,))
        if int((await cur.fetchone())[0]) >= int(config[8]):
            raise HTTPException(status_code=429, detail="office_agent_quota_exceeded")
        await cur.execute("SELECT COUNT(*) FROM office_agent_run WHERE project_id=%s AND status='DISPATCHING' AND deadline_at>UTC_TIMESTAMP(6)", (project_id,))
        if int((await cur.fetchone())[0]) >= int(config[9]):
            raise HTTPException(status_code=429, detail="office_agent_quota_exceeded")
        try:
            engine = await resolve_office_engine_in_cursor(cur, engine_key=str(row[9]), data_classification=str(row[4]), for_update=True)
            target = await load_dispatch_target_in_cursor(cur, engine=engine)
        except (OfficeEngineUnavailable, OfficeBrokerUnavailable) as error:
            raise HTTPException(status_code=409, detail="office_engine_unavailable") from error
        run_id = str(uuid.uuid4())
        authorization_event_id = await record_authorization_event(cur, decision, correlation_id=run_id)
        instructions_digest = hashlib.sha256(str(row[3]).encode("utf-8")).digest()
        snapshot = hashlib.sha256(b"office-agent-dispatch/v1\0" + digest + instructions_digest + engine.engine_binding_fingerprint.encode()).digest()
        await cur.execute("INSERT INTO office_agent_run (run_id,agent_id,project_id,requested_by,authorization_event_id,agent_version,binding_id,binding_version,idempotency_key,input_sha256,instructions_sha256,dispatch_snapshot_sha256,config_version,engine_key,engine_version,engine_binding_fingerprint,model_ref,model_id,credential_id,credential_fingerprint,provider_id,auth_type,auth_transport,status,deadline_at,fence) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DISPATCHING',UTC_TIMESTAMP(6)+INTERVAL %s SECOND,1)", (run_id,agent_id,project_id,actor,authorization_event_id,int(row[6]),int(row[7]),int(row[8]),payload.idempotency_key,digest,instructions_digest,snapshot,int(config[0]),engine.engine_key,engine.version,engine.engine_binding_fingerprint,engine.model_ref,engine.model_id,engine.credential_id,engine.credential_fingerprint,engine.provider_id,engine.provider_auth_type,engine.provider_api_key_transport,int(config[11])))
    try:
        output = await dispatch(target, instructions=str(row[3]), user_input=payload.input, max_tokens_field=engine.max_tokens_param, max_output_tokens=min(int(config[1]), engine.max_output_tokens), max_response_bytes=int(config[4]), timeout_seconds=int(config[10]))
    except OfficeBrokerUnavailable:
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            await cur.execute("UPDATE office_agent_run SET status='UNKNOWN',completed_at=UTC_TIMESTAMP(6) WHERE run_id=%s AND status='DISPATCHING'", (run_id,))
        return {"run_id": run_id, "status": "UNKNOWN", "output": None, "model_id": engine.model_id}
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await cur.execute("UPDATE office_agent_run SET status='SUCCEEDED',output_text=%s,completed_at=UTC_TIMESTAMP(6) WHERE run_id=%s AND status='DISPATCHING'", (output, run_id))
        if cur.rowcount != 1:
            return {"run_id": run_id, "status": "UNKNOWN", "output": None, "model_id": engine.model_id}
    return {"run_id": run_id, "status": "SUCCEEDED", "output": output, "model_id": engine.model_id}


@router.get("/{project_id}/agents/{agent_id}/runs/{run_id}")
async def get_agent_run(project_id: int, agent_id: int, run_id: str, user: AuthUser = Depends(get_current_user)):
    actor = _actor_id(user)
    if user.account_class == "EXTERNAL_CLIENT":
        raise HTTPException(status_code=403, detail="office_agent_denied")
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await _decision(cur, actor, OfficeOperation.VIEW_PROJECT, project_id)
        await cur.execute("UPDATE office_agent_run SET status='UNKNOWN',completed_at=UTC_TIMESTAMP(6) WHERE run_id=%s AND agent_id=%s AND project_id=%s AND requested_by=%s AND status='DISPATCHING' AND deadline_at<=UTC_TIMESTAMP(6)", (run_id, agent_id, project_id, actor))
        await cur.execute("SELECT status,output_text,model_id FROM office_agent_run WHERE run_id=%s AND agent_id=%s AND project_id=%s AND requested_by=%s", (run_id, agent_id, project_id, actor))
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="office_agent_run_not_found")
    return {"run_id": run_id, "status": str(row[0]), "output": row[1], "model_id": str(row[2])}
