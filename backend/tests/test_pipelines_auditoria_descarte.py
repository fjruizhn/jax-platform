"""GET /api/pipelines/{id}/auditoria-descarte (2026-09-22, cierre de los dos
huecos que dejó abiertos la revisión final de la rama Descartar Pipelines).

Por qué: PIPELINE_DISCARDED/RECOVERED/HIDDEN/RESTORED se escriben en
jacobs_events en la MISMA transacción que el CAS (jax, jacobs/routes.py::
transicion_descarte -> store.pipeline_transicion_descarte), con payload
{"user_id", "desde", "a"} -- pero ninguna pantalla los muestra: hoy se leen
con `mysql` a mano. Esto expone esos cuatro tipos de evento, y sólo esos
cuatro (nunca el resto de jacobs_events -- STEP_FAILED, PIPELINE_ABORTED,
etc.), ordenados del más nuevo al más viejo.

Permisos: el dueño (_require_pipeline_owner, MISMA regla que /results y
GET/{id} -- un hidden sigue dando 404 a un dueño no-superadmin) O el
superadmin (mismo patrón de rama que recover_pipeline: _require_pipeline_exists,
sin exigir dueño).

Índice: `FORCE INDEX (idx_events_pipeline)` -- NO idx_events_pipeline_tipo.
Verificado con EXPLAIN (tests/probar_indice_eventos.py del reporte de esta
tarea, con ruido de 200 eventos STEP_FAILED del mismo pipeline): sin hint,
el optimizador prefiere idx_events_pipeline_tipo para el filtro
`event_type IN (...)`, pero el ORDER BY id DESC sobre CUATRO rangos
distintos (uno por valor del IN) no le queda ordenado gratis -- "Using
filesort". Con `idx_events_pipeline` (sólo pipeline_id), el rango es UNO
solo por pipeline_id y ya viene en orden de id (la clave primaria va
implícita al final de todo índice secundario de InnoDB): el motor lo
recorre al revés para el DESC sin ordenar nada aparte -- "Using index
condition; Using where", sin filesort. Es justo lo que pide el encargo, sin
ninguna contradicción como la del punto 1 (ver
tests/test_pipelines_descartados_admin.py)."""
import json
import time
import uuid
from functools import partial

from tests.identidades import cabeceras, sql, uid
from tests.test_pipelines_descarte import _insertar_pipeline, _borrar_pipelines

TENANT = "auditoria-descarte-t2"

TIPOS_DE_DESCARTE = ("PIPELINE_DISCARDED", "PIPELINE_RECOVERED", "PIPELINE_HIDDEN", "PIPELINE_RESTORED")


async def _insertar_evento(pipeline_id, event_type, payload, ts):
    await sql(
        "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
        "VALUES (%s, NULL, %s, %s, %s)",
        (pipeline_id, event_type, json.dumps(payload), ts),
    )


async def _borrar_eventos(pipeline_id):
    await sql("DELETE FROM jacobs_events WHERE pipeline_id=%s", (pipeline_id,))


def test_auditoria_de_un_pipeline_ajeno_es_404(client):
    duenio = uid(client, "auditoria-c1-duenio", "operator")
    uid(client, "auditoria-c1-otro", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "aborted"))
    try:
        resp = client.get(f"/api/pipelines/{pid}/auditoria-descarte",
                          headers=cabeceras(client, "auditoria-c1-otro", "operator", tenant_id=TENANT))
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "pipeline_no_encontrado"
    finally:
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_del_dueno_trae_solo_los_cuatro_tipos_ordenados_del_mas_nuevo(client):
    duenio = uid(client, "auditoria-c2-duenio", "operator")
    pid = str(uuid.uuid4())
    ahora = time.time()
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded",
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora))
    try:
        client.portal.call(_insertar_evento, pid, "STEP_FAILED", {"step_index": 0}, ahora - 10)
        client.portal.call(_insertar_evento, pid, "PIPELINE_DISCARDED",
                           {"user_id": duenio, "desde": "aborted", "a": "discarded"}, ahora - 5)
        client.portal.call(_insertar_evento, pid, "PIPELINE_RECOVERED",
                           {"user_id": duenio, "desde": "discarded", "a": "aborted"}, ahora - 3)
        client.portal.call(_insertar_evento, pid, "STEP_FAILED", {"step_index": 1}, ahora - 1)
        resp = client.get(f"/api/pipelines/{pid}/auditoria-descarte",
                          headers=cabeceras(client, "auditoria-c2-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        eventos = resp.json()["eventos"]
        assert [e["event_type"] for e in eventos] == ["PIPELINE_RECOVERED", "PIPELINE_DISCARDED"]
        assert eventos[0]["user_id"] == duenio
        assert eventos[0]["desde"] == "discarded"
        assert eventos[0]["a"] == "aborted"
        assert eventos[0]["ts"] == ahora - 3
    finally:
        client.portal.call(_borrar_eventos, pid)
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_de_un_pipeline_sin_eventos_de_descarte_es_lista_vacia(client):
    duenio = uid(client, "auditoria-c3-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "completed"))
    try:
        resp = client.get(f"/api/pipelines/{pid}/auditoria-descarte",
                          headers=cabeceras(client, "auditoria-c3-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        assert resp.json()["eventos"] == []
    finally:
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_de_un_hidden_para_su_dueno_no_superadmin_es_404(client):
    duenio = uid(client, "auditoria-c4-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "hidden",
                       status_previo="aborted", descartado_por=duenio, descartado_at=time.time()))
    try:
        resp = client.get(f"/api/pipelines/{pid}/auditoria-descarte",
                          headers=cabeceras(client, "auditoria-c4-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "pipeline_no_encontrado"
    finally:
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_del_superadmin_sobre_el_pipeline_de_otro_es_200(client, client_superadmin):
    duenio = uid(client, "auditoria-c5-duenio", "operator")
    pid = str(uuid.uuid4())
    ahora = time.time()
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "hidden",
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora))
    try:
        client.portal.call(_insertar_evento, pid, "PIPELINE_HIDDEN",
                           {"user_id": "superadmin-1", "desde": "discarded", "a": "hidden"}, ahora)
        resp = client_superadmin.get(f"/api/pipelines/{pid}/auditoria-descarte")
        assert resp.status_code == 200, resp.text
        eventos = resp.json()["eventos"]
        assert [e["event_type"] for e in eventos] == ["PIPELINE_HIDDEN"]
    finally:
        client.portal.call(_borrar_eventos, pid)
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_del_superadmin_de_un_pipeline_inexistente_es_404(client_superadmin):
    resp = client_superadmin.get(f"/api/pipelines/{uuid.uuid4()}/auditoria-descarte")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "pipeline_no_encontrado"


def test_auditoria_de_un_id_con_forma_invalida_es_400(client):
    resp = client.get("/api/pipelines/abc%3Fx=1/auditoria-descarte",
                      headers=cabeceras(client, "auditoria-c6", "operator"))
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "pipeline_id_invalido"


def test_explain_auditoria_descarte_usa_idx_events_pipeline_sin_filesort(client):
    from api.pipelines import SQL_AUDITORIA_DESCARTE

    pid = str(uuid.uuid4())
    ahora = time.time()
    try:
        # Ruido: 200 eventos de OTRO tipo del mismo pipeline -- sin esto, con
        # 0-1 filas el optimizador puede elegir cualquier índice y empatar
        # (mismo motivo que el resto de los EXPLAIN de esta tarea).
        for i in range(200):
            client.portal.call(_insertar_evento, pid, "STEP_FAILED", {"step_index": i}, ahora + i)
        for i, tipo in enumerate(TIPOS_DE_DESCARTE):
            client.portal.call(_insertar_evento, pid, tipo, {"user_id": "x", "desde": "a", "a": "b"}, ahora + 300 + i)
        client.portal.call(sql, "ANALYZE TABLE jacobs_events", (), True)

        args = (pid,) + TIPOS_DE_DESCARTE
        filas = client.portal.call(sql, "EXPLAIN " + SQL_AUDITORIA_DESCARTE, args, True)
        ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
        assert tabla == "jacobs_events"
        assert clave == "idx_events_pipeline", filas
        assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas
    finally:
        client.portal.call(_borrar_eventos, pid)
