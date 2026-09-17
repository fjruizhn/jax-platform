import json
import logging
import os
import time

import ajustes
from jax_engine import owner_cleanup
from jax_engine.owner_cleanup import reap_orphaned_command_owner_files


def test_reaps_a_command_owner_file_only_when_both_mission_and_result_are_gone(tmp_path):
    task_id = "aaaa"
    (tmp_path / f"web-task-{task_id}_owner.json").write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    # mission y result todavía existen -> no se debe reapear
    (tmp_path / f"web-task-{task_id}.md").write_text("mission")
    (tmp_path / f"web-task-{task_id}_result.md").write_text("result")

    reaped = reap_orphaned_command_owner_files(tmp_path, max_age_seconds=30 * 24 * 3600)

    assert reaped == 0
    assert (tmp_path / f"web-task-{task_id}_owner.json").exists()


def test_reaps_a_command_owner_file_once_mission_and_result_are_both_gone(tmp_path):
    task_id = "bbbb"
    (tmp_path / f"web-task-{task_id}_owner.json").write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    # ni mission ni result existen (ya limpiados en un ciclo anterior)

    reaped = reap_orphaned_command_owner_files(tmp_path, max_age_seconds=30 * 24 * 3600)

    assert reaped == 1
    assert not (tmp_path / f"web-task-{task_id}_owner.json").exists()


def test_does_not_reap_if_only_the_result_survives(tmp_path):
    task_id = "cccc"
    (tmp_path / f"web-task-{task_id}_owner.json").write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    (tmp_path / f"web-task-{task_id}_result.md").write_text("result")

    reaped = reap_orphaned_command_owner_files(tmp_path, max_age_seconds=30 * 24 * 3600)

    assert reaped == 0
    assert (tmp_path / f"web-task-{task_id}_owner.json").exists()


def test_reaps_a_command_owner_file_past_the_age_cutoff_and_takes_the_surviving_sibling_with_it(tmp_path):
    # Ruling R4 (2026-09-16): al vencer la retención se borra lo que quede
    # de la misión, el resultado y el dueño -- una sola regla. Ya no hay
    # caso donde el owner venza y un hermano sobreviva.
    task_id = "dddd"
    owner_file = tmp_path / f"web-task-{task_id}_owner.json"
    result_file = tmp_path / f"web-task-{task_id}_result.md"
    owner_file.write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    old_time = time.time() - 3600
    os.utime(owner_file, (old_time, old_time))
    result_file.write_text("result")

    reaped = reap_orphaned_command_owner_files(tmp_path, max_age_seconds=1800)

    assert reaped == 1
    assert not owner_file.exists()
    assert not result_file.exists()


# Ronda 5 (2026-08-20, T1): reap_old_pipeline_owner_files() se eliminó --
# el ownership de pipelines ya no vive en sidecar files, ver
# test_pipeline_ownership.py y api/pipelines.py.


def test_reap_functions_are_no_ops_on_a_missing_directory(tmp_path):
    missing = tmp_path / "does-not-exist"

    assert reap_orphaned_command_owner_files(missing, max_age_seconds=30 * 24 * 3600) == 0


# ---------------------------------------------------------------------------
# Ruling R4 (controlador, 2026-09-16): al vencer web_task_retention_days se
# borran misión, resultado y dueño JUNTOS -- una sola regla. Pruebas puras
# de reap_orphaned_command_owner_files, sin pasar por el ajuste.

def test_un_owner_viejo_con_los_dos_hermanos_se_lleva_los_tres_archivos(tmp_path):
    task_id = "vieja-completa"
    owner_file = tmp_path / f"web-task-{task_id}_owner.json"
    mission_file = tmp_path / f"web-task-{task_id}.md"
    result_file = tmp_path / f"web-task-{task_id}_result.md"
    owner_file.write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    mission_file.write_text("mission")
    result_file.write_text("result")
    old_time = time.time() - 3600
    os.utime(owner_file, (old_time, old_time))

    reaped = reap_orphaned_command_owner_files(tmp_path, max_age_seconds=1800)

    assert reaped == 1
    assert not owner_file.exists()
    assert not mission_file.exists()
    assert not result_file.exists()


def test_un_owner_viejo_con_un_solo_hermano_presente_se_lleva_los_dos(tmp_path):
    task_id = "vieja-un-hermano"
    owner_file = tmp_path / f"web-task-{task_id}_owner.json"
    mission_file = tmp_path / f"web-task-{task_id}.md"
    result_file = tmp_path / f"web-task-{task_id}_result.md"
    owner_file.write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    mission_file.write_text("mission")
    # no hay result file: el faltante no debe hacer fallar el borrado
    old_time = time.time() - 3600
    os.utime(owner_file, (old_time, old_time))

    reaped = reap_orphaned_command_owner_files(tmp_path, max_age_seconds=1800)

    assert reaped == 1
    assert not owner_file.exists()
    assert not mission_file.exists()
    assert not result_file.exists()


def test_una_tarea_reciente_no_se_toca(tmp_path):
    task_id = "reciente"
    owner_file = tmp_path / f"web-task-{task_id}_owner.json"
    mission_file = tmp_path / f"web-task-{task_id}.md"
    result_file = tmp_path / f"web-task-{task_id}_result.md"
    owner_file.write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    mission_file.write_text("mission")
    result_file.write_text("result")

    reaped = reap_orphaned_command_owner_files(tmp_path, max_age_seconds=30 * 24 * 3600)

    assert reaped == 0
    assert owner_file.exists()
    assert mission_file.exists()
    assert result_file.exists()


# ---------------------------------------------------------------------------
# Frente C (2026-09-16): la edad máxima sale de web_task_retention_days. Si el
# ajuste está ilegible, el ciclo NO borra nada (fail-closed: sin saber cuánto
# se retiene no se destruye) y lo deja en el log como ERROR.

def _owner_con_hermanos(carpeta, task_id, dias_de_edad):
    owner = carpeta / f"web-task-{task_id}_owner.json"
    owner.write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    (carpeta / f"web-task-{task_id}.md").write_text("mission")
    (carpeta / f"web-task-{task_id}_result.md").write_text("result")
    viejo = time.time() - dias_de_edad * 24 * 3600
    os.utime(owner, (viejo, viejo))
    return owner


async def test_el_ciclo_usa_los_dias_del_ajuste(tmp_path, monkeypatch):
    async def valor(clave):
        assert clave == ajustes.RETENCION
        return 2
    monkeypatch.setattr(ajustes, "valor", valor)
    viejo = _owner_con_hermanos(tmp_path, "viejo", 3)
    reciente = _owner_con_hermanos(tmp_path, "reciente", 1)
    assert await owner_cleanup.ciclo_de_limpieza(tmp_path) == 1
    assert not viejo.exists() and reciente.exists()
    # Ruling R4: mission y result de la tarea vieja se van con el owner; los
    # de la tarea reciente quedan intactos.
    assert not (tmp_path / "web-task-viejo.md").exists()
    assert not (tmp_path / "web-task-viejo_result.md").exists()
    assert (tmp_path / "web-task-reciente.md").exists()
    assert (tmp_path / "web-task-reciente_result.md").exists()


async def test_con_el_ajuste_ilegible_no_borra_nada_y_lo_dice(tmp_path, monkeypatch, caplog):
    async def valor(clave):
        raise ajustes.AjusteIlegible(clave, "invalido")
    monkeypatch.setattr(ajustes, "valor", valor)
    viejo = _owner_con_hermanos(tmp_path, "viejo", 400)
    with caplog.at_level(logging.ERROR, logger="jax_engine.owner_cleanup"):
        assert await owner_cleanup.ciclo_de_limpieza(tmp_path) is None
    assert viejo.exists()
    assert (tmp_path / "web-task-viejo.md").exists()
    assert (tmp_path / "web-task-viejo_result.md").exists()
    assert "web_task_retention_days" in caplog.text
