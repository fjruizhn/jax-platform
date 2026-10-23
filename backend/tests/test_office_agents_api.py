"""Database-backed contract for private Office agent creation and retry."""
from __future__ import annotations

import json
import uuid
import asyncio
import time

from auth.jwt import create_access_token
from tests.identidades import crear_usuario, sql
from virtual_office.engine_resolver import engine_binding_fingerprint


def _headers(user_id: int) -> dict[str, str]:
    return {"Authorization": "Bearer " + create_access_token(str(user_id), "1", "operator")}


async def _project_admin_with_engine():
    user_id, _ = await crear_usuario()
    await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (user_id,))
    project_id = await sql("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", ("office-agent-" + uuid.uuid4().hex,))
    await sql("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',NOW(6),'test',NOW(6))", (project_id,))
    await sql("INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))", (str(uuid.uuid4()), project_id, user_id))
    await sql("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, user_id, user_id))
    engine_key = "glm-" + uuid.uuid4().hex
    model_ref = (await sql("SELECT id FROM model WHERE provider_id='zhipu' AND model_id='glm-5.3' LIMIT 1", (), True))[0][0]
    rows = await sql(
        "SELECT p.id,p.base_url,p.auth_type,p.api_key_transport,p.status,p.is_local,m.id,m.provider_id,m.model_id,m.status,m.is_alias,m.context_window,m.supports_tool_use,m.supports_structured_output,m.max_tokens_param,m.max_output_tokens,m.input_modalities,c.id,c.provider_id,c.state,c.activated_at,c.revoked_at,SHA2(c.encrypted_value,256) FROM provider p JOIN model m ON m.provider_id=p.id JOIN credential c ON c.provider_id=p.id AND c.state='active' WHERE p.id='zhipu' AND m.id=%s",
        (model_ref,), True)
    assert len(rows) == 1
    row = rows[0]
    credential_id = int(row[17])
    await sql("INSERT INTO office_engine (engine_key,display_name_key,model_ref,credential_id,transport,allowed_data_classifications,binding_fingerprint,enabled,activation_epoch,version) VALUES (%s,'GLM',%s,%s,'http_openai_compat',%s,%s,FALSE,0,1)", (engine_key, model_ref, credential_id, json.dumps(["INTERNAL_CONFIDENTIAL"]), "0" * 64))
    provider_fp = await sql("SELECT SHA2(CAST(JSON_ARRAY('office-engine-provider-v1',%s,%s,%s,%s,%s,%s) AS CHAR),256)", tuple(row[:6]), True)
    model_fp = await sql("SELECT SHA2(CAST(JSON_ARRAY('office-engine-model-v1',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS CHAR),256)", tuple(row[6:17]), True)
    credential_fp = await sql("SELECT SHA2(CAST(JSON_ARRAY('office-engine-credential-v1',%s,%s,%s,%s,%s,%s) AS CHAR),256)", tuple(row[17:23]), True)
    fingerprint = engine_binding_fingerprint(engine_key=engine_key, model_ref=int(model_ref), transport="http_openai_compat", allowed_data_classifications=frozenset({"INTERNAL_CONFIDENTIAL"}), provider_fingerprint=provider_fp[0][0], model_fingerprint=model_fp[0][0], credential_id=credential_id, credential_fingerprint=credential_fp[0][0], engine_enabled=1, activation_epoch=1, version=1)
    await sql("UPDATE office_engine SET binding_fingerprint=%s,enabled=TRUE,activation_epoch=1 WHERE engine_key=%s", (fingerprint, engine_key))
    return user_id, project_id, engine_key


def test_project_admin_creates_and_runs_an_internal_agent_once(client, monkeypatch):
    import api.virtual_office.agents as agents_api

    monkeypatch.setenv("OFFICE_BROKER_ALLOWED_BASE_URLS", "https://api.z.ai/api/paas/v4")
    admin, project, engine_key = client.portal.call(_project_admin_with_engine)
    calls = []

    async def fake_dispatch(_target, *, instructions, user_input, **_kwargs):
        calls.append((instructions, user_input))
        return "respuesta GLM"

    monkeypatch.setattr(agents_api, "dispatch", fake_dispatch)
    headers = _headers(admin)
    engines = client.get(f"/api/virtual-office/projects/{project}/engines", headers=headers)
    assert engines.status_code == 200
    assert {"engine_key": engine_key, "display_name": "GLM", "model_id": "glm-5.3", "allowed_data_classifications": ["INTERNAL_CONFIDENTIAL"]} in engines.json()["items"]
    created = client.post(f"/api/virtual-office/projects/{project}/agents", headers=headers, json={"agent_key": "writer", "display_name": "Writer", "engine_key": engine_key, "data_classification": "INTERNAL_CONFIDENTIAL", "instructions": "Responde breve."})
    assert created.status_code == 201
    duplicate = client.post(f"/api/virtual-office/projects/{project}/agents", headers=headers, json={"agent_key": "writer", "display_name": "Writer", "engine_key": engine_key, "data_classification": "INTERNAL_CONFIDENTIAL", "instructions": "Responde breve."})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "office_agent_key_conflict"
    agent_id = created.json()["agent_id"]
    body = {"input": "hola", "idempotency_key": "run-1"}
    first = client.post(f"/api/virtual-office/projects/{project}/agents/{agent_id}/runs", headers=headers, json=body)
    second = client.post(f"/api/virtual-office/projects/{project}/agents/{agent_id}/runs", headers=headers, json=body)
    assert first.status_code == second.status_code == 202
    assert first.json()["status"] == second.json()["status"] == "SUCCEEDED"
    assert first.json()["run_id"] == second.json()["run_id"]
    assert calls == [("Responde breve.", "hola")]

    other_user, _ = client.portal.call(crear_usuario)
    client.portal.call(sql, "INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (other_user,))
    client.portal.call(sql, "INSERT INTO jax_project_membership (membership_id,project_id,tenant_id,user_id,project_role,status,created_at,created_by,updated_at) VALUES (%s,%s,1,%s,'CONTRIBUTOR','ACTIVE',NOW(6),'test',NOW(6))", (str(uuid.uuid4()), project, other_user))
    client.portal.call(sql, "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','INTERNAL','ACTIVE',%s)", (project, other_user, admin))
    cross_actor = client.post(f"/api/virtual-office/projects/{project}/agents/{agent_id}/runs", headers=_headers(other_user), json=body)
    assert cross_actor.status_code == 202
    assert cross_actor.json()["run_id"] != first.json()["run_id"]
    assert cross_actor.json()["output"] == "respuesta GLM"
    foreign_get = client.get(f"/api/virtual-office/projects/{project}/agents/{agent_id}/runs/{cross_actor.json()['run_id']}", headers=headers)
    assert foreign_get.status_code == 404
    assert calls == [("Responde breve.", "hola"), ("Responde breve.", "hola")]

    async def replay_while_dispatch_is_in_flight():
        from api.virtual_office.agents import AgentRunRequest, get_agent_run, run_agent
        from auth.models import AuthUser

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_dispatch(_target, *, instructions, user_input, **_kwargs):
            calls.append((instructions, user_input))
            started.set()
            await release.wait()
            return "respuesta GLM lenta"

        monkeypatch.setattr(agents_api, "dispatch", slow_dispatch)
        await sql("UPDATE office_agent_runtime_config SET timeout_seconds=1,stale_seconds=1 WHERE config_id=1")
        user = AuthUser(user_id=str(admin), tenant_id="1", role="operator", account_class="INTERNAL")
        request = AgentRunRequest(input="carrera", idempotency_key="in-flight")
        first_run = asyncio.create_task(run_agent(project, agent_id, request, user))
        await started.wait()
        replay = await run_agent(project, agent_id, request, user)
        await asyncio.sleep(1.05)
        expired_replay = await run_agent(project, agent_id, request, user)
        stale = await get_agent_run(project, agent_id, replay["run_id"], user)
        release.set()
        completed = await first_run
        assert replay["status"] == "DISPATCHING"
        assert stale["status"] == "UNKNOWN"
        assert expired_replay["status"] == "UNKNOWN"
        assert completed["status"] == "UNKNOWN"
        assert replay["run_id"] == completed["run_id"]

    client.portal.call(replay_while_dispatch_is_in_flight)
    assert calls == [("Responde breve.", "hola"), ("Responde breve.", "hola"), ("Responde breve.", "carrera")]

    # #355 has not granted clients any visibility or execution of private agents.
    calls_before_external_attempt = list(calls)
    client.portal.call(sql, "UPDATE jax_users SET account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (admin,))
    denied = client.post(
        f"/api/virtual-office/projects/{project}/agents/{agent_id}/runs",
        headers=headers,
        json={"input": "no debe salir", "idempotency_key": "external-denied"},
    )
    assert denied.status_code == 403
    assert calls == calls_before_external_attempt


def test_private_agent_mocked_provider_handles_parallel_runs_on_a_real_database(client, monkeypatch):
    """A bounded local load probe: durable run rows plus mocked provider I/O."""
    import api.virtual_office.agents as agents_api
    from api.virtual_office.agents import AgentRunRequest, run_agent
    from auth.models import AuthUser

    monkeypatch.setenv("OFFICE_BROKER_ALLOWED_BASE_URLS", "https://api.z.ai/api/paas/v4")
    admin, project, engine_key = client.portal.call(_project_admin_with_engine)
    headers = _headers(admin)
    created = client.post(f"/api/virtual-office/projects/{project}/agents", headers=headers, json={"agent_key": "load", "display_name": "Load", "engine_key": engine_key, "data_classification": "INTERNAL_CONFIDENTIAL", "instructions": "Responde breve."})
    assert created.status_code == 201
    agent_id = created.json()["agent_id"]

    async def fake_dispatch(_target, *, instructions, user_input, **_kwargs):
        await asyncio.sleep(0.001)
        return user_input

    monkeypatch.setattr(agents_api, "dispatch", fake_dispatch)

    async def run_probe():
        user = AuthUser(user_id=str(admin), tenant_id="1", role="operator", account_class="INTERNAL")

        async def one(index: int):
            started = time.perf_counter()
            reply = await run_agent(project, agent_id, AgentRunRequest(input=f"load-{index}", idempotency_key=f"load-{index}"), user)
            return reply, time.perf_counter() - started

        batch_started = time.perf_counter()
        measured = await asyncio.gather(*(one(index) for index in range(2)))
        batch_seconds = time.perf_counter() - batch_started
        durations = sorted(duration for _reply, duration in measured)
        p95 = durations[round((len(durations) - 1) * 0.95)]
        assert all(reply["status"] == "SUCCEEDED" for reply, _duration in measured)
        assert p95 < 2.0
        print(f"office-agent mocked-provider load: concurrency=2 p95_seconds={p95:.4f} throughput_rps={len(measured) / batch_seconds:.1f}")

    client.portal.call(run_probe)
