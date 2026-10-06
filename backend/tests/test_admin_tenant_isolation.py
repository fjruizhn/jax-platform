"""Aislamiento del superadmin por tenant con identidades y datos reales de test."""
import time
import uuid
from datetime import date, datetime, time as hora, timedelta, timezone

from api.admin import dashboard, memoria as memoria_mod, usage as usage_mod, users as users_mod
from tests.identidades import cabeceras, sql, uid
from tests.test_pipelines_descarte import _instalar_jacobs_falso, respuesta


TENANT_A = "870001"
TENANT_B = "870002"


def _admin_b(client):
    return cabeceras(client, "tenant-isolation-admin-b", "superadmin", tenant_id=TENANT_B)


async def _insertar_pipeline(pid, user_id, tenant_id, status, descartado_at):
    await sql(
        "INSERT INTO jacobs_pipelines "
        "(pipeline_id, name, invoked_by, mode, status, created_at, updated_at, user_id, tenant_id, "
        "owner_ack_at, status_previo, descartado_por, descartado_at) "
        "VALUES (%s, 'aislamiento', 'plataforma', 'supervised', %s, %s, %s, %s, %s, %s, 'aborted', %s, %s)",
        (pid, status, time.time(), time.time(), user_id, tenant_id, time.time(), user_id, time.time()),
    )


def test_listas_y_acciones_de_usuarios_no_cruzan_tenant(client, monkeypatch):
    target_a = uid(client, "tenant-isolation-target-a", "operator", TENANT_A)
    uid(client, "tenant-isolation-admin-b", "superadmin", TENANT_B)
    monkeypatch.setattr(users_mod.smtp_config, "cargar_settings", _smtp_test)
    monkeypatch.setattr(users_mod.auth_api, "_send_reset_email", lambda *args: None)
    try:
        headers = _admin_b(client)
        listado = client.get("/api/admin/users", headers=headers)
        assert listado.status_code == 200, listado.text
        assert str(target_a) not in {str(row["user_id"]) for row in listado.json()["users"]}

        assert client.get(f"/api/admin/users/{target_a}/audit", headers=headers).status_code == 404
        assert client.post(f"/api/admin/users/{target_a}/reset-link", headers=headers).status_code == 404
        assert client.post(f"/api/admin/users/{target_a}/unlock", headers=headers).status_code == 404
        assert client.post(f"/api/admin/users/{target_a}/revoke-sessions", headers=headers).status_code == 404
        assert client.put(f"/api/admin/users/{target_a}", json={"status": "inactive"}, headers=headers).status_code == 404
        assert client.post(
            f"/api/admin/users/{target_a}/password",
            json={"new_password": "TenantIsolation_2026!"}, headers=headers,
        ).status_code == 404
        assert client.post(f"/api/admin/users/{target_a}/baja", headers=headers).status_code == 404
    finally:
        client.portal.call(sql, "DELETE FROM jax_users WHERE user_id = %s", (target_a,))


async def _smtp_test():
    return object()


def test_listados_de_pipelines_ocultos_y_descartados_son_del_tenant(client):
    user_a = uid(client, "tenant-pipeline-a", "operator", TENANT_A)
    user_b = uid(client, "tenant-pipeline-b", "operator", TENANT_B)
    pid_a_hidden, pid_b_hidden = str(uuid.uuid4()), str(uuid.uuid4())
    pid_a_discarded, pid_b_discarded = str(uuid.uuid4()), str(uuid.uuid4())
    ids = (pid_a_hidden, pid_b_hidden, pid_a_discarded, pid_b_discarded)
    for pid, owner, tenant, status in (
        (pid_a_hidden, user_a, TENANT_A, "hidden"),
        (pid_b_hidden, user_b, TENANT_B, "hidden"),
        (pid_a_discarded, user_a, TENANT_A, "discarded"),
        (pid_b_discarded, user_b, TENANT_B, "discarded"),
    ):
        client.portal.call(_insertar_pipeline, pid, owner, tenant, status, time.time())
    try:
        headers = _admin_b(client)
        ocultos = client.get("/api/admin/pipelines/ocultos", headers=headers)
        descartados = client.get("/api/admin/pipelines/descartados", headers=headers)
        assert ocultos.status_code == descartados.status_code == 200
        assert pid_b_hidden in {row["pipeline_id"] for row in ocultos.json()["pipelines"]}
        assert pid_a_hidden not in {row["pipeline_id"] for row in ocultos.json()["pipelines"]}
        assert pid_b_discarded in {row["pipeline_id"] for row in descartados.json()["pipelines"]}
        assert pid_a_discarded not in {row["pipeline_id"] for row in descartados.json()["pipelines"]}
    finally:
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE pipeline_id IN (%s, %s, %s, %s)", ids)
        client.portal.call(sql, "DELETE FROM jax_users WHERE user_id IN (%s, %s)", (user_a, user_b))


def test_hide_y_restore_no_llaman_a_jacobs_para_otro_tenant(client, monkeypatch):
    user_a = uid(client, "tenant-hide-target-a", "operator", TENANT_A)
    pid = str(uuid.uuid4())
    client.portal.call(_insertar_pipeline, pid, user_a, TENANT_A, "aborted", time.time())
    falso = _instalar_jacobs_falso(monkeypatch, {
        ("POST", f"/pipeline/{pid}/hide"): respuesta(200, {"pipeline_id": pid, "status": "hidden"}),
        ("POST", f"/pipeline/{pid}/restore"): respuesta(200, {"pipeline_id": pid, "status": "aborted"}),
    })
    try:
        headers = _admin_b(client)
        assert client.post(f"/api/pipelines/{pid}/hide", headers=headers).status_code == 404
        assert client.post(f"/api/pipelines/{pid}/restore", headers=headers).status_code == 404
        assert falso.llamadas == []
    finally:
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE pipeline_id = %s", (pid,))


def test_recover_y_auditoria_no_cruzan_tenant(client, monkeypatch):
    user_a = uid(client, "tenant-pipeline-action-a", "operator", TENANT_A)
    pid = str(uuid.uuid4())
    client.portal.call(_insertar_pipeline, pid, user_a, TENANT_A, "discarded", time.time())
    falso = _instalar_jacobs_falso(monkeypatch, {
        ("POST", f"/pipeline/{pid}/recover"): respuesta(200, {"pipeline_id": pid, "status": "aborted"}),
    })
    try:
        headers = _admin_b(client)
        recover = client.post(f"/api/pipelines/{pid}/recover", headers=headers)
        audit = client.get(f"/api/pipelines/{pid}/auditoria-descarte", headers=headers)
        assert recover.status_code == audit.status_code == 404
        assert falso.llamadas == []
    finally:
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE pipeline_id = %s", (pid,))
        client.portal.call(sql, "DELETE FROM jax_users WHERE user_id = %s", (user_a,))


def test_dashboard_cuenta_solo_datos_del_tenant(client, monkeypatch):
    admin_b = uid(client, "tenant-dashboard-admin-b", "superadmin", TENANT_B)
    usuario_a = uid(client, "tenant-dashboard-user-a", "operator", TENANT_A)
    user_a_id, user_b_id = int(usuario_a), int(admin_b)
    hoy = date.today()
    inicio = datetime.combine(hoy, hora.min)
    uso_a = client.portal.call(
        sql,
        "INSERT INTO axioma_usage (tenant_id,user_id,facet,model,tokens_in,tokens_out,cost_usd,request_type,created_at) "
        "VALUES (%s,%s,'tenant-test','m',1,1,0,'chat',%s)",
        (int(TENANT_A), user_a_id, inicio + timedelta(seconds=1)),
    )
    uso_b = client.portal.call(
        sql,
        "INSERT INTO axioma_usage (tenant_id,user_id,facet,model,tokens_in,tokens_out,cost_usd,request_type,created_at) "
        "VALUES (%s,%s,'tenant-test','m',1,1,0,'imagen',%s)",
        (int(TENANT_B), user_b_id, inicio + timedelta(seconds=2)),
    )
    pid_a, pid_b = str(uuid.uuid4()), str(uuid.uuid4())
    client.portal.call(_insertar_pipeline, pid_a, user_a_id, TENANT_A, "completed", time.time())
    client.portal.call(_insertar_pipeline, pid_b, user_b_id, TENANT_B, "completed", time.time())
    monkeypatch.setattr(dashboard, "_servicio", _no_service)
    monkeypatch.setattr(dashboard, "_check_db", _connected)
    try:
        response = client.get("/api/admin/dashboard", headers=_admin_b(client))
        assert response.status_code == 200, response.text
        stats = response.json()["stats"]
        inicio_dia, fin_dia = dashboard._rango_del_dia(hoy)
        ((mensajes, imagenes),) = client.portal.call(
            sql,
            "SELECT COUNT(*), COALESCE(SUM(request_type='imagen'), 0) FROM axioma_usage "
            "WHERE tenant_id=%s AND created_at >= %s AND created_at < %s",
            (int(TENANT_B), inicio_dia, fin_dia), True,
        )
        assert (stats["messages_today"], stats["images_generated"]) == (mensajes, imagenes)
        assert stats["pipelines_completed"] == 1
        ((users_active,),) = client.portal.call(
            sql, "SELECT COUNT(*) FROM jax_users WHERE tenant_id=%s AND status='active'", (int(TENANT_B),), True)
        assert stats["users_active"] == users_active
    finally:
        client.portal.call(sql, "DELETE FROM axioma_usage WHERE id IN (%s, %s)", (uso_a, uso_b))
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE pipeline_id IN (%s, %s)", (pid_a, pid_b))
        client.portal.call(sql, "DELETE FROM jax_users WHERE user_id IN (%s, %s)", (admin_b, usuario_a))


def test_dashboard_cuenta_usuarios_bloqueados_solo_del_tenant(client, monkeypatch):
    user_a = uid(client, "tenant-dashboard-isolation-a", "operator", TENANT_A)
    user_b = uid(client, "tenant-dashboard-isolation-b", "operator", TENANT_B)
    monkeypatch.setattr(dashboard, "_servicio", _no_service)
    monkeypatch.setattr(dashboard, "_check_db", _connected)
    headers = _admin_b(client)
    before = client.get("/api/admin/dashboard", headers=headers)
    assert before.status_code == 200, before.text
    locks = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
    try:
        client.portal.call(sql, "UPDATE jax_users SET locked_until=%s WHERE user_id IN (%s, %s)",
                           (locks, user_a, user_b))
        after = client.get("/api/admin/dashboard", headers=headers)
        assert after.status_code == 200, after.text
        before_stats = before.json()["stats"]
        after_stats = after.json()["stats"]
        assert after_stats["users_locked"] == before_stats["users_locked"] + 1
    finally:
        client.portal.call(sql, "UPDATE jax_users SET locked_until=NULL WHERE user_id IN (%s, %s)",
                           (user_a, user_b))
        client.portal.call(sql, "DELETE FROM jax_users WHERE user_id IN (%s, %s)", (user_a, user_b))


def test_dashboard_cuenta_hechos_sin_verificar_solo_del_tenant(client, monkeypatch):
    user_a = uid(client, "tenant-dashboard-fact-a", "operator", TENANT_A)
    user_b = uid(client, "tenant-dashboard-fact-b", "operator", TENANT_B)
    monkeypatch.setattr(dashboard, "_servicio", _no_service)
    monkeypatch.setattr(dashboard, "_check_db", _connected)
    headers = _admin_b(client)
    before = client.get("/api/admin/dashboard", headers=headers)
    assert before.status_code == 200, before.text
    fact_a = client.portal.call(_insertar_fact, "tenant-dashboard-unverified-a", user_a)
    fact_b = client.portal.call(_insertar_fact, "tenant-dashboard-unverified-b", user_b)
    try:
        after = client.get("/api/admin/dashboard", headers=headers)
        assert after.status_code == 200, after.text
        assert after.json()["stats"]["facts_unverified"] == before.json()["stats"]["facts_unverified"] + 1
    finally:
        client.portal.call(sql, "DELETE FROM facts WHERE id IN (%s, %s)", (fact_a, fact_b))
        client.portal.call(sql, "DELETE FROM jax_users WHERE user_id IN (%s, %s)", (user_a, user_b))


async def _insertar_fact(texto, user_id, *, is_verified=False, expires_at=None):
    return await sql(
        "INSERT INTO facts (fact_uuid, fact_text, fact_type, is_verified, expires_at, user_id, project_id) "
        "VALUES (UUID(), %s, 'technical', %s, %s, %s, %s)",
        (texto, is_verified, expires_at, user_id, None),
    )


async def _crear_proyecto_de_tenant(tenant_id, nombre):
    project_id = await sql(
        "INSERT INTO projects (project_uuid, name, status) VALUES (UUID(), %s, 'active')",
        (nombre,),
    )
    await sql(
        "INSERT INTO jax_project_scope "
        "(project_id, tenant_id, status, created_at, created_by, updated_at) "
        "VALUES (%s, %s, 'ACTIVE', NOW(6), 'test', NOW(6))",
        (project_id, int(tenant_id)),
    )
    return project_id


def test_memoria_listado_y_conteo_son_del_tenant(client):
    user_a = uid(client, "tenant-facts-user-a", "operator", TENANT_A)
    user_b = uid(client, "tenant-facts-user-b", "operator", TENANT_B)
    project_a = client.portal.call(_crear_proyecto_de_tenant, TENANT_A, "tenant-facts-project-a")
    project_b = client.portal.call(_crear_proyecto_de_tenant, TENANT_B, "tenant-facts-project-b")
    fact_a = client.portal.call(_insertar_fact, "tenant-facts-a", user_a)
    fact_b = client.portal.call(_insertar_fact, "tenant-facts-b", user_b)
    global_fact = client.portal.call(_insertar_fact, "tenant-facts-unscoped", None)
    project_fact_a = client.portal.call(_insertar_fact, "tenant-facts-project-a", None)
    project_fact_b = client.portal.call(_insertar_fact, "tenant-facts-project-b", None)
    client.portal.call(sql, "UPDATE facts SET project_id=%s WHERE id=%s", (project_a, project_fact_a))
    client.portal.call(sql, "UPDATE facts SET project_id=%s WHERE id=%s", (project_b, project_fact_b))
    try:
        response = client.get("/api/admin/memoria/hechos?verificado=false&limite=500",
                              headers=_admin_b(client))
        assert response.status_code == 200, response.text
        body = response.json()
        ids = {fact["id"] for fact in body["hechos"]}
        assert fact_b in ids
        assert fact_a not in ids
        assert global_fact not in ids
        assert project_fact_b in ids
        assert project_fact_a not in ids
        assert body["total"] == len(ids)
    finally:
        client.portal.call(sql, "DELETE FROM facts WHERE id IN (%s, %s, %s, %s, %s)",
                           (fact_a, fact_b, global_fact, project_fact_a, project_fact_b))
        client.portal.call(sql, "DELETE FROM jax_project_scope WHERE project_id IN (%s, %s)",
                           (project_a, project_b))
        client.portal.call(sql, "DELETE FROM projects WHERE id IN (%s, %s)", (project_a, project_b))
        client.portal.call(sql, "DELETE FROM jax_users WHERE user_id IN (%s, %s)", (user_a, user_b))


def test_memoria_no_aprueba_hecho_de_otro_tenant(client, monkeypatch):
    user_a = uid(client, "tenant-approve-user-a", "operator", TENANT_A)
    fact_a = client.portal.call(_insertar_fact, "tenant-approve-a", user_a)
    try:
        response = client.post("/api/admin/memoria/hechos/aprobar", json={"ids": [fact_a]},
                               headers=_admin_b(client))
        assert response.status_code == 404, response.text
        ((verified,),) = client.portal.call(sql, "SELECT is_verified FROM facts WHERE id=%s", (fact_a,), True)
        assert not verified
    finally:
        client.portal.call(sql, "DELETE FROM facts WHERE id=%s", (fact_a,))
        client.portal.call(sql, "DELETE FROM jax_users WHERE user_id=%s", (user_a,))


def test_memoria_no_corrige_hecho_de_otro_tenant(client, monkeypatch):
    user_a = uid(client, "tenant-correct-user-a", "operator", TENANT_A)
    fact_a = client.portal.call(_insertar_fact, "tenant-correct-a", user_a)
    llamadas_embedding = []
    memoria = type("MemoriaFalsa", (), {"get_embedding": lambda self, _text: llamadas_embedding.append(_text)})()
    monkeypatch.setattr(memoria_mod, "_memoria_conectada", lambda: _memoria_lista(memoria))
    try:
        response = client.post(f"/api/admin/memoria/hechos/{fact_a}/corregir", json={"texto": "corregido"},
                               headers=_admin_b(client))
        assert response.status_code == 404, response.text
        assert llamadas_embedding == []
        ((superseded,),) = client.portal.call(sql, "SELECT superseded_by FROM facts WHERE id=%s", (fact_a,), True)
        assert superseded is None
    finally:
        client.portal.call(sql, "DELETE FROM facts WHERE id=%s", (fact_a,))
        client.portal.call(sql, "DELETE FROM jax_users WHERE user_id=%s", (user_a,))


async def _sin_embedding():
    return []


async def _memoria_lista(memoria):
    return memoria


def test_memoria_no_caduca_hecho_de_otro_tenant(client, monkeypatch):
    user_a = uid(client, "tenant-expire-user-a", "operator", TENANT_A)
    fact_a = client.portal.call(_insertar_fact, "tenant-expire-a", user_a)
    memoria = type("MemoriaFalsa", (), {"expire_fact": lambda self, *_args: _true()})()
    monkeypatch.setattr(memoria_mod, "_memoria_conectada", lambda: _memoria_lista(memoria))
    try:
        response = client.post(f"/api/admin/memoria/hechos/{fact_a}/caducar",
                               json={"vence_at": "2030-01-01T00:00:00Z"}, headers=_admin_b(client))
        assert response.status_code == 404, response.text
    finally:
        client.portal.call(sql, "DELETE FROM facts WHERE id=%s", (fact_a,))
        client.portal.call(sql, "DELETE FROM jax_users WHERE user_id=%s", (user_a,))


async def _true():
    return True


def test_usage_no_expone_facetas_de_otro_tenant(client):
    user_a = uid(client, "tenant-usage-user-a", "operator", TENANT_A)
    user_b = uid(client, "tenant-usage-user-b", "operator", TENANT_B)
    since = datetime.combine(date.today() - timedelta(days=1), hora.min)
    ids = []
    for user_id, tenant, facet in ((user_a, TENANT_A, "tenant-private-a"),
                                   (user_b, TENANT_B, "tenant-private-b")):
        ids.append(client.portal.call(
            sql,
            "INSERT INTO axioma_usage (tenant_id,user_id,facet,model,tokens_in,tokens_out,cost_usd,request_type,created_at) "
            "VALUES (%s,%s,%s,'m',1,1,0,'chat',%s)",
            (int(tenant), user_id, facet, since + timedelta(hours=1)),
        ))
    try:
        response = client.get("/api/admin/usage?period=week", headers=_admin_b(client))
        assert response.status_code == 200, response.text
        body = response.json()
        facets = {row["facet"] for row in body["by_facet"]}
        assert "tenant-private-b" in facets
        assert "tenant-private-a" not in facets
        assert "tenant-private-a" not in body["chart_data"]["datasets"]
    finally:
        client.portal.call(sql, "DELETE FROM axioma_usage WHERE id IN (%s, %s)", tuple(ids))
        client.portal.call(sql, "DELETE FROM jax_users WHERE user_id IN (%s, %s)", (user_a, user_b))


async def _no_service(*args):
    return {"name": args[0], "port": None, "status": "sin_configurar", "latency_ms": None}


async def _connected():
    return {"status": "connected"}
