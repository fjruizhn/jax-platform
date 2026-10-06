"""GET /api/admin/pipelines/descartados (2026-09-22, cierre de los dos huecos
que dejó abiertos la revisión final de la rama Descartar Pipelines).

Por qué: el superadmin puede ocultar y restaurar cualquier pipeline de su tenant, pero
sólo VE los descartados que él mismo descartó (GET /api/pipelines?estado=
discarded filtra por user_id/tenant_id del token). Si restaura el
descartado de otro, no puede volver a ocultarlo desde la UI porque nunca
aparece en ninguna lista que el superadmin pueda ver. Esta vista es el
equivalente, para 'discarded', de /api/admin/pipelines/ocultos (que ya
existe para 'hidden') -- MISMA forma de respuesta (has_more, paginación con
límite+1), MISMOS campos (pipeline_id, name, user_id, tenant_id,
descartado_por, descartado_at, created_at), sólo cambia el status del WHERE.

La consulta filtra por tenant y ordena dentro del tenant; usa
`idx_pipelines_tenant_status_date` (tenant_id, status, descartado_at,
pipeline_id), que sirve tanto a esta vista como a /ocultos. La prueba de
EXPLAIN confirma el índice en el plan real. La vista sigue incluyendo todos
los usuarios del tenant, pero no lee filas de otros tenants."""
import time
import uuid
from functools import partial

import pytest

from tests.identidades import cabeceras, sql, uid
from tests.test_pipelines_descarte import _insertar_pipeline, _borrar_pipelines

TENANT = "876003"


def _admin(client):
    return cabeceras(client, "descarte-admin-superadmin", "superadmin", tenant_id=TENANT)


def test_descartados_admin_de_un_no_superadmin_es_403(client):
    resp = client.get("/api/admin/pipelines/descartados",
                      headers=cabeceras(client, "descarte-admin-t1-operador", "operator"))
    assert resp.status_code == 403, resp.text


def _total_descartados(client) -> int:
    """Esta vista incluye todos los usuarios del tenant, así que se mide su
    conteo relativo antes de sembrar filas para aislar la paginación. El verde de antes era un accidente de
    tabla limpia: se mide el total REAL antes de sembrar y las
    aserciones de has_more/paginación se calculan relativas a ESE número,
    no a un 0 absoluto."""
    filas = client.portal.call(sql, "SELECT COUNT(*) FROM jacobs_pipelines WHERE tenant_id=%s AND status='discarded'",
                               (TENANT,), True)
    return filas[0][0]


def test_descartados_admin_trae_los_de_todos_los_usuarios(client, client_superadmin):
    duenio_a = uid(client, "descarte-admin-t1-duenio-a", "operator", TENANT)
    duenio_b = uid(client, "descarte-admin-t1-duenio-b", "operator", TENANT)
    ahora = time.time()
    pid_a = str(uuid.uuid4())
    pid_b = str(uuid.uuid4())
    pid_oculto = str(uuid.uuid4())  # hidden, no tiene que aparecer acá
    total_antes = _total_descartados(client)
    client.portal.call(partial(_insertar_pipeline, pid_a, duenio_a, TENANT, "discarded", ahora - 5, ahora - 5,
                       status_previo="aborted", descartado_por=duenio_a, descartado_at=ahora - 5))
    client.portal.call(partial(_insertar_pipeline, pid_b, duenio_b, TENANT, "discarded", ahora - 2, ahora - 2,
                       status_previo="expired", descartado_por=duenio_b, descartado_at=ahora - 1))
    client.portal.call(partial(_insertar_pipeline, pid_oculto, duenio_a, TENANT, "hidden", ahora, ahora,
                       status_previo="aborted", descartado_por=duenio_a, descartado_at=ahora))
    try:
        resp = client.get("/api/admin/pipelines/descartados", headers=_admin(client))
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert "has_more" in cuerpo, cuerpo
        assert "hay_mas" not in cuerpo
        # LIMITE_MAX de listar_descartados_admin es 50 -- calculado, no
        # supuesto en False.
        assert cuerpo["has_more"] == ((total_antes + 2) > 50), (total_antes, cuerpo["has_more"])
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
    """MAJOR-C (fix round 2, revisión adversarial de PR 151): la ronda 1
    ya había corregido el `has_more`/`len` hardcodeados de MAJOR-3, pero
    seguía asumiendo que `total_antes + 2` entra en el `limite` del
    endpoint -- `listar_descartados_admin` lo declara `Query(..., le=50)`
    (`LIMITE_MAX`). Con 49+ filas `discarded` YA en la tabla, ese `limite`
    supera 50, la validación de FastAPI responde 422, y el test moría en
    `assert resp.status_code == 200` con un mensaje que apuntaba a la
    validación del pedido, no al problema real -- el mismo supuesto de
    tabla-casi-limpia de MAJOR-3, con el umbral corrido de 50 a 49. Se
    tapa el `limite` en `LIMITE_MAX` y las aserciones se recalculan para
    los dos casos (sin tope, con tope)."""
    from api.admin.pipelines_ocultos import LIMITE_MAX

    duenio = uid(client, "descarte-admin-t1-pagina-duenio", "operator", TENANT)
    ahora = time.time()
    ids = [str(uuid.uuid4()) for _ in range(3)]
    total_antes = _total_descartados(client)
    for i, pid in enumerate(ids):
        client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded", ahora - 10 + i, ahora - 10 + i,
                           status_previo="aborted", descartado_por=duenio, descartado_at=ahora - 10 + i))
    try:
        # `limite` relativo al total REAL, pero SIN pasar el tope del
        # endpoint -- un `total_antes` grande (49+) ya no rompe la
        # petición con un 422 ajeno a lo que este test quiere probar.
        limite = min(total_antes + 2, LIMITE_MAX)
        total_real = total_antes + 3
        resp = client.get("/api/admin/pipelines/descartados", params={"limite": limite, "offset": 0},
                          headers=_admin(client))
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert len(cuerpo["pipelines"]) == limite
        assert cuerpo["has_more"] == (total_real > limite)
        # Página 2, desde offset=limite: lo que haya quedado afuera de la
        # primera, acotado igual por `limite` (si `total_antes` era
        # enorme, la página 2 también viene llena, no "sólo la que sobra").
        resp2 = client.get("/api/admin/pipelines/descartados", params={"limite": limite, "offset": limite},
                           headers=_admin(client))
        assert resp2.status_code == 200, resp2.text
        cuerpo2 = resp2.json()
        restantes = max(0, total_real - limite)
        assert len(cuerpo2["pipelines"]) == min(restantes, limite)
        assert cuerpo2["has_more"] == (restantes > limite)
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


def test_explain_descartados_admin_usa_idx_pipelines_tenant_status_date_sin_filesort(client):
    """Con datos con forma de producción (muchos usuarios/tenants distintos,
    no una tabla casi vacía -- mismo motivo que
    test_explain_descartados_del_usuario_usa_idx_pipelines_descartados en
    test_pipelines_descarte.py): sin esto, con 0-1 filas el optimizador
    puede desempatar por otro criterio que no se sostiene con datos reales."""
    from api.admin.pipelines_ocultos import SQL_DESCARTADOS_ADMIN

    ahora = time.time()
    filas = []
    for i in range(600):
        pid = str(uuid.uuid4())
        creado = ahora - (600 - i) * 2
        tenant_id = str(int(TENANT) if i % 30 == 0 else 880000 + i % 30)
        filas.append((pid, "discarded", creado, creado + 1, f"user-adm-{i % 100}",
                      tenant_id, creado, "aborted", f"user-adm-{i % 100}", creado + 1))
    try:
        client.portal.call(_insertar_pipelines_bulk_admin, filas)
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)

        filas_explain = client.portal.call(
            sql, "EXPLAIN " + SQL_DESCARTADOS_ADMIN, (TENANT, 51, 0), True)
        ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas_explain]
        assert tabla == "jacobs_pipelines"
        assert clave == "idx_pipelines_tenant_status_date", filas_explain
        assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas_explain
    finally:
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE user_id LIKE %s", ("user-adm-%",))
