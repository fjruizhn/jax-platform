import json
import os
import time

from jax_engine.owner_cleanup import (
    reap_old_pipeline_owner_files,
    reap_orphaned_command_owner_files,
)


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


def test_reaps_a_pipeline_owner_file_older_than_the_cutoff(tmp_path):
    old_file = tmp_path / "pid-old_owner.json"
    old_file.write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    old_time = time.time() - 3600  # 1h en el pasado
    os.utime(old_file, (old_time, old_time))

    new_file = tmp_path / "pid-new_owner.json"
    new_file.write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))

    reaped = reap_old_pipeline_owner_files(tmp_path, max_age_seconds=1800)  # cutoff: 30min

    assert reaped == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_reap_functions_are_no_ops_on_a_missing_directory(tmp_path):
    missing = tmp_path / "does-not-exist"

    assert reap_orphaned_command_owner_files(missing) == 0
    assert reap_old_pipeline_owner_files(missing) == 0
