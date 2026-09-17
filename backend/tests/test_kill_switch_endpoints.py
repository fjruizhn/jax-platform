"""Endpoints del kill switch (plan 2026-09-16-frente-b-kill-switch, Task 7).
Contra jax_memory_test (conftest). El freno es la ruta temporal de la suite."""
import os

import pytest

import interruptor
import kill_switch
from tests.identidades import auth, cabeceras, sql, token_para

ACTIVAR = "/api/admin/kill-switch/activar"
REANUDAR = "/api/admin/kill-switch/reanudar"
ESTADO = "/api/admin/kill-switch"
ES_ROOT = os.geteuid() == 0


def _superadmin(usuarios):
    user_id, _ = usuarios(role="superadmin")
    return user_id, auth(token_para(user_id, role="superadmin"))


async def _auditoria(user_id):
    filas = await sql("SELECT accion, user_id FROM kill_switch_audit WHERE user_id = %s ORDER BY id",
                      (user_id,), True)
    return [tuple(f) for f in filas]


def test_solo_un_superadmin_ve_o_toca_el_freno(client):
    h = cabeceras(client, "ks-operador")
    assert client.get(ESTADO, headers=h).status_code == 403
    assert client.post(ACTIVAR, headers=h).status_code == 403
    assert client.post(REANUDAR, headers=h).status_code == 403
    assert not interruptor.interruptor_activo()


def test_sin_token_no_hay_freno(client):
    assert client.post(ACTIVAR).status_code in (401, 403)
    assert not interruptor.interruptor_activo()


def test_ciclo_completo_con_auditoria(client, usuarios):
    user_id, h = _superadmin(usuarios)
    r = client.post(ACTIVAR, headers=h)
    assert (r.status_code, r.json()) == (200, {"activo": True, "cambio": True, "heredada": False})
    assert interruptor.interruptor_activo()
    visto = client.get(ESTADO, headers=h).json()
    assert visto["activo"] is True
    assert (visto["ultimo"]["accion"], visto["ultimo"]["user_id"]) == ("activar", user_id)
    assert client.post(ACTIVAR, headers=h).json() == {"activo": True, "cambio": False, "heredada": False}
    assert client.post(REANUDAR, headers=h).json() == {"activo": False, "cambio": True, "heredada": False}
    assert not interruptor.interruptor_activo()
    assert client.post(REANUDAR, headers=h).json() == {"activo": False, "cambio": False, "heredada": False}
    assert client.portal.call(_auditoria, user_id) == [("activar", user_id), ("reanudar", user_id)]


def test_el_estado_llega_a_cualquier_usuario_por_api_state(client, usuarios):
    _, h = _superadmin(usuarios)
    operador = cabeceras(client, "ks-estado-operador")
    assert client.get("/api/state", headers=operador).json()["kill_switch_active"] is False
    client.post(ACTIVAR, headers=h)
    assert client.get("/api/state", headers=operador).json()["kill_switch_active"] is True
    client.post(REANUDAR, headers=h)
    assert client.get("/api/state", headers=operador).json()["kill_switch_active"] is False


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_si_no_se_puede_escribir_da_503_y_no_audita(client, usuarios):
    user_id, h = _superadmin(usuarios)
    carpeta = interruptor.ruta_del_interruptor().parent
    carpeta.chmod(0o500)
    try:
        r = client.post(ACTIVAR, headers=h)
    finally:
        carpeta.chmod(0o700)
    assert (r.status_code, r.json()["detail"]) == (503, "kill_switch_no_escribible")
    assert client.portal.call(_auditoria, user_id) == []


def test_el_ultimo_cambio_usa_el_indice_sin_filesort(client, usuarios):
    user_id, _ = usuarios(role="superadmin")

    async def medir():
        await sql(
            "INSERT INTO kill_switch_audit (accion, user_id, at) "
            # tests/identidades.py::sql() pasa por cursor.execute(query, args), que en
            # aiomysql hace `query % args`: el "%" literal de "seq %% 2" (modulo) tiene
            # que escaparse como "%%" para no chocar con el placeholder "%s" del user_id.
            "SELECT IF(seq %% 2 = 0, 'activar', 'reanudar'), %s, UTC_TIMESTAMP(6) - INTERVAL seq SECOND "
            "FROM seq_1_to_300", (user_id,))
        await sql("ANALYZE TABLE kill_switch_audit", (), True)
        return await sql("EXPLAIN " + kill_switch.SQL_ULTIMO, (), True)

    plan = client.portal.call(medir)
    primera = plan[0]  # id, select_type, table, type, possible_keys, key, key_len, ref, rows, Extra
    assert primera[2] == "a"
    assert primera[5] == "idx_kill_switch_audit_at", plan
    assert "filesort" not in (primera[9] or ""), plan
    assert "temporary" not in (primera[9] or ""), plan


def test_api_state_frena_con_solo_la_ruta_heredada(client, monkeypatch, tmp_path):
    """Task H (2026-09-17): la ruta vieja del freno sigue frenando y /api/state
    lo informa aunque el archivo de JAX_KILL_SWITCH_PATH no exista."""
    vieja = tmp_path / "PAUSE"
    monkeypatch.setattr(interruptor, "RUTA_HEREDADA", vieja, raising=False)
    operador = cabeceras(client, "ks-heredada-operador")
    assert client.get("/api/state", headers=operador).json()["kill_switch_active"] is False
    vieja.write_text("")
    assert client.get("/api/state", headers=operador).json()["kill_switch_active"] is True
    assert not interruptor.ruta_del_interruptor().exists()
