"""GET /api/command/{task_id} previously had no authorization check at all --
any authenticated user who learned another user's task_id (e.g. via the
frontend's jax_pending_cmds leak, or by reading it off a shared browser)
could read their full command result. Fixed by recording the owner
(tenant_id, user_id) to disk at creation time and checking it here.
"""
import json
import uuid

import pytest
from fastapi import HTTPException

from api.command import _owner_file, get_command_result
from auth.models import AuthUser


@pytest.fixture(autouse=True)
def _isolated_missions_dir(monkeypatch, tmp_path):
    # MISSIONS_DIR apunta por default a ~/jax/missions, el directorio real
    # que sirve el servicio en producción -- sin esto, cada corrida de este
    # archivo escribe result files reales ahí, y cuentan contra la ventana
    # de retención (10 más nuevos) del script de limpieza, pudiendo
    # desalojar resultados reales de usuarios.
    monkeypatch.setattr("api.command.MISSIONS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def owned_task(_isolated_missions_dir):
    task_id = str(uuid.uuid4())
    _owner_file(task_id).write_text(json.dumps({"tenant_id": "1", "user_id": "owner-user"}))
    result_file = _isolated_missions_dir / f"web-task-{task_id}_result.md"
    result_file.write_text("resultado secreto del dueño")
    return task_id


async def test_owner_can_read_their_own_result(owned_task):
    owner = AuthUser(user_id="owner-user", tenant_id="1", role="operator")

    result = await get_command_result(task_id=owned_task, user=owner)

    assert result == {"status": "completed", "result": "resultado secreto del dueño"}


async def test_owner_sees_running_while_result_not_yet_written(_isolated_missions_dir):
    # el caso normal de polling: la tarea existe (owner file) pero todavía
    # no terminó (sin result file) -- esto es lo que el frontend consulta
    # cada 5s durante toda la vida de un comando.
    task_id = str(uuid.uuid4())
    _owner_file(task_id).write_text(json.dumps({"tenant_id": "1", "user_id": "owner-user"}))
    owner = AuthUser(user_id="owner-user", tenant_id="1", role="operator")

    result = await get_command_result(task_id=task_id, user=owner)

    assert result == {"status": "running"}


async def test_different_user_cannot_read_someone_elses_result(owned_task):
    attacker = AuthUser(user_id="attacker", tenant_id="1", role="operator")

    with pytest.raises(HTTPException) as exc:
        await get_command_result(task_id=owned_task, user=attacker)

    assert exc.value.status_code == 404


async def test_same_user_id_different_tenant_cannot_read(owned_task):
    # user_id coincide pero tenant no -- también debe rechazarse
    cross_tenant = AuthUser(user_id="owner-user", tenant_id="2", role="operator")

    with pytest.raises(HTTPException) as exc:
        await get_command_result(task_id=owned_task, user=cross_tenant)

    assert exc.value.status_code == 404


async def test_task_with_no_owner_file_404s_even_if_result_exists(_isolated_missions_dir):
    # simula una tarea creada antes de este fix (o corrupta): resultado en
    # disco pero sin owner file -- debe fallar cerrado, no exponer el resultado.
    task_id = str(uuid.uuid4())
    result_file = _isolated_missions_dir / f"web-task-{task_id}_result.md"
    result_file.write_text("resultado huérfano, sin dueño registrado")
    user = AuthUser(user_id="anyone", tenant_id="1", role="operator")

    with pytest.raises(HTTPException) as exc:
        await get_command_result(task_id=task_id, user=user)

    assert exc.value.status_code == 404


async def test_owner_file_with_non_dict_json_fails_closed(_isolated_missions_dir):
    # un owner file corrupto/inesperado (ej. un array o string en vez de un
    # objeto) no debe volverse un 500 -- sigue siendo un 404 controlado.
    task_id = str(uuid.uuid4())
    _owner_file(task_id).write_text(json.dumps(["not", "a", "dict"]))
    user = AuthUser(user_id="anyone", tenant_id="1", role="operator")

    with pytest.raises(HTTPException) as exc:
        await get_command_result(task_id=task_id, user=user)

    assert exc.value.status_code == 404
