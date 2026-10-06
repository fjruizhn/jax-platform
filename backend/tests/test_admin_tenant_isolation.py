"""Aislamiento del superadmin por tenant con identidades y datos reales de test."""
import time
import uuid
from datetime import date, datetime, time as hora, timedelta

from api.admin import dashboard, users as users_mod
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


async def _no_service(*args):
    return {"name": args[0], "port": None, "status": "sin_configurar", "latency_ms": None}


async def _connected():
    return {"status": "connected"}
