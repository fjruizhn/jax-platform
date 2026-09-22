"""GET /api/admin/pipelines/descartados (2026-09-22, cierre de los dos huecos
que dejó abiertos la revisión final de la rama Descartar Pipelines).

Por qué: el superadmin puede ocultar y restaurar cualquier pipeline, pero
sólo VE los descartados que él mismo descartó (GET /api/pipelines?estado=
discarded filtra por user_id/tenant_id del token). Si restaura el
descartado de otro, no puede volver a ocultarlo desde la UI porque nunca
aparece en ninguna lista que el superadmin pueda ver. Esta vista es el
equivalente, para 'discarded', de /api/admin/pipelines/ocultos (que ya
existe para 'hidden') -- MISMA forma de respuesta (has_more, paginación con
límite+1), MISMOS campos (pipeline_id, name, user_id, tenant_id,
descartado_por, descartado_at, created_at), sólo cambia el status del WHERE.

Nota de índice (verificada, no supuesta -- Principio I): el encargo pedía
que esta consulta fuera por `idx_pipelines_descartados` (user_id, tenant_id,
status, descartado_at). Esa clave NO sirve acá -- esta vista, a diferencia
de la de descartados DEL DUEÑO (SQL_DESCARTADOS_DEL_USUARIO en
api/pipelines.py), no filtra por user_id/tenant_id (trae los de TODOS los
usuarios), así que ese índice no puede darle el ORDER BY descartado_at sin
filesort: cualquier prefijo user_id/tenant_id distinto rompe el orden
global. Medido con EXPLAIN contra datos con forma de producción (3000 filas
`discarded` de 500 usuarios/50 tenants distintos, tests/probar_indice.py
del reporte de esta tarea): sin hint, el optimizador elige
`idx_pipelines_ocultos` (status, descartado_at) -- type=range, Extra=
"Using index condition", SIN filesort; forzando `idx_pipelines_descartados`
da type=ALL + "Using filesort" (peor de las dos formas posibles). El código
de `jax` (jacobs/store.py, comentario junto a `_INDICES`) ya documenta esto:
"la de ocultos (todos los usuarios) por status + descartado_at" -- ese
índice ya es genérico por status, no exclusivo de 'hidden'; es el MISMO que
usa el hermano /admin/pipelines/ocultos, con otro valor de status. Se
implementa con `idx_pipelines_ocultos`, que es lo que el propio encargo
pide en la frase de al lado ("no Using filesort", "EXACTAMENTE el patrón
de pipelines_ocultos.py") -- no se crea ningún índice nuevo ni se toca el
repo `jax`. Reportado a Fernando como corrección, no como decisión de
diseño abierta (ver el reporte de la tarea)."""
import time
import uuid
from functools import partial

import pytest

from tests.identidades import cabeceras, sql, uid
from tests.test_pipelines_descarte import _insertar_pipeline, _borrar_pipelines

TENANT = "descarte-admin-t1"


def test_descartados_admin_de_un_no_superadmin_es_403(client):
    resp = client.get("/api/admin/pipelines/descartados",
                      headers=cabeceras(client, "descarte-admin-t1-operador", "operator"))
    assert resp.status_code == 403, resp.text


def test_descartados_admin_trae_los_de_todos_los_usuarios(client, client_superadmin):
    duenio_a = uid(client, "descarte-admin-t1-duenio-a", "operator")
    duenio_b = uid(client, "descarte-admin-t1-duenio-b", "operator")
    ahora = time.time()
    pid_a = str(uuid.uuid4())
    pid_b = str(uuid.uuid4())
    pid_oculto = str(uuid.uuid4())  # hidden, no tiene que aparecer acá
    client.portal.call(partial(_insertar_pipeline, pid_a, duenio_a, TENANT, "discarded", ahora - 5, ahora - 5,
                       status_previo="aborted", descartado_por=duenio_a, descartado_at=ahora - 5))
    client.portal.call(partial(_insertar_pipeline, pid_b, duenio_b, TENANT, "discarded", ahora - 2, ahora - 2,
                       status_previo="expired", descartado_por=duenio_b, descartado_at=ahora - 1))
    client.portal.call(partial(_insertar_pipeline, pid_oculto, duenio_a, TENANT, "hidden", ahora, ahora,
                       status_previo="aborted", descartado_por=duenio_a, descartado_at=ahora))
    try:
        resp = client_superadmin.get("/api/admin/pipelines/descartados")
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert "has_more" in cuerpo, cuerpo
        assert cuerpo["has_more"] is False
        assert "hay_mas" not in cuerpo
        filas = {f["pipeline_id"]: f for f in cuerpo["pipelines"] if f["pipeline_id"] in (pid_a, pid_b, pid_oculto)}
        assert set(filas) == {pid_a, pid_b}
        assert filas[pid_a]["user_id"] == duenio_a
        assert filas[pid_a]["descartado_por"] == duenio_a
        assert filas[pid_b]["user_id"] == duenio_b
        # descartado_at DESC: pid_b (más nuevo) antes que pid_a.
        orden = [f["pipeline_id"] for f in cuerpo["pipelines"] if f["pipeline_id"] in (pid_a, pid_b)]
        assert orden == [pid_b, pid_a]
    finally:
        client.portal.call(_borrar_pipelines, [pid_a, pid_b, pid_oculto])


def test_descartados_admin_pagina_con_limite_y_offset(client, client_superadmin):
    duenio = uid(client, "descarte-admin-t1-pagina-duenio", "operator")
    ahora = time.time()
    ids = [str(uuid.uuid4()) for _ in range(3)]
    for i, pid in enumerate(ids):
        client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded", ahora - 10 + i, ahora - 10 + i,
                           status_previo="aborted", descartado_por=duenio, descartado_at=ahora - 10 + i))
    try:
        resp = client_superadmin.get("/api/admin/pipelines/descartados", params={"limite": 2, "offset": 0})
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert cuerpo["has_more"] is True
        assert len(cuerpo["pipelines"]) == 2
        # Página 2: sólo la fila que quedó afuera de la primera.
        resp2 = client_superadmin.get("/api/admin/pipelines/descartados", params={"limite": 2, "offset": 2})
        cuerpo2 = resp2.json()
        assert cuerpo2["has_more"] is False
        assert len(cuerpo2["pipelines"]) == 1
    finally:
        client.portal.call(_borrar_pipelines, ids)


async def _insertar_pipelines_bulk_admin(filas):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO jacobs_pipelines "
                "(pipeline_id, name, invoked_by, mode, status, created_at, updated_at, "
                " user_id, tenant_id, owner_ack_at, status_previo, descartado_por, descartado_at) "
                "VALUES (%s, 'desc', 'plataforma', 'supervised', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                filas)


def test_explain_descartados_admin_usa_idx_pipelines_ocultos_sin_filesort(client):
    """Con datos con forma de producción (muchos usuarios/tenants distintos,
    no una tabla casi vacía -- mismo motivo que
    test_explain_descartados_del_usuario_usa_idx_pipelines_descartados en
    test_pipelines_descarte.py): sin esto, con 0-1 filas el optimizador
    puede desempatar por otro criterio que no se sostiene con datos reales."""
    from api.admin.pipelines_ocultos import SQL_DESCARTADOS_ADMIN

    tenant_real = "TENANT-EXPLAIN-ADMIN-T1"
    ahora = time.time()
    filas = []
    for i in range(600):
        pid = str(uuid.uuid4())
        creado = ahora - (600 - i) * 2
        filas.append((pid, "discarded", creado, creado + 1, f"user-adm-{i % 100}",
                      f"tenant-adm-{i % 30}", creado, "aborted", f"user-adm-{i % 100}", creado + 1))
    try:
        client.portal.call(_insertar_pipelines_bulk_admin, filas)
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)

        filas_explain = client.portal.call(
            sql, "EXPLAIN " + SQL_DESCARTADOS_ADMIN, (50, 0), True)
        ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas_explain]
        assert tabla == "jacobs_pipelines"
        assert clave == "idx_pipelines_ocultos", filas_explain
        assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas_explain
    finally:
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE user_id LIKE %s", ("user-adm-%",))
