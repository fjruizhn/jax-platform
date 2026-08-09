"""Periodic reaper for the ownership sidecar files api/command.py and
api/pipelines.py write (web-task-{id}_owner.json / {pipeline_id}_owner.json).
Neither had any cleanup, so both directories grew forever, one small file
per task/pipeline ever created.

Commands: an external retention script (~/jax/scripts/cleanup.sh, outside
this repo) already prunes web-task-*.md / web-task-*_result.md, but knows
nothing about the newer _owner.json sidecars. Reaping those here, keyed off
"the mission AND result files are already gone", means this never competes
with that script's retention policy or risks deleting an owner file while
its result is still readable -- it only cleans up what's already orphaned.

Pipelines: there is no local mission/result file to key off (results live
in the separate LAS MANOS/Jacobs service) -- so pipeline owner files are
reaped by age instead.
"""
import asyncio
import time
from pathlib import Path

PIPELINE_OWNER_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 días
CLEANUP_INTERVAL_SECONDS = 6 * 3600  # cada 6 horas


def reap_orphaned_command_owner_files(missions_dir: Path) -> int:
    if not missions_dir.exists():
        return 0
    reaped = 0
    for owner_file in missions_dir.glob("web-task-*_owner.json"):
        task_id = owner_file.name[len("web-task-"):-len("_owner.json")]
        mission_file = missions_dir / f"web-task-{task_id}.md"
        result_file = missions_dir / f"web-task-{task_id}_result.md"
        if not mission_file.exists() and not result_file.exists():
            try:
                owner_file.unlink()
                reaped += 1
            except OSError:
                pass
    return reaped


def reap_old_pipeline_owner_files(pipelines_dir: Path, max_age_seconds: float = PIPELINE_OWNER_MAX_AGE_SECONDS) -> int:
    if not pipelines_dir.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    reaped = 0
    for owner_file in pipelines_dir.glob("*_owner.json"):
        try:
            if owner_file.stat().st_mtime < cutoff:
                owner_file.unlink()
                reaped += 1
        except OSError:
            pass
    return reaped


async def start_owner_file_cleanup():
    # Import acá adentro (no al nivel de módulo) para evitar el ciclo
    # api.command/api.pipelines -> jax_engine.state -> ... -> este módulo.
    from api.command import MISSIONS_DIR
    from api.pipelines import PIPELINES_DIR

    while True:
        try:
            reap_orphaned_command_owner_files(MISSIONS_DIR)
            reap_old_pipeline_owner_files(PIPELINES_DIR)
        except Exception:
            pass  # best-effort: nunca debe tumbar el proceso
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
