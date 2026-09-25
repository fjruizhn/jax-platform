"""Task 7 (2026-09-18, historial-y-arreglos-de-pipeline): GET /api/pipelines
para un historial, no un volcado de los últimos 50.

Le faltaban `duracion_s` y `costo_usd`, y le sobraba un tope fijo (LIMIT
LISTA_PIPELINES_MAX horneado en la consulta, sin forma de pedir la página
siguiente). Los tres se agregan sin tocar la regla de dueño (user_id Y
tenant_id, 404 al que no lo es) ni el orden (created_at DESC).

costo_usd -- HALLAZGO original de Task 7 (verificado contra el esquema real
de jax_memory_test, 2026-09-18, `SHOW CREATE TABLE axioma_usage`): la tabla
NO tenía columna trace_id ni pipeline_id, y ningún escritor la mandaba --
costo_usd salía `null` para TODO pipeline, documentado abajo en
`test_costo_usd_no_se_inventa_ni_con_un_registro_de_uso_parecido`. NO se
implementó ningún cruce heurístico (por ventana de tiempo/facet/modelo) a
propósito: esa clase de cruce ya está probada ambigua en
jax-platform/backend/db/migrations.py:2151-2157 (medición puntual de
min_output_tokens, 2026-09-17, "filas ambiguas entre capabilities
excluidas") -- Principio VIII.

Task 7b (2026-09-18, MISMA ronda, cruza jax + jax-platform): agrega
`axioma_usage.pipeline_id` (migrations.py) y hace que los DOS escritores de
`jax` lo llenen (jacobs/usage_writer.py::record_direct_usage,
las_manos/motor_registry/usage_writer.py::record_motor_usage) -- pasa a
existir el cruce EXACTO que Task 7 no tenía. `costo_usd` ahora suma
`axioma_usage.cost_usd` por `pipeline_id` para los pipelines que SÍ tienen
uso referenciado. Los pipelines de ANTES de esta migración (o cualquier fila
de uso que un escritor viejo, sin esta ronda, haya dejado sin la columna)
siguen `null` -- honesto: ese dato no existe y no se fabrica por ventana de
tiempo ni por ningún otro heurístico. La prueba de que NO se arma un cruce
heurístico por casualidad (mismo tenant/época, sin `pipeline_id`) se
conserva tal cual -- ver `test_costo_usd_no_se_inventa_ni_con_un_registro_de_uso_parecido`.
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


async def _insertar_uso_de_pipeline(pipeline_id, tenant_id, user_id, facet, model, cost, creado_ts):
    """Como `_insertar_uso`, pero con `pipeline_id` -- lo que los escritores
    de la Task 7b mandan de verdad. `cost` puede ser None (precio no
    resuelto, mismo caso que un escritor sin base de precios)."""
    await sql(
        "INSERT INTO axioma_usage "
        "(tenant_id, user_id, facet, model, tokens_in, tokens_out, cost_usd, request_type, created_at, pipeline_id) "
        "VALUES (%s, %s, %s, %s, 100, 50, %s, 'pipeline', FROM_UNIXTIME(%s), %s)",
        (tenant_id, user_id, facet, model, cost, creado_ts, pipeline_id))


async def _borrar_pipelines(ids):
    for pid in ids:
        await sql("DELETE FROM jacobs_pipelines WHERE pipeline_id=%s", (pid,))


async def _borrar_uso(tenant_id):
    await sql("DELETE FROM axioma_usage WHERE tenant_id=%s", (tenant_id,))


TENANT = "701"


def test_el_listado_trae_duracion_costo_y_pagina(client):
    """Step 1 del brief, literal: el listado devuelve duracion_s, costo_usd
    y pagina."""
    duenio = uid(client, "hist-basico", "operator", TENANT)
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
    duenio = uid(client, f"hist-no-final-{status}", "operator", TENANT)
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
    duenio = uid(client, "hist-failed", "operator", TENANT)
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
    duenio = uid(client, "hist-costo-parecido", "operator", "91234")
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


def test_costo_usd_suma_el_uso_real_del_pipeline_por_pipeline_id(client):
    """Task 7b: con `pipeline_id` en axioma_usage y los escritores de jax
    llenándolo, costo_usd deja de ser null siempre -- suma SOLO las filas de
    ESE pipeline, no las de un pipeline vecino con el mismo tenant."""
    duenio = uid(client, "hist-costo-real", "operator", TENANT)
    tenant_num = int(duenio)  # axioma_usage.tenant_id es INT; no participa del
                              # cruce (que es por pipeline_id), así que alcanza
                              # con un número propio de este test para poder
                              # limpiarlo después.
    ahora = time.time()
    pid = str(uuid.uuid4())
    otro_pid = str(uuid.uuid4())
    client.portal.call(_insertar_pipeline, pid, duenio, TENANT, "completed", ahora - 5, ahora)
    client.portal.call(_insertar_pipeline, otro_pid, duenio, TENANT, "completed", ahora - 5, ahora)
    client.portal.call(_insertar_uso_de_pipeline, pid, tenant_num, int(duenio), "jekyll", "m1", 0.01, ahora - 4)
    client.portal.call(_insertar_uso_de_pipeline, pid, tenant_num, int(duenio), "jekyll", "m1", 0.02, ahora - 3)
    client.portal.call(_insertar_uso_de_pipeline, otro_pid, tenant_num, int(duenio), "jekyll", "m1", 9.0, ahora - 3)
    try:
        resp = client.get("/api/pipelines", headers=cabeceras(client, "hist-costo-real", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        fila = next(p for p in resp.json()["pipelines"] if p["pipeline_id"] == pid)
        assert fila["costo_usd"] == pytest.approx(0.03)
    finally:
        client.portal.call(_borrar_pipelines, [pid, otro_pid])
        client.portal.call(_borrar_uso, tenant_num)


def test_costo_usd_suma_lo_que_conoce_cuando_una_fila_no_tiene_precio(client):
    """cost_usd NULL en una fila individual (precio no resuelto, mismo caso
    que documenta record_direct_usage/record_motor_usage) no tira el total a
    null -- SUM() lo ignora y suma lo que SÍ se sabe. Es un total parcial
    real, no un total inventado."""
    duenio = uid(client, "hist-costo-parcial", "operator", TENANT)
    tenant_num = int(duenio)
    ahora = time.time()
    pid = str(uuid.uuid4())
    client.portal.call(_insertar_pipeline, pid, duenio, TENANT, "completed", ahora - 5, ahora)
    client.portal.call(_insertar_uso_de_pipeline, pid, tenant_num, int(duenio), "jekyll", "m1", 0.05, ahora - 4)
    client.portal.call(_insertar_uso_de_pipeline, pid, tenant_num, int(duenio), "jekyll", "m1", None, ahora - 3)
    try:
        resp = client.get("/api/pipelines", headers=cabeceras(client, "hist-costo-parcial", "operator", tenant_id=TENANT))
        fila = next(p for p in resp.json()["pipelines"] if p["pipeline_id"] == pid)
        assert fila["costo_usd"] == pytest.approx(0.05)
    finally:
        client.portal.call(_borrar_pipelines, [pid])
        client.portal.call(_borrar_uso, tenant_num)


def test_la_consulta_de_costo_usa_el_indice_de_pipeline_sin_filesort_ni_temporary(client):
    """LAS CUATRO (indexing): mismo criterio que la consulta de dueño -- el
    plan REAL, no que el índice exista."""
    filas = client.portal.call(
        sql, "EXPLAIN " + mod.sql_costo_por_pipeline(3),
        ("pid-explain-a", "pid-explain-b", "pid-explain-c"), True)
    ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
    assert tabla == "axioma_usage"
    assert clave == "idx_axioma_usage_pipeline", filas
    assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas


def test_el_tope_fijo_ya_no_corta_el_historial__pagina_con_limite_y_offset(client):
    duenio = uid(client, "hist-pagina", "operator", TENANT)
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
    lo cree); hay que ver que el plan lo USE.

    `idx_pipelines_visibles`, no `idx_jacobs_pipelines_duenio` (Task 4,
    descartar-pipelines, fix round 4, Ruling 18/19, 2026-09-22):
    SQL_PIPELINES_DEL_USUARIO cambió de índice para que el costo quede
    acotado por el LIMIT en vez de por el histórico del tenant -- ver
    api/pipelines.py y docs/carga-sql-pipelines-del-usuario-indice-2026-09-22.md.
    Esta prueba (Task 7, 2026-09-18) sigue siendo la misma propiedad
    (LAS CUATRO/indexing sobre la consulta paginada real); sólo cambió CUÁL
    es el índice correcto."""
    filas = client.portal.call(
        sql, "EXPLAIN " + mod.SQL_PIPELINES_DEL_USUARIO,
        ("x", "TENANT-EXPLAIN-T7", mod.LISTA_PIPELINES_MAX, 5), True)
    ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
    assert tabla == "jacobs_pipelines"
    assert clave == "idx_pipelines_visibles", filas
    assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas
