import json
import os
import time

from jax_engine.owner_cleanup import reap_orphaned_command_owner_files


def test_reaps_a_command_owner_file_only_when_both_mission_and_result_are_gone(tmp_path):
    task_id = "aaaa"
    (tmp_path / f"web-task-{task_id}_owner.json").write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    # mission y result todavía existen -> no se debe reapear
    (tmp_path / f"web-task-{task_id}.md").write_text("mission")
    (tmp_path / f"web-task-{task_id}_result.md").write_text("result")

    reaped = reap_orphaned_command_owner_files(tmp_path)

    assert reaped == 0
    assert (tmp_path / f"web-task-{task_id}_owner.json").exists()


def test_reaps_a_command_owner_file_once_mission_and_result_are_both_gone(tmp_path):
    task_id = "bbbb"
    (tmp_path / f"web-task-{task_id}_owner.json").write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    # ni mission ni result existen (ya limpiados por el script externo)

    reaped = reap_orphaned_command_owner_files(tmp_path)

    assert reaped == 1
    assert not (tmp_path / f"web-task-{task_id}_owner.json").exists()


def test_does_not_reap_if_only_the_result_survives(tmp_path):
    task_id = "cccc"
    (tmp_path / f"web-task-{task_id}_owner.json").write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    (tmp_path / f"web-task-{task_id}_result.md").write_text("result")

    reaped = reap_orphaned_command_owner_files(tmp_path)

    assert reaped == 0
    assert (tmp_path / f"web-task-{task_id}_owner.json").exists()


def test_reaps_a_command_owner_file_past_the_age_cutoff_even_if_siblings_survive(tmp_path):
    # el script externo de retención no tiene scheduler propio hoy -- este
    # fallback por edad hace que el reaper no dependa de que corra.
    task_id = "dddd"
    owner_file = tmp_path / f"web-task-{task_id}_owner.json"
    owner_file.write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    old_time = time.time() - 3600
    os.utime(owner_file, (old_time, old_time))
    (tmp_path / f"web-task-{task_id}_result.md").write_text("result")

    reaped = reap_orphaned_command_owner_files(tmp_path, max_age_seconds=1800)

    assert reaped == 1
    assert not owner_file.exists()


# Ronda 5 (2026-08-20, T1): reap_old_pipeline_owner_files() se eliminó --
# el ownership de pipelines ya no vive en sidecar files, ver
# test_pipeline_ownership.py y api/pipelines.py.


def test_reap_functions_are_no_ops_on_a_missing_directory(tmp_path):
    missing = tmp_path / "does-not-exist"

    assert reap_orphaned_command_owner_files(missing) == 0
