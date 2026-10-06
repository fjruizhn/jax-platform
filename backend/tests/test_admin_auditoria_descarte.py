"""GET /api/admin/auditoria-descarte: feed de eventos del descarte."""
from __future__ import annotations

import json
import time
import uuid
from functools import partial

from tests.identidades import _tenant_db_id, cabeceras, sql, uid
from tests.test_pipelines_descarte import _borrar_pipelines, _insertar_pipeline


TENANT = "auditoria-global-t1"
TENANT_DB = str(_tenant_db_id(TENANT))
OTRO_TENANT = "auditoria-global-t2"
OTRO_TENANT_DB = str(_tenant_db_id(OTRO_TENANT))
EVENTOS = (
    "PIPELINE_DISCARDED", "PIPELINE_RECOVERED", "PIPELINE_HIDDEN", "PIPELINE_RESTORED",
)


async def _insertar_evento(pipeline_id, tipo, actor, ts, motivo=None):
    payload = {"user_id": actor, "desde": "aborted", "a": "discarded"}
    if motivo is not None:
        payload["motivo"] = motivo
    await sql(
        "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
        "VALUES (%s, NULL, %s, %s, %s)",
        (pipeline_id, tipo, json.dumps(payload), ts),
    )


async def _borrar_eventos(ids):
    for pipeline_id in ids:
        await sql("DELETE FROM jacobs_events WHERE pipeline_id=%s", (pipeline_id,))


async def _insertar_eventos_bulk(pipeline_id, cantidad):
    from db.connection import get_pool
    pool = await get_pool()
    ahora = time.time()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
                "VALUES (%s, NULL, %s, %s, %s)",
                [(pipeline_id, EVENTOS[i % len(EVENTOS)], '{"user_id":"load","motivo":"test"}', ahora - i)
                 for i in range(cantidad)],
            )


def _admin_headers(client, label="auditoria-global-admin", tenant=TENANT):
    return cabeceras(client, label, "superadmin", tenant_id=tenant)


def test_cursor_auditoria_rechaza_payloads_malformados_sin_base_de_datos():
    from fastapi import HTTPException
    from api.admin.auditoria_descarte import _decodificar_cursor

    assert _decodificar_cursor(None) is None
    assert _decodificar_cursor("WzEuMCwxXQ") == (1.0, 1)
    for invalid in ("!", "bnVsbA", "W3RydWUsMV0", "WzEuMCwwXQ", "WzEuMCwiMSJd"):
        try:
            _decodificar_cursor(invalid)
        except HTTPException as exc:
            assert exc.status_code == 422
        else:
            raise AssertionError(f"cursor inválido aceptado: {invalid!r}")


def test_auditoria_descarte_exige_superadmin_y_no_expone_escrituras(client):
    url = "/api/admin/auditoria-descarte"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=cabeceras(client, "auditoria-global-operador", "operator",
                                             tenant_id=TENANT)).status_code == 403
    assert client.post(url, headers=_admin_headers(client)).status_code == 405


def test_auditoria_descarte_pagina_filtra_tenant_y_devuelve_actor_motivo_y_pipeline(client):
    owner_a = uid(client, "auditoria-global-owner-a", "operator", tenant_id=TENANT)
    owner_b = uid(client, "auditoria-global-owner-b", "operator", tenant_id=OTRO_TENANT)
    pipeline_a, pipeline_b = str(uuid.uuid4()), str(uuid.uuid4())
    ahora = time.time()
    client.portal.call(partial(_insertar_pipeline, pipeline_a, owner_a, TENANT_DB, "discarded"))
    client.portal.call(partial(_insertar_pipeline, pipeline_b, owner_b, OTRO_TENANT_DB, "discarded"))
    for i, tipo in enumerate(EVENTOS):
        client.portal.call(_insertar_evento, pipeline_a, tipo, f"actor-{i}", ahora - 40 + i * 10,
                           "motivo de prueba" if i == 2 else None)
    client.portal.call(_insertar_evento, pipeline_b, "PIPELINE_DISCARDED", "actor-ajeno", ahora)
    try:
        url = "/api/admin/auditoria-descarte"
        primera = client.get(url, headers=_admin_headers(client), params={"limite": 2})
        assert primera.status_code == 200, primera.text
        data = primera.json()
        assert [e["event_type"] for e in data["eventos"]] == list(reversed(EVENTOS[-2:]))
        assert all(e["pipeline_id"] == pipeline_a for e in data["eventos"])
        assert data["eventos"][0]["pipeline_name"] == "desc"
        assert data["eventos"][0]["actor"] == "actor-3"
        assert data["has_more"] is True
        cursor = data["cursor_siguiente"]
        assert isinstance(cursor, str) and cursor

        segunda = client.get(url, headers=_admin_headers(client), params={"limite": 2, "cursor": cursor})
        assert segunda.status_code == 200, segunda.text
        pagina_dos = segunda.json()
        assert [e["event_type"] for e in pagina_dos["eventos"]] == list(reversed(EVENTOS[:2]))
        assert pagina_dos["has_more"] is False
        motivo = client.get(url, headers=_admin_headers(client), params={
            "evento": "PIPELINE_HIDDEN", "pipeline_id": pipeline_a,
        })
        assert motivo.status_code == 200, motivo.text
        [evento] = motivo.json()["eventos"]
        assert evento["motivo"] == "motivo de prueba"

        otro_admin = client.get(url, headers=_admin_headers(client, "auditoria-global-admin-b", OTRO_TENANT))
        assert otro_admin.status_code == 200, otro_admin.text
        assert [e["pipeline_id"] for e in otro_admin.json()["eventos"]] == [pipeline_b]
    finally:
        ids = [pipeline_a, pipeline_b]
        client.portal.call(_borrar_eventos, ids)
        client.portal.call(_borrar_pipelines, ids)


def test_auditoria_descarte_valida_tipo_rango_de_fechas_y_tope(client):
    url = "/api/admin/auditoria-descarte"
    headers = _admin_headers(client)
    assert client.get(url, headers=headers, params={"evento": "STEP_FAILED"}).status_code == 422
    assert client.get(url, headers=headers, params={"desde": "2026-10-06", "hasta": "2026-10-01"}).status_code == 422
    assert client.get(url, headers=headers, params={"limite": 51}).status_code == 422


def test_explain_consultas_reales_auditoria_usan_indices_sin_filesort(client):
    from api.admin.auditoria_descarte import SQL_GLOBAL, SQL_POR_PIPELINE

    owner = uid(client, "auditoria-explain-owner", "operator", tenant_id=TENANT)
    pipeline_id = str(uuid.uuid4())
    ahora = time.time()
    client.portal.call(partial(_insertar_pipeline, pipeline_id, owner, TENANT_DB, "discarded"))
    try:
        client.portal.call(_insertar_eventos_bulk, pipeline_id, 1200)
        client.portal.call(sql, "ANALYZE TABLE jacobs_events", (), True)
        planes = []
        variantes = (
            (SQL_GLOBAL, ("PIPELINE_DISCARDED", TENANT_DB, ahora - 2000, ahora + 10),
             "idx_events_auditoria_fecha"),
            (SQL_POR_PIPELINE, (pipeline_id, "PIPELINE_DISCARDED", TENANT_DB, ahora - 2000, ahora + 10),
             "idx_events_pipeline_auditoria_fecha"),
        )
        consultas = []
        for base, args, indice in variantes:
            consultas.append((base + "ORDER BY e.ts DESC, e.id DESC LIMIT %s", (*args, 51), indice))
            consultas.append((base + "AND (e.ts < %s OR (e.ts = %s AND e.id < %s)) "
                              "ORDER BY e.ts DESC, e.id DESC LIMIT %s",
                              (*args, ahora - 100, ahora - 100, 1000, 51), indice))
        for consulta, parametros, indice in consultas:
            filas = client.portal.call(sql, "EXPLAIN " + consulta, parametros, True)
            plan = [tuple(f) for f in filas]
            evento = next(f for f in plan if f[2] == "e")
            assert evento[5] == indice, plan
            extra = " ".join(str(f[9] or "") for f in plan).lower()
            assert "filesort" not in extra and "temporary" not in extra, plan
            planes.append(plan)
        assert len(planes) == 4
    finally:
        client.portal.call(_borrar_eventos, [pipeline_id])
        client.portal.call(_borrar_pipelines, [pipeline_id])
