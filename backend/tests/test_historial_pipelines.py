"""Task 7 (2026-09-18, historial-y-arreglos-de-pipeline): GET /api/pipelines
para un historial, no un volcado de los últimos 50.

Le faltaban `duracion_s` y `costo_usd`, y le sobraba un tope fijo (LIMIT
LISTA_PIPELINES_MAX horneado en la consulta, sin forma de pedir la página
siguiente). Los tres se agregan sin tocar la regla de dueño (user_id Y
tenant_id, 404 al que no lo es) ni el orden (created_at DESC).

costo_usd -- HALLAZGO (verificado contra el esquema real de jax_memory_test,
2026-09-18, `SHOW CREATE TABLE axioma_usage`): la tabla NO tiene columna
trace_id ni pipeline_id. Ningún escritor la manda tampoco -- ni
jax/jacobs/usage_writer.py::record_direct_usage() ni
jax/las_manos/motor_registry/usage_writer.py::record_motor_usage() reciben
trace_id o pipeline_id; el `job_id` que sí guarda axioma_usage es el id de un
job de Motor Registry, un espacio de ids distinto del trace_id de un paso de
Jacobs. No hay registro que cruzar por trace_id: costo_usd sale `null` para
TODO pipeline hoy, no como estimación sino porque el cruce que pediría el
brief no existe en el esquema. Documentado también en pipelines.py junto al
campo. NO se implementó ningún cruce heurístico (por ventana de tiempo/
facet/modelo) a propósito: el brief pide explícitamente no recalcular ni
estimar (Principio VIII), y esa clase de cruce ya está probada ambigua en
jax-platform/backend/db/migrations.py:2151-2157 (medición puntual de
min_output_tokens, 2026-09-17: join por ventana de tiempo [started_at,
finished_at+5s] + facet + modelo, "filas ambiguas entre capabilities
excluidas").
"""
import time
import uuid

import pytest

from api import pipelines as mod
from tests.identidades import cabeceras, sql, uid


async def _insertar_pipeline(pipeline_id, user_id, tenant_id, status, creado, actualizado, owner_ack_at=None):
    await sql(
        "INSERT INTO jacobs_pipelines "
        "(pipeline_id, name, invoked_by, mode, status, created_at, updated_at, "
        " user_id, tenant_id, owner_ack_at) "
        "VALUES (%s, 'hist', 'plataforma', 'supervised', %s, %s, %s, %s, %s, %s)",
        (pipeline_id, status, creado, actualizado, user_id, tenant_id,
         owner_ack_at if owner_ack_at is not None else creado))


async def _insertar_uso(tenant_id, user_id, facet, model, creado_ts):
    await sql(
        "INSERT INTO axioma_usage "
        "(tenant_id, user_id, facet, model, tokens_in, tokens_out, cost_usd, request_type, created_at) "
        "VALUES (%s, %s, %s, %s, 100, 50, 0.012345, 'pipeline', FROM_UNIXTIME(%s))",
        (tenant_id, user_id, facet, model, creado_ts))


async def _borrar_pipelines(ids):
    for pid in ids:
        await sql("DELETE FROM jacobs_pipelines WHERE pipeline_id=%s", (pid,))


async def _borrar_uso(tenant_id):
    await sql("DELETE FROM axioma_usage WHERE tenant_id=%s", (tenant_id,))


TENANT = "hist-t7"


def test_el_listado_trae_duracion_costo_y_pagina(client):
    """Step 1 del brief, literal: el listado devuelve duracion_s, costo_usd
    y pagina."""
    duenio = uid(client, "hist-basico", "operator")
    ahora = time.time()
    pid = str(uuid.uuid4())
    client.portal.call(_insertar_pipeline, pid, duenio, TENANT, "completed", ahora - 12.5, ahora)
    try:
        resp = client.get("/api/pipelines", headers=cabeceras(client, "hist-basico", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert "has_more" in cuerpo
        fila = next(p for p in cuerpo["pipelines"] if p["pipeline_id"] == pid)
        assert fila["duracion_s"] == 12.5
        assert fila["costo_usd"] is None
    finally:
        client.portal.call(_borrar_pipelines, [pid])


@pytest.mark.parametrize("status", ["pending", "running", "interrupted", "aborted", "expired"])
def test_duracion_s_null_si_el_pipeline_puede_seguir_corriendo(client, status):
    """Ronda de arreglo 1 (2026-09-18): duracion_s es duración DEFINITIVA o
    nada. Van a null tanto los que todavía pueden avanzar solos
    (pending/running/interrupted -- interrupted es donde un pipeline
    supervised PAUSA entre olas esperando /resume, jax/jacobs/executor.py:1173,
    no un reposo) como los que pueden RETOMAR sobre la misma fila
    (aborted/expired: un /continue los reanuda). Mostrar una duración ahí
    tendría pinta de definitiva y dejaría de ser cierta en cuanto el
    pipeline avance o se continúe."""
    duenio = uid(client, f"hist-no-final-{status}", "operator")
    ahora = time.time()
    pid = str(uuid.uuid4())
    client.portal.call(_insertar_pipeline, pid, duenio, TENANT, status, ahora - 5, ahora - 1)
    try:
        resp = client.get("/api/pipelines",
                          headers=cabeceras(client, f"hist-no-final-{status}", "operator", tenant_id=TENANT))
        fila = next(p for p in resp.json()["pipelines"] if p["pipeline_id"] == pid)
        assert fila["duracion_s"] is None
    finally:
        client.portal.call(_borrar_pipelines, [pid])


def test_duracion_s_se_calcula_para_failed_igual_que_completed(client):
    """completed y failed son los ÚNICOS estados sin camino de vuelta a
    correr (ESTADOS_CONTINUABLES no los incluye): ahí sí hay una duración
    definitiva que mostrar."""
    duenio = uid(client, "hist-failed", "operator")
    ahora = time.time()
    pid = str(uuid.uuid4())
    client.portal.call(_insertar_pipeline, pid, duenio, TENANT, "failed", ahora - 7.25, ahora)
    try:
        resp = client.get("/api/pipelines", headers=cabeceras(client, "hist-failed", "operator", tenant_id=TENANT))
        fila = next(p for p in resp.json()["pipelines"] if p["pipeline_id"] == pid)
        assert fila["duracion_s"] == 7.25
    finally:
        client.portal.call(_borrar_pipelines, [pid])


def test_costo_usd_no_se_inventa_ni_con_un_registro_de_uso_parecido(client):
    """HALLAZGO (ver docstring del módulo): axioma_usage no tiene con qué
    cruzar por trace_id/pipeline_id. Esta prueba fija que, aunque exista una
    fila de axioma_usage con el mismo tenant/época que PARECE del pipeline,
    costo_usd sigue null -- no se arma un cruce heurístico por ventana de
    tiempo para disfrazarlo de dato verificado (Principio VIII)."""
    duenio = uid(client, "hist-costo-parecido", "operator")
    tenant_num = 91234
    ahora = time.time()
    pid = str(uuid.uuid4())
    client.portal.call(_insertar_pipeline, pid, duenio, str(tenant_num), "completed", ahora - 3, ahora)
    client.portal.call(_insertar_uso, tenant_num, int(duenio), "thot", "glm-4.6", ahora - 1)
    try:
        resp = client.get("/api/pipelines",
                          headers=cabeceras(client, "hist-costo-parecido", "operator", tenant_id=str(tenant_num)))
        fila = next(p for p in resp.json()["pipelines"] if p["pipeline_id"] == pid)
        assert fila["costo_usd"] is None
    finally:
        client.portal.call(_borrar_pipelines, [pid])
        client.portal.call(_borrar_uso, tenant_num)


def test_el_tope_fijo_ya_no_corta_el_historial__pagina_con_limite_y_offset(client):
    duenio = uid(client, "hist-pagina", "operator")
    ahora = time.time()
    ids = [str(uuid.uuid4()) for _ in range(3)]
    # ids[0] el más nuevo, ids[2] el más viejo.
    for i, pid in enumerate(ids):
        client.portal.call(_insertar_pipeline, pid, duenio, TENANT, "completed", ahora - i, ahora - i)
    try:
        cab = cabeceras(client, "hist-pagina", "operator", tenant_id=TENANT)
        r1 = client.get("/api/pipelines?limite=2&offset=0", headers=cab)
        assert r1.status_code == 200, r1.text
        c1 = r1.json()
        assert [p["pipeline_id"] for p in c1["pipelines"]] == [ids[0], ids[1]]
        assert c1["has_more"] is True

        r2 = client.get("/api/pipelines?limite=2&offset=2", headers=cab)
        c2 = r2.json()
        assert [p["pipeline_id"] for p in c2["pipelines"]] == [ids[2]]
        assert c2["has_more"] is False
    finally:
        client.portal.call(_borrar_pipelines, ids)


def test_limite_por_encima_del_tope_maximo_es_422(client):
    resp = client.get(f"/api/pipelines?limite={mod.LISTA_PIPELINES_MAX + 1}",
                      headers=cabeceras(client, "hist-limite-alto", "operator"))
    assert resp.status_code == 422, resp.text


def test_la_consulta_paginada_usa_el_indice_de_dueño_sin_filesort_ni_temporary(client):
    """LAS CUATRO (indexing): EXPLAIN sobre la consulta REAL, con el OFFSET
    nuevo -- no alcanza con que el índice EXISTA (una base de tests
    persistente puede tenerlo de una corrida vieja aunque la migración ya no
    lo cree); hay que ver que el plan lo USE."""
    filas = client.portal.call(
        sql, "EXPLAIN " + mod.SQL_PIPELINES_DEL_USUARIO,
        ("x", "TENANT-EXPLAIN-T7", mod.LISTA_PIPELINES_MAX, 5), True)
    ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
    assert tabla == "jacobs_pipelines"
    assert clave == "idx_jacobs_pipelines_duenio", filas
    assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas
