"""Task 4 (2026-09-22, descartar-pipelines): proxies discard/recover/hide/
restore con permisos, y las listas sin los descartados/ocultos.

Brief: .superpowers/sdd/2026-09-22-descartar-pipelines/task-4-brief.md
Spec: docs/superpowers/specs/2026-09-22-descartar-pipelines-design.md §4.

Contrato de Jacobs (repo jax, MASTER 0a37a54, jacobs/routes.py): 4 rutas
LITERALES POST /pipeline/{id}/{discard,recover,hide,restore}, body
{"user_id": <str, min_length 1>} + encabezados_las_manos(). 200 ->
{"pipeline_id", "status"}. 404 -> {"code": "pipeline_no_encontrado"}.
409 -> {"code": "transicion_no_permitida", "status": <str>} o
{"code": "cambio_concurrente"}. 422 -> {"code": "estado_previo_invalido"}.

Columnas nuevas en jacobs_pipelines: status_previo, descartado_por,
descartado_at (ver jax/jacobs/store.py). Índices: idx_pipelines_descartados
(user_id, tenant_id, status, descartado_at), idx_pipelines_ocultos (status,
descartado_at).
"""
import time
import uuid
from functools import partial

import pytest

from api import pipelines as mod
from tests.identidades import cabeceras, sql, uid
from tests.jacobs_falso import JacobsFalso, respuesta

TENANT = "descarte-t4"
URL_JACOBS_FALSO = "http://jacobs.test/jacobs"

# La base de tests LOCAL (jax_memory_test_<sufijo>, clonada de jax_memory_test
# -- ver base_de_test.py) no tiene status_previo/descartado_por/descartado_at
# ni idx_pipelines_descartados/idx_pipelines_ocultos: esas columnas/índices
# los trae `jax` (jacobs/store.py::init_tables(), repo aparte). CI los tiene
# porque .github/workflows/policy.yml clona jax MASTER y corre su propio
# init_tables() ANTES de la suite; localmente no hay ese paso. El remedio
# -- session fixture que corre jax's init_tables() vía JAX_REPO_PATH -- vive
# ahora en tests/conftest.py::_esquema_de_jax_en_la_base_de_test (fix round
# 2, Ruling 16 punto 3): se movió de acá porque desde que
# SQL_PIPELINES_DEL_USUARIO lleva IGNORE INDEX (Ruling 16 punto 1) el
# problema dejó de ser sólo de este módulo -- CUALQUIER test que use esa
# consulta revienta con "Key ... doesn't exist" (1176) si corre antes.


async def _insertar_pipeline(pipeline_id, user_id, tenant_id, status, creado=None, actualizado=None,
                              owner_ack_at=None, status_previo=None, descartado_por=None,
                              descartado_at=None):
    ahora = time.time()
    creado = ahora if creado is None else creado
    actualizado = ahora if actualizado is None else actualizado
    await sql(
        "INSERT INTO jacobs_pipelines "
        "(pipeline_id, name, invoked_by, mode, status, created_at, updated_at, "
        " user_id, tenant_id, owner_ack_at, status_previo, descartado_por, descartado_at) "
        "VALUES (%s, 'desc', 'plataforma', 'supervised', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (pipeline_id, status, creado, actualizado, user_id, tenant_id,
         owner_ack_at if owner_ack_at is not None else creado,
         status_previo, descartado_por, descartado_at))


async def _borrar_pipelines(ids):
    for pid in ids:
        await sql("DELETE FROM jacobs_pipelines WHERE pipeline_id=%s", (pid,))


async def _borrar_uso_de_pipeline(pipeline_id):
    await sql("DELETE FROM axioma_usage WHERE pipeline_id=%s", (pipeline_id,))


def _instalar_jacobs_falso(monkeypatch, rutas):
    """Instala un JacobsFalso SIN tocar _require_pipeline_owner (a
    diferencia de tests.jacobs_falso.preparar): estos tests ejercitan la
    guardia real contra la base de test."""
    falso = JacobsFalso(rutas)

    async def cliente():
        return falso

    monkeypatch.setattr(mod, "get_http_client", cliente)
    monkeypatch.setattr(mod, "JACOBS_URL", URL_JACOBS_FALSO)
    return falso


# ---------------------------------------------------------------------------
# Caso 1: POST /discard de un pipeline ajeno -> 404, sin llamar a Jacobs.
# ---------------------------------------------------------------------------
def test_discard_de_un_pipeline_ajeno_es_404_y_no_llama_a_jacobs(client, monkeypatch):
    duenio = uid(client, "descarte-c1-duenio", "operator")
    uid(client, "descarte-c1-otro", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "aborted"))
    falso = _instalar_jacobs_falso(monkeypatch, {})
    try:
        resp = client.post(f"/api/pipelines/{pid}/discard",
                           headers=cabeceras(client, "descarte-c1-otro", "operator", tenant_id=TENANT))
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "pipeline_no_encontrado"
        assert falso.llamadas == []
    finally:
        client.portal.call(_borrar_pipelines, [pid])


# ---------------------------------------------------------------------------
# Caso 2: POST /discard del dueño -> llama a Jacobs con user_id y la
# cabecera de encabezados_las_manos().
# ---------------------------------------------------------------------------
def test_discard_del_dueno_llama_a_jacobs_con_user_id_y_credencial(client, monkeypatch):
    duenio = uid(client, "descarte-c2-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "aborted"))
    falso = _instalar_jacobs_falso(monkeypatch, {
        ("POST", f"/pipeline/{pid}/discard"): respuesta(200, {"pipeline_id": pid, "status": "discarded"}),
    })
    try:
        resp = client.post(f"/api/pipelines/{pid}/discard",
                           headers=cabeceras(client, "descarte-c2-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"pipeline_id": pid, "status": "discarded"}
        [(metodo, ruta, cuerpo)] = falso.llamadas
        assert (metodo, ruta) == ("POST", f"/pipeline/{pid}/discard")
        assert cuerpo == {"user_id": duenio}
    finally:
        client.portal.call(_borrar_pipelines, [pid])


# ---------------------------------------------------------------------------
# Caso 3: POST /recover de un usuario distinto de descartado_por, que no es
# superadmin -> 403 recuperar_no_permitido. Se siembra directo (el guard
# tiene que sostenerse pase lo que pase, no sólo en el camino discard ->
# recover): el pipeline es DEL usuario que llama (pasa _require_pipeline_owner)
# pero descartado_por es otro id -- el guard mira descartado_por, no dueño.
# ---------------------------------------------------------------------------
def test_recover_de_quien_no_descarto_y_no_es_superadmin_es_403(client, monkeypatch):
    duenio = uid(client, "descarte-c3-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded",
                       status_previo="aborted", descartado_por="algun-otro-id", descartado_at=time.time()))
    falso = _instalar_jacobs_falso(monkeypatch, {})
    try:
        resp = client.post(f"/api/pipelines/{pid}/recover",
                           headers=cabeceras(client, "descarte-c3-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == "recuperar_no_permitido"
        assert falso.llamadas == []
    finally:
        client.portal.call(_borrar_pipelines, [pid])


# ---------------------------------------------------------------------------
# Fix round 1 (2026-09-22, hallazgo del revisor): faltaba el camino FELIZ de
# recover para un usuario normal -- el dueño que descartó SU PROPIO pipeline
# lo recupera. Tiene que caer si se fuerza la condición del guard (probado
# más abajo con las mutaciones de Ruling 13(a): NULL/no-discarded no dan
# 403, y "otro te lo ganó" sigue dando 403 -- caso 3, arriba).
# ---------------------------------------------------------------------------
def test_recover_del_dueno_que_descarto_su_propio_pipeline_es_200(client, monkeypatch):
    duenio = uid(client, "descarte-c3b-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded",
                       status_previo="aborted", descartado_por=duenio, descartado_at=time.time()))
    falso = _instalar_jacobs_falso(monkeypatch, {
        ("POST", f"/pipeline/{pid}/recover"): respuesta(200, {"pipeline_id": pid, "status": "aborted"}),
    })
    try:
        resp = client.post(f"/api/pipelines/{pid}/recover",
                           headers=cabeceras(client, "descarte-c3b-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"pipeline_id": pid, "status": "aborted"}
        [(metodo, ruta, cuerpo)] = falso.llamadas
        assert (metodo, ruta) == ("POST", f"/pipeline/{pid}/recover")
        assert cuerpo == {"user_id": duenio}
    finally:
        client.portal.call(_borrar_pipelines, [pid])


# ---------------------------------------------------------------------------
# Fix round 1, Ruling 13(a): descartado_por NULL (nunca se descartó, o el
# store ya lo limpió) no da 403 -- se deja pasar a Jacobs, que decide con su
# propio CAS. Acá Jacobs devuelve 409 transicion_no_permitida: la prueba de
# que el 403 NO lo frenó antes es justamente que el pedido LLEGÓ a Jacobs.
# ---------------------------------------------------------------------------
def test_recover_con_descartado_por_null_no_da_403_deja_pasar_a_jacobs(client, monkeypatch):
    duenio = uid(client, "descarte-c3c-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded",
                       status_previo="aborted", descartado_por=None, descartado_at=time.time()))
    falso = _instalar_jacobs_falso(monkeypatch, {
        ("POST", f"/pipeline/{pid}/recover"): respuesta(
            409, {"detail": {"code": "transicion_no_permitida", "status": "discarded"}}),
    })
    try:
        resp = client.post(f"/api/pipelines/{pid}/recover",
                           headers=cabeceras(client, "descarte-c3c-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "transicion_no_permitida"
        assert falso.llamadas, "el pedido tenía que LLEGAR a Jacobs, no frenarse en un 403"
    finally:
        client.portal.call(_borrar_pipelines, [pid])


# ---------------------------------------------------------------------------
# Fix round 1, Ruling 13(a) -- "el caso del doble recover": un pipeline YA
# recuperado (status volvió a 'aborted', store.py limpió status_previo/
# descartado_por/descartado_at a NULL en el mismo UPDATE) no es 'discarded'
# -- un segundo /recover no puede dar 403 (nadie "se lo ganó": ya no hay
# nada que recuperar). Se deja pasar a Jacobs, que rechaza con 409
# transicion_no_permitida porque 'aborted' no está en TRANSICIONES["recover"].
# ---------------------------------------------------------------------------
def test_recover_doble_no_da_403_deja_pasar_a_jacobs_que_da_409(client, monkeypatch):
    duenio = uid(client, "descarte-c3d-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "aborted",
                       status_previo=None, descartado_por=None, descartado_at=None))
    falso = _instalar_jacobs_falso(monkeypatch, {
        ("POST", f"/pipeline/{pid}/recover"): respuesta(
            409, {"detail": {"code": "transicion_no_permitida", "status": "aborted"}}),
    })
    try:
        resp = client.post(f"/api/pipelines/{pid}/recover",
                           headers=cabeceras(client, "descarte-c3d-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == {"code": "transicion_no_permitida", "status": "aborted"}
        assert falso.llamadas, "el segundo recover tenía que LLEGAR a Jacobs, no frenarse en un 403"
    finally:
        client.portal.call(_borrar_pipelines, [pid])


# ---------------------------------------------------------------------------
# Caso 4: POST /recover del superadmin sobre el descartado de OTRO -> 200.
# El superadmin no es dueño: _require_pipeline_owner lo rechazaría, así que
# recover_pipeline tiene que ramificar y no pasar por esa guardia para él.
# ---------------------------------------------------------------------------
def test_recover_del_superadmin_sobre_el_descartado_de_otro_es_200(client, client_superadmin, monkeypatch):
    duenio = uid(client, "descarte-c4-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded",
                       status_previo="aborted", descartado_por=duenio, descartado_at=time.time()))
    falso = _instalar_jacobs_falso(monkeypatch, {
        ("POST", f"/pipeline/{pid}/recover"): respuesta(200, {"pipeline_id": pid, "status": "aborted"}),
    })
    try:
        resp = client_superadmin.post(f"/api/pipelines/{pid}/recover")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"pipeline_id": pid, "status": "aborted"}
        [(metodo, ruta, _cuerpo)] = falso.llamadas
        assert (metodo, ruta) == ("POST", f"/pipeline/{pid}/recover")
    finally:
        client.portal.call(_borrar_pipelines, [pid])


def test_recover_del_superadmin_de_un_pipeline_inexistente_es_404(client_superadmin, monkeypatch):
    _instalar_jacobs_falso(monkeypatch, {})
    resp = client_superadmin.post(f"/api/pipelines/{uuid.uuid4()}/recover")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "pipeline_no_encontrado"


# ---------------------------------------------------------------------------
# Caso 5: POST /hide y /restore de un no-superadmin -> 403.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("accion", ["hide", "restore"])
def test_hide_y_restore_de_un_no_superadmin_es_403(client, monkeypatch, accion):
    operador = uid(client, "descarte-c5-operador", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, operador, TENANT, "discarded"))
    falso = _instalar_jacobs_falso(monkeypatch, {})
    try:
        resp = client.post(f"/api/pipelines/{pid}/{accion}",
                           headers=cabeceras(client, "descarte-c5-operador", "operator", tenant_id=TENANT))
        assert resp.status_code == 403, resp.text
        assert falso.llamadas == []
    finally:
        client.portal.call(_borrar_pipelines, [pid])


@pytest.mark.parametrize("accion", ["hide", "restore"])
def test_hide_y_restore_del_superadmin_llaman_a_jacobs(client, client_superadmin, monkeypatch, accion):
    operador = uid(client, "descarte-c5b-operador", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, operador, TENANT,
                       "discarded" if accion == "hide" else "hidden"))
    destino = "hidden" if accion == "hide" else "discarded"
    falso = _instalar_jacobs_falso(monkeypatch, {
        ("POST", f"/pipeline/{pid}/{accion}"): respuesta(200, {"pipeline_id": pid, "status": destino}),
    })
    try:
        resp = client_superadmin.post(f"/api/pipelines/{pid}/{accion}")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"pipeline_id": pid, "status": destino}
    finally:
        client.portal.call(_borrar_pipelines, [pid])


# ---------------------------------------------------------------------------
# Fix round 1, Ruling 13(b): hide/restore validan la FORMA del id (400
# pipeline_id_invalido) ANTES de llamar a Jacobs -- igual que discard/recover
# (vía _require_pipeline_owner). `abc%3Fx=1` decodifica a `abc?x=1`: no es un
# UUID, y antes de este fix se mandaba tal cual a Jacobs sin que este backend
# lo rechazara primero.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("accion", ["hide", "restore"])
def test_hide_y_restore_de_un_id_con_forma_invalida_es_400(client_superadmin, monkeypatch, accion):
    falso = _instalar_jacobs_falso(monkeypatch, {})
    resp = client_superadmin.post(f"/api/pipelines/abc%3Fx=1/{accion}")
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "pipeline_id_invalido"
    assert falso.llamadas == []


# ---------------------------------------------------------------------------
# Caso 6: GET /api/pipelines excluye discarded y hidden.
# ---------------------------------------------------------------------------
def test_el_listado_normal_excluye_descartados_y_ocultos(client):
    duenio = uid(client, "descarte-c6-duenio", "operator")
    ahora = time.time()
    pid_vivo = str(uuid.uuid4())
    pid_descartado = str(uuid.uuid4())
    pid_oculto = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid_vivo, duenio, TENANT, "completed", ahora - 3, ahora))
    client.portal.call(partial(_insertar_pipeline, pid_descartado, duenio, TENANT, "discarded", ahora - 2, ahora,
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora))
    client.portal.call(partial(_insertar_pipeline, pid_oculto, duenio, TENANT, "hidden", ahora - 1, ahora,
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora))
    try:
        resp = client.get("/api/pipelines", headers=cabeceras(client, "descarte-c6-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        ids = {p["pipeline_id"] for p in resp.json()["pipelines"]}
        assert ids == {pid_vivo}
    finally:
        client.portal.call(_borrar_pipelines, [pid_vivo, pid_descartado, pid_oculto])


# ---------------------------------------------------------------------------
# Caso 7: GET /api/pipelines?estado=discarded devuelve solo los descartados
# del usuario, ordenados por descartado_at DESC.
# ---------------------------------------------------------------------------
def test_estado_discarded_devuelve_solo_los_descartados_ordenados_por_descartado_at(client):
    duenio = uid(client, "descarte-c7-duenio", "operator")
    otro = uid(client, "descarte-c7-otro", "operator")
    ahora = time.time()
    pid_a = str(uuid.uuid4())  # descartado más viejo
    pid_b = str(uuid.uuid4())  # descartado más nuevo
    pid_vivo = str(uuid.uuid4())
    pid_ajeno = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid_a, duenio, TENANT, "discarded", ahora - 10, ahora - 5,
                       status_previo="expired", descartado_por=duenio, descartado_at=ahora - 5))
    client.portal.call(partial(_insertar_pipeline, pid_b, duenio, TENANT, "discarded", ahora - 8, ahora - 1,
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora - 1))
    client.portal.call(partial(_insertar_pipeline, pid_vivo, duenio, TENANT, "aborted", ahora - 3, ahora))
    client.portal.call(partial(_insertar_pipeline, pid_ajeno, otro, TENANT, "discarded", ahora - 4, ahora,
                       status_previo="aborted", descartado_por=otro, descartado_at=ahora))
    try:
        resp = client.get("/api/pipelines?estado=discarded",
                          headers=cabeceras(client, "descarte-c7-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        filas = resp.json()["pipelines"]
        assert [p["pipeline_id"] for p in filas] == [pid_b, pid_a]
        assert all(p["status"] == "discarded" for p in filas)
        assert all(p["descartado_at"] is not None for p in filas)
    finally:
        client.portal.call(_borrar_pipelines, [pid_a, pid_b, pid_vivo, pid_ajeno])


# ---------------------------------------------------------------------------
# Fix round 1, Ruling 13(c): la vista de descartados trae el costo_usd REAL
# -- misma fuente que la lista principal (SUM(axioma_usage.cost_usd) por
# pipeline_id). duracion_s se queda en None (su razón está en el código:
# updated_at de un descartado es el instante del descarte, no el fin real).
# ---------------------------------------------------------------------------
def test_estado_discarded_trae_el_costo_usd_real(client):
    duenio = uid(client, "descarte-c7c-duenio", "operator")
    ahora = time.time()
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded", ahora - 10, ahora - 5,
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora - 5))
    client.portal.call(_insertar_uso_de_pipeline, pid, 0.03, ahora - 9)
    client.portal.call(_insertar_uso_de_pipeline, pid, 0.02, ahora - 9)
    try:
        resp = client.get("/api/pipelines?estado=discarded",
                          headers=cabeceras(client, "descarte-c7c-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        (fila,) = [p for p in resp.json()["pipelines"] if p["pipeline_id"] == pid]
        assert fila["costo_usd"] == pytest.approx(0.05)
        assert fila["duracion_s"] is None
    finally:
        client.portal.call(_borrar_uso_de_pipeline, pid)
        client.portal.call(_borrar_pipelines, [pid])


def test_estado_fuera_del_vocabulario_cerrado_es_422(client):
    resp = client.get("/api/pipelines?estado=hidden",
                      headers=cabeceras(client, "descarte-c7b", "operator"))
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Caso 8: GET /api/pipelines/{id} de un hidden para su dueño no superadmin
# -> 404.
# ---------------------------------------------------------------------------
def test_get_de_un_hidden_para_su_dueno_no_superadmin_es_404(client):
    duenio = uid(client, "descarte-c8-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "hidden",
                       status_previo="aborted", descartado_por=duenio, descartado_at=time.time()))
    try:
        resp = client.get(f"/api/pipelines/{pid}",
                          headers=cabeceras(client, "descarte-c8-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "pipeline_no_encontrado"
    finally:
        client.portal.call(_borrar_pipelines, [pid])


def test_get_de_un_hidden_para_el_superadmin_no_es_404_por_oculto(client, client_superadmin, monkeypatch):
    """El superadmin SÍ pasa el chequeo de oculto de _require_pipeline_owner
    -- pero igual no es el DUEÑO de este pipeline, así que sigue dando 404
    por la regla de pertenencia (es_del_usuario), no por estar hidden. Se
    fija con un pipeline propiedad del propio superadmin para aislar el
    chequeo de "hidden" del chequeo de dueño."""
    superadmin_id = uid(client, "descarte-c8b-superadmin", "superadmin")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, superadmin_id, "1", "hidden",
                       status_previo="aborted", descartado_por=superadmin_id, descartado_at=time.time()))
    falso = _instalar_jacobs_falso(monkeypatch, {
        ("GET", f"/pipeline/{pid}"): respuesta(200, {"pipeline": {"status": "hidden"}, "steps": []}),
    })
    try:
        resp = client.get(f"/api/pipelines/{pid}",
                          headers=cabeceras(client, "descarte-c8b-superadmin", "superadmin", tenant_id="1"))
        assert resp.status_code == 200, resp.text
        assert falso.llamadas
    finally:
        client.portal.call(_borrar_pipelines, [pid])


# ---------------------------------------------------------------------------
# Caso 9: GET /api/admin/pipelines/ocultos de un no-superadmin -> 403; del
# superadmin -> los hidden de TODOS los usuarios.
# ---------------------------------------------------------------------------
def test_ocultos_de_un_no_superadmin_es_403(client):
    resp = client.get("/api/admin/pipelines/ocultos",
                      headers=cabeceras(client, "descarte-c9-operador", "operator"))
    assert resp.status_code == 403, resp.text


def test_ocultos_del_superadmin_trae_los_de_todos_los_usuarios(client, client_superadmin):
    duenio_a = uid(client, "descarte-c9-duenio-a", "operator")
    duenio_b = uid(client, "descarte-c9-duenio-b", "operator")
    ahora = time.time()
    pid_a = str(uuid.uuid4())
    pid_b = str(uuid.uuid4())
    pid_visible = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid_a, duenio_a, TENANT, "hidden", ahora - 5, ahora - 5,
                       status_previo="aborted", descartado_por=duenio_a, descartado_at=ahora - 5))
    client.portal.call(partial(_insertar_pipeline, pid_b, duenio_b, TENANT, "hidden", ahora - 2, ahora - 2,
                       status_previo="expired", descartado_por=duenio_b, descartado_at=ahora - 1))
    client.portal.call(partial(_insertar_pipeline, pid_visible, duenio_a, TENANT, "discarded", ahora, ahora,
                       status_previo="aborted", descartado_por=duenio_a, descartado_at=ahora))
    try:
        resp = client_superadmin.get("/api/admin/pipelines/ocultos")
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        # Fix round 1, Ruling 13(d): "has_more", el mismo contrato de
        # paginación que ya usa GET /api/pipelines -- no "hay_mas".
        assert "has_more" in cuerpo, cuerpo
        assert cuerpo["has_more"] is False
        assert "hay_mas" not in cuerpo
        filas = {f["pipeline_id"]: f for f in cuerpo["pipelines"] if f["pipeline_id"] in (pid_a, pid_b)}
        assert set(filas) == {pid_a, pid_b}
        assert filas[pid_a]["user_id"] == duenio_a
        assert filas[pid_a]["descartado_por"] == duenio_a
        assert filas[pid_b]["user_id"] == duenio_b
        # descartado_at DESC: pid_b (más nuevo) antes que pid_a.
        orden = [f["pipeline_id"] for f in cuerpo["pipelines"] if f["pipeline_id"] in (pid_a, pid_b)]
        assert orden == [pid_b, pid_a]
    finally:
        client.portal.call(_borrar_pipelines, [pid_a, pid_b, pid_visible])


# ---------------------------------------------------------------------------
# Caso 10: EXPLAIN de las tres consultas nuevas/cambiadas.
# ---------------------------------------------------------------------------
async def _insertar_pipelines_bulk(filas):
    """`cur.executemany` directo (fix round 2, Ruling 16 punto 5): sembrar
    miles de filas de a una vía `_insertar_pipeline`/`sql()` es demasiado
    lento para un test que corre en cada CI -- una sola ida y vuelta de red
    por lote. `filas`: lista de tuplas (pipeline_id, status, created_at,
    updated_at, user_id, tenant_id, owner_ack_at, status_previo,
    descartado_por, descartado_at)."""
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


def _filas_pipelines_bulk(tenant_id, n, status, descartado=False, offset=0):
    ahora = time.time()
    out = []
    for i in range(n):
        pid = str(uuid.uuid4())
        creado = ahora - (offset + n - i) * 2
        actualizado = creado + 1.0
        if descartado:
            out.append((pid, "discarded", creado, actualizado, "x", tenant_id, creado,
                        "aborted", "x", creado))
        else:
            out.append((pid, status, creado, actualizado, "x", tenant_id, creado, None, None, None))
    return out


# Fix round 2 (2026-09-22), Ruling 16 del controlador: la aceptación del
# filesort de la ronda anterior (fix round 1, Ruling 13(e)) NO se sostenía.
# `MAX_PIPELINES` acota los pipelines CONCURRENTES, no el histórico --
# `status NOT IN ('discarded','hidden')` incluye TODO lo terminado
# (completed/failed/aborted/expired), que crece sin techo. Un usuario con
# 5000 completados y 50 descartados leería 5000 filas para ordenar y servir
# 50. La medición anterior (363 filas, 1,6x) era ruido a escala de décimas
# de milisegundo, no evidencia de que el plan con filesort ganara al caso
# real. `Using temporary` tampoco protegía nada en esta consulta -- nunca
# apareció, con o sin filesort.
#
# La consulta ahora lleva `IGNORE INDEX (idx_pipelines_descartados,
# idx_pipelines_ocultos)` (api/pipelines.py): el plan vuelve a ser
# DETERMINISTA -- range scan EN ORDEN por idx_jacobs_pipelines_duenio, sin
# filesort, que corta en el LIMIT -- sin depender de qué estadísticas tenga
# la tabla en un momento dado. Medido con número en
# docs/carga-sql-pipelines-del-usuario-indice-2026-09-22.md (método +
# comando exacto ahí).
#
# Las DOS formas de dato que pidió el controlador, ambas verificando el
# MISMO contrato (idx_jacobs_pipelines_duenio, sin filesort):
@pytest.mark.parametrize("nombre_forma, n_terminados, n_descartados", [
    ("historial-largo", 5000, 50),
    ("muchos-descartados", 3, 60),
])
def test_explain_pipelines_del_usuario_usa_idx_jacobs_pipelines_duenio_sin_filesort(
        client, nombre_forma, n_terminados, n_descartados):
    tenant_real = f"TENANT-EXPLAIN-T4-{nombre_forma}"
    try:
        client.portal.call(
            _insertar_pipelines_bulk,
            _filas_pipelines_bulk(tenant_real, n_terminados, "completed"))
        client.portal.call(
            _insertar_pipelines_bulk,
            _filas_pipelines_bulk(tenant_real, n_descartados, "discarded", descartado=True, offset=n_terminados))
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)

        filas = client.portal.call(
            sql, "EXPLAIN " + mod.SQL_PIPELINES_DEL_USUARIO,
            ("x", tenant_real, mod.LISTA_PIPELINES_MAX, 5), True)
        ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
        assert tabla == "jacobs_pipelines"
        assert clave == "idx_jacobs_pipelines_duenio", filas
        assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas
    finally:
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE tenant_id=%s", (tenant_real,))


def test_explain_descartados_del_usuario_usa_idx_pipelines_descartados(client):
    """Con la base de test casi vacía, el optimizador puede preferir
    idx_pipelines_ocultos (status, descartado_at) sobre idx_pipelines_descartados
    (user_id, tenant_id, status, descartado_at): con estadísticas triviales
    (0-1 filas) el costo estimado de las dos empata y MariaDB desempata por
    key_len más corto, NO por selectividad real -- medido 2026-09-22 contra
    esta misma base. Con datos que se PARECEN a producción (muchos
    descartados de OTROS tenants, pocos del tenant real) el plan usa
    idx_pipelines_descartados con `type: range` -- verificado con el mismo
    EXPLAIN. Se siembra ese ruido acá: el `EXPLAIN sobre la consulta REAL`
    que pide LAS CUATRO/indexing sólo prueba algo si el plan que mide se
    parece al que corre con datos reales, no al de una tabla vacía."""
    tenant_real = "TENANT-EXPLAIN-T4-idx"
    ids_ruido = [str(uuid.uuid4()) for _ in range(60)]
    ids_reales = [str(uuid.uuid4()) for _ in range(3)]
    try:
        for i, pid in enumerate(ids_ruido):
            client.portal.call(partial(
                _insertar_pipeline, pid, f"ruido-{i}", f"ruido-tenant-{i}", "discarded",
                status_previo="aborted", descartado_por=f"ruido-{i}", descartado_at=time.time()))
        for pid in ids_reales:
            client.portal.call(partial(
                _insertar_pipeline, pid, "x", tenant_real, "discarded",
                status_previo="aborted", descartado_por="x", descartado_at=time.time()))
        # Fix round 1, Ruling 13(e): ANALYZE TABLE antes del EXPLAIN, no
        # sólo el sembrado -- estadísticas persistentes de InnoDB, el plan
        # depende de qué corrió antes en la sesión sin esto. Mismo motivo
        # que test_explain_pipelines_del_usuario_usa_idx_jacobs_pipelines_duenio_sin_filesort
        # (fix round 2, Ruling 16).
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)

        filas = client.portal.call(
            sql, "EXPLAIN " + mod.SQL_DESCARTADOS_DEL_USUARIO,
            ("x", tenant_real, mod.LISTA_PIPELINES_MAX, 5), True)
        ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
        assert tabla == "jacobs_pipelines"
        assert clave == "idx_pipelines_descartados", filas
        assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas
    finally:
        client.portal.call(_borrar_pipelines, ids_ruido + ids_reales)


def test_explain_ocultos_usa_idx_pipelines_ocultos(client):
    from api.admin.pipelines_ocultos import SQL_OCULTOS

    filas = client.portal.call(sql, "EXPLAIN " + SQL_OCULTOS, (mod.LISTA_PIPELINES_MAX, 5), True)
    ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
    assert tabla == "jacobs_pipelines"
    assert clave == "idx_pipelines_ocultos", filas
    assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas


# ---------------------------------------------------------------------------
# Caso 11: axioma_usage intacto tras ocultar (Jacobs simulado + transición
# aplicada directo en la base de test, spec §2: "ninguna fila sale de la
# base -- tampoco de axioma_usage").
# ---------------------------------------------------------------------------
async def _insertar_uso_de_pipeline(pipeline_id, cost, creado_ts):
    await sql(
        "INSERT INTO axioma_usage "
        "(tenant_id, user_id, facet, model, tokens_in, tokens_out, cost_usd, request_type, created_at, pipeline_id) "
        "VALUES (777001, 777001, 'jekyll', 'm1', 100, 50, %s, 'pipeline', FROM_UNIXTIME(%s), %s)",
        (cost, creado_ts, pipeline_id))


async def _suma_de_costo(pipeline_id):
    filas = await sql("SELECT SUM(cost_usd) FROM axioma_usage WHERE pipeline_id=%s", (pipeline_id,), True)
    return float(filas[0][0])


async def _aplicar_transicion_en_la_base(pipeline_id, status):
    await sql("UPDATE jacobs_pipelines SET status=%s WHERE pipeline_id=%s", (status, pipeline_id))


def test_axioma_usage_intacto_tras_ocultar(client, client_superadmin, monkeypatch):
    duenio = uid(client, "descarte-c11-duenio", "operator")
    ahora = time.time()
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded", ahora - 5, ahora - 5,
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora - 4))
    client.portal.call(_insertar_uso_de_pipeline, pid, 0.03, ahora - 6)
    client.portal.call(_insertar_uso_de_pipeline, pid, 0.02, ahora - 6)
    falso = _instalar_jacobs_falso(monkeypatch, {
        ("POST", f"/pipeline/{pid}/hide"): respuesta(200, {"pipeline_id": pid, "status": "hidden"}),
    })
    try:
        antes = client.portal.call(_suma_de_costo, pid)
        assert antes == pytest.approx(0.05)

        resp = client_superadmin.post(f"/api/pipelines/{pid}/hide")
        assert resp.status_code == 200, resp.text
        # Jacobs está simulado: la transición real la aplica Jacobs, acá se
        # replica en la base de test para verificar que OCULTAR no toca
        # axioma_usage (spec §2: "ninguna fila sale de la base").
        client.portal.call(_aplicar_transicion_en_la_base, pid, "hidden")

        despues = client.portal.call(_suma_de_costo, pid)
        assert despues == pytest.approx(0.05)
        assert antes == despues
    finally:
        client.portal.call(_borrar_uso_de_pipeline, pid)
        client.portal.call(_borrar_pipelines, [pid])


# ---------------------------------------------------------------------------
# `_json_de_jacobs`/`_rechazo_de_jacobs` propagan los 4 codes nuevos de
# Jacobs (404/409/422) con el MISMO status_code y detail -- sin esto,
# CODIGOS_DE_JACOBS no los declaraba y _rechazo_de_jacobs los aplanaba al
# jacobs_rechazo genérico, perdiendo el `code` (y el `status` de
# transicion_no_permitida) que el cliente necesita para mostrar el motivo
# correcto (Detalles a resolver del brief, step 4).
# ---------------------------------------------------------------------------
def test_transicion_no_permitida_se_propaga_con_su_code_y_status():
    cuerpo = {"detail": {"code": "transicion_no_permitida", "status": "running"}}
    exc = mod._rechazo_de_jacobs(409, cuerpo, "")
    assert exc.status_code == 409
    assert exc.detail == {"code": "transicion_no_permitida", "status": "running"}


def test_cambio_concurrente_se_propaga_con_su_code():
    cuerpo = {"detail": {"code": "cambio_concurrente"}}
    exc = mod._rechazo_de_jacobs(409, cuerpo, "")
    assert exc.status_code == 409
    assert exc.detail == {"code": "cambio_concurrente"}


def test_estado_previo_invalido_se_propaga_con_su_code():
    cuerpo = {"detail": {"code": "estado_previo_invalido"}}
    exc = mod._rechazo_de_jacobs(422, cuerpo, "")
    assert exc.status_code == 422
    assert exc.detail == {"code": "estado_previo_invalido"}


def test_pipeline_no_encontrado_de_jacobs_se_propaga_con_su_code():
    cuerpo = {"detail": {"code": "pipeline_no_encontrado"}}
    exc = mod._rechazo_de_jacobs(404, cuerpo, "")
    assert exc.status_code == 404
    assert exc.detail == {"code": "pipeline_no_encontrado"}


def test_discard_propaga_el_409_de_jacobs_con_su_code_y_status(client, monkeypatch):
    duenio = uid(client, "descarte-jrej-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "aborted"))
    _instalar_jacobs_falso(monkeypatch, {
        ("POST", f"/pipeline/{pid}/discard"): respuesta(
            409, {"detail": {"code": "transicion_no_permitida", "status": "running"}}),
    })
    try:
        resp = client.post(f"/api/pipelines/{pid}/discard",
                           headers=cabeceras(client, "descarte-jrej-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == {"code": "transicion_no_permitida", "status": "running"}
    finally:
        client.portal.call(_borrar_pipelines, [pid])
