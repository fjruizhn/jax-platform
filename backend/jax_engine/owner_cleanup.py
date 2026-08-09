"""Periodic reaper for the ownership sidecar files api/command.py and
api/pipelines.py write (web-task-{id}_owner.json / {pipeline_id}_owner.json).
Neither had any cleanup, so both directories grew forever, one small file
per task/pipeline ever created.

Commands: reaped as soon as BOTH the mission and result files are gone (an
external retention script, ~/jax/scripts/cleanup.sh, prunes those -- when
it runs, this stays in sync with it instead of guessing a competing TTL).
That script has no scheduler of its own today, so relying on it alone would
make this reaper a no-op in practice -- COMMAND_OWNER_MAX_AGE_SECONDS is a
self-sufficient fallback: an owner file is also reaped once it's simply old
enough, regardless of whether its mission/result siblings were ever cleaned.

Pipelines: there is no local mission/result file to key off (results live
in the separate LAS MANOS/Jacobs service) -- so pipeline owner files are
reaped by age only.
"""
import asyncio
import time
from pathlib import Path

COMMAND_OWNER_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 días
PIPELINE_OWNER_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 días
CLEANUP_INTERVAL_SECONDS = 6 * 3600  # cada 6 horas


def reap_orphaned_command_owner_files(missions_dir: Path, max_age_seconds: float = COMMAND_OWNER_MAX_AGE_SECONDS) -> int:
    if not missions_dir.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    reaped = 0
    for owner_file in missions_dir.glob("web-task-*_owner.json"):
        task_id = owner_file.name[len("web-task-"):-len("_owner.json")]
        mission_file = missions_dir / f"web-task-{task_id}.md"
        result_file = missions_dir / f"web-task-{task_id}_result.md"
        try:
            siblings_gone = not mission_file.exists() and not result_file.exists()
            too_old = owner_file.stat().st_mtime < cutoff
            if siblings_gone or too_old:
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
    # Import diferido: no hay ciclo real (jax_engine.state sólo importa
    # .schemas/.events/.resource_manager/http_client), pero mantiene este
    # módulo sin depender de que api.command/api.pipelines ya estén
    # cargados en el momento en que jax_engine.owner_cleanup se importa.
    from api.command import MISSIONS_DIR
    from api.pipelines import PIPELINES_DIR

    while True:
        try:
            await asyncio.to_thread(reap_orphaned_command_owner_files, MISSIONS_DIR)
            await asyncio.to_thread(reap_old_pipeline_owner_files, PIPELINES_DIR)
        except Exception:
            pass  # best-effort: nunca debe tumbar el proceso
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
