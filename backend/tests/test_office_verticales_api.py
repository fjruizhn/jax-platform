"""Vertical registry announcement and project-vertical wiring (OP 358 pieza 4).

The registry of salas (ids, estados, order) is announced by the server; the
browser only renders it.  These tests pin: the announcement contract, that
creation-time membership in the registry only accepts an ACTIVE room, the
list/detail exposure of the assignment, the list filter (registered rooms
filter, unknown ids are rejected), and that external publication keeps
nulling the description while the vertical — an organizational fact like the
name — stays visible.
"""
from __future__ import annotations

import time
import uuid

from auth.jwt import create_access_token
from fastapi.testclient import TestClient
from office_main import app as dedicated_office_app
from tests.identidades import crear_usuario, sql


def _headers(user_id: int, tenant_id: int = 1):
    return {"Authorization": "Bearer " + create_access_token(str(user_id), str(tenant_id), "operator")}


async def _office_root_with_current_proof(*, role: str = "OFFICE_SUPERUSER"):
    user_id, _ = await crear_usuario()
    await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,%s,'ACTIVE')", (user_id, role))
    versions = (await sql("SELECT token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True))[0]
    event_id = await sql(
        "INSERT INTO office_mfa_security_event "
        "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
        "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,UTC_TIMESTAMP(6))",
        (user_id, user_id, int(versions[0]), int(versions[1]), uuid.uuid4().bytes),
    )
    return user_id, int(versions[0]), int(versions[1]), event_id


def _proof_headers(user_id: int, token_version: int, mfa_version: int, proof_id: int, key: str):
    token = create_access_token(str(user_id), "1", "operator", token_version,
                                mfa_proof_event_id=proof_id, mfa_version=mfa_version,
                                mfa_at=int(time.time()))
    return {"Authorization": "Bearer " + token, "Idempotency-Key": key}


def _project_payload(**extra):
    payload = {"tenant_id": 1, "name": "office-vertical-" + uuid.uuid4().hex[:12], "description": "confidential"}
    payload.update(extra)
    return payload


def _configure_mfa(monkeypatch):
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120", "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300", "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)


def test_verticales_endpoint_announces_the_server_registry(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, _tv, _mv, _proof = client.portal.call(_office_root_with_current_proof)
    response = client.get("/api/virtual-office/verticales", headers=_headers(root))
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    announced = response.json()["verticales"]
    assert [vertical["id"] for vertical in announced] == [
        "junta", "agencia", "bufete", "arquitectura", "contable", "banca", "biblioteca",
    ]
    assert [vertical["estado"] for vertical in announced].count("activa") == 1
    assert next(vertical for vertical in announced if vertical["id"] == "agencia")["estado"] == "activa"
    dedicated = TestClient(dedicated_office_app).get(
        "/api/virtual-office/verticales", headers=_headers(root))
    assert dedicated.status_code == 200
    assert dedicated.json() == response.json()


def test_verticales_endpoint_requires_an_active_office_profile(client, monkeypatch):
    _configure_mfa(monkeypatch)
    stranger, _ = client.portal.call(crear_usuario)
    denied = client.get("/api/virtual-office/verticales", headers=_headers(stranger))
    assert denied.status_code == 403
    assert denied.headers["Cache-Control"] == "no-store"


def test_create_assigns_vertical_and_exposes_it_in_detail_list_and_replay(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    key = str(uuid.uuid4())
    payload = _project_payload(vertical="agencia")
    created = client.post("/api/virtual-office/projects", json=payload, headers=_proof_headers(root, tv, mv, proof, key))
    assert created.status_code == 200
    assert created.json()["created"] is True
    project_id = created.json()["id"]
    replay = client.post("/api/virtual-office/projects", json=payload, headers=_proof_headers(root, tv, mv, proof, key))
    assert replay.status_code == 200
    assert replay.json() == {"id": project_id, "created": False}
    assert client.portal.call(
        sql, "SELECT vertical FROM office_project_vertical WHERE project_id=%s", (project_id,), True
    ) == (("agencia",),)
    detail = client.get(f"/api/virtual-office/projects/{project_id}", headers=_headers(root))
    assert detail.status_code == 200
    assert detail.json()["vertical"] == "agencia"
    page = client.get(f"/api/virtual-office/projects?after_id={project_id - 1}", headers=_headers(root)).json()["projects"]
    row = next(project for project in page if project["id"] == project_id)
    assert row["vertical"] == "agencia"


def test_create_without_vertical_leaves_the_project_general(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    created = client.post(
        "/api/virtual-office/projects",
        json=_project_payload(),
        headers=_proof_headers(root, tv, mv, proof, str(uuid.uuid4())),
    )
    assert created.status_code == 200
    project_id = created.json()["id"]
    assert client.portal.call(
        sql, "SELECT COUNT(*) FROM office_project_vertical WHERE project_id=%s", (project_id,), True
    ) == ((0,),)
    detail = client.get(f"/api/virtual-office/projects/{project_id}", headers=_headers(root))
    assert detail.status_code == 200
    assert detail.json()["vertical"] is None


def test_create_rejects_inactive_or_unknown_verticals(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    for vertical in ("banca", "desconocida"):
        response = client.post(
            "/api/virtual-office/projects",
            json=_project_payload(vertical=vertical),
            headers=_proof_headers(root, tv, mv, proof, str(uuid.uuid4())),
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "office_project_vertical_invalid"


def test_replay_without_then_with_vertical_repairs_the_missing_companion(client, monkeypatch):
    # The JAX idempotency digest does not include the vertical (only the
    # form signature does), so a same-key replay that adds a room is not a
    # digest conflict: Phase B repairs the missing companion row.  This
    # test pins that current behavior explicitly.
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    key = str(uuid.uuid4())
    payload = _project_payload()
    created = client.post("/api/virtual-office/projects", json=payload, headers=_proof_headers(root, tv, mv, proof, key))
    assert created.status_code == 200
    project_id = created.json()["id"]
    payload["vertical"] = "agencia"
    replay = client.post("/api/virtual-office/projects", json=payload, headers=_proof_headers(root, tv, mv, proof, key))
    assert replay.status_code == 200
    assert replay.json() == {"id": project_id, "created": False}
    assert client.portal.call(
        sql, "SELECT vertical FROM office_project_vertical WHERE project_id=%s", (project_id,), True
    ) == (("agencia",),)


def test_phase_b_vertical_drift_is_retryable_and_never_rewrites(client, monkeypatch):
    # A companion row whose value differs from the authorized intent (torn
    # state, manual repair, a future migration) is never silently rewritten:
    # Phase B refuses with a retryable 503 and leaves the stored value alone.
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    key = str(uuid.uuid4())
    payload = _project_payload(vertical="agencia")
    created = client.post("/api/virtual-office/projects", json=payload, headers=_proof_headers(root, tv, mv, proof, key))
    assert created.status_code == 200
    project_id = created.json()["id"]
    # Only SQL can produce a divergent value today: creation validates the
    # payload against the registry before Phase B ever runs.
    client.portal.call(sql, "UPDATE office_project_vertical SET vertical='banca' WHERE project_id=%s", (project_id,))
    replay = client.post("/api/virtual-office/projects", json=payload, headers=_proof_headers(root, tv, mv, proof, key))
    assert replay.status_code == 503
    assert replay.json()["detail"] == "office_project_initialization_retryable"
    assert client.portal.call(
        sql, "SELECT vertical FROM office_project_vertical WHERE project_id=%s", (project_id,), True
    ) == (("banca",),)


def test_list_filter_keeps_registered_verticals_and_rejects_unknown_ones(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)
    in_room = client.post(
        "/api/virtual-office/projects",
        json=_project_payload(vertical="agencia"),
        headers=_proof_headers(root, tv, mv, proof, str(uuid.uuid4())),
    ).json()["id"]
    general = client.post(
        "/api/virtual-office/projects",
        json=_project_payload(),
        headers=_proof_headers(root, tv, mv, proof, str(uuid.uuid4())),
    ).json()["id"]
    page = f"/api/virtual-office/projects?after_id={in_room - 1}"
    assert [project["id"] for project in client.get(page + "&vertical=agencia", headers=_headers(root)).json()["projects"]] == [in_room]
    # A registered but not-yet-active room is an honest empty page, not an error.
    assert client.get(page + "&vertical=banca", headers=_headers(root)).json()["projects"] == []
    rejected = client.get(page + "&vertical=desconocida", headers=_headers(root))
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "office_vertical_invalid"
    # Without a filter the list stays the canonical, unfiltered one.
    ids = [project["id"] for project in client.get(page, headers=_headers(root)).json()["projects"]]
    assert in_room in ids and general in ids


def test_external_publication_nulls_description_but_keeps_the_vertical(client, monkeypatch):
    _configure_mfa(monkeypatch)
    root, tv, mv, proof = client.portal.call(_office_root_with_current_proof)

    async def scenario():
        external, _ = await crear_usuario()
        await sql("UPDATE jax_users SET account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (external,))
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (external,))
        return external

    external = client.portal.call(scenario)
    project_id = client.post(
        "/api/virtual-office/projects",
        json=_project_payload(vertical="agencia"),
        headers=_proof_headers(root, tv, mv, proof, str(uuid.uuid4())),
    ).json()["id"]

    async def relate():
        await sql(
            "INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) VALUES (%s,%s,'COLLABORATOR','CLIENT_PUBLISHED','ACTIVE',%s)",
            (project_id, external, root),
        )
        # The create endpoint already provisions exactly one ACTIVE CLIENT
        # group per project (its idempotent contract); publication links the
        # external reader to that group instead of minting a second one.
        group = (await sql(
            "SELECT recipient_group_id FROM office_recipient_group WHERE project_id=%s AND group_kind='CLIENT' AND status='ACTIVE'",
            (project_id,), True,
        ))[0][0]
        await sql("INSERT INTO office_external_relation (project_id,user_id,recipient_group_id,status) VALUES (%s,%s,%s,'ACTIVE')", (project_id, external, group))

    client.portal.call(relate)
    rows = client.get(
        f"/api/virtual-office/projects?after_id={project_id - 1}&vertical=agencia",
        headers=_headers(external),
    ).json()["projects"]
    assert [row["id"] for row in rows] == [project_id]
    assert rows[0]["description"] is None
    assert rows[0]["vertical"] == "agencia"
