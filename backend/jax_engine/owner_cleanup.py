"""Periodic reaper for the ownership sidecar files api/command.py writes
(web-task-{id}_owner.json), together with the mission and result files
that belong to the same web task. Had no cleanup, so the directory grew
forever, one small file per task ever created.

Rule (decisión de Fernando, frente C, 2026-09-16): mission, result and
owner are retained together for web_task_retention_days (ajustes.py) and
deleted together once that age is reached -- mission and result first,
owner last, so a crash in between leaves an owner with no siblings, which
the next cycle removes on its own. One rule, visible in the admin screen.
This replaces the old design (an external retention script,
~/jax/scripts/cleanup.sh, pruned mission/result and this reaper only
followed by deleting an orphaned owner, plus its own age fallback since
that script had no scheduler) -- the age cutoff above is a self-sufficient
rule now: there is no external script to stay in sync with.

Pipelines: ronda 5 (2026-08-20, T1) movió el ownership de un sidecar file
(pipelines_dir/{id}_owner.json) a la columna owner_ack_at en
jacobs_pipelines (DB compartida con Jacobs) -- ver api/pipelines.py. Ya no
hay archivo que este reaper limpie para pipelines; la funcion
reap_old_pipeline_owner_files() y su uso abajo se eliminaron con el mismo
commit que migro el escritor/lector.
"""
import asyncio
import logging
import time
from pathlib import Path

import ajustes

logger = logging.getLogger(__name__)

SEGUNDOS_POR_DIA = 24 * 3600
CLEANUP_INTERVAL_SECONDS = 6 * 3600  # cada 6 horas


def reap_orphaned_command_owner_files(missions_dir: Path, max_age_seconds: float) -> int:
    if not missions_dir.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    reaped = 0
    for owner_file in missions_dir.glob("web-task-*_owner.json"):
        task_id = owner_file.name[len("web-task-"):-len("_owner.json")]
        mission_file = missions_dir / f"web-task-{task_id}.md"
        result_file = missions_dir / f"web-task-{task_id}_result.md"
        try:
            too_old = owner_file.stat().st_mtime < cutoff
            siblings_gone = not mission_file.exists() and not result_file.exists()
            if too_old or siblings_gone:
                if too_old:
                    # Ruling R4: al vencer la retención se borran misión y
                    # resultado -- lo que quede de ellos -- y el owner al
                    # final, para que una carrera a mitad de camino deje un
                    # owner sin hermanos que el próximo ciclo termine de
                    # limpiar por la rama de siblings_gone.
                    mission_file.unlink(missing_ok=True)
                    result_file.unlink(missing_ok=True)
                owner_file.unlink()
                reaped += 1
        except OSError:  # fail-soft: unlink en reaper con carrera TOCTOU benigna (el archivo ya pudo desaparecer); el proximo ciclo reintenta
            pass
    return reaped


async def ciclo_de_limpieza(missions_dir: Path) -> int | None:
    """Un ciclo con la retención VIGENTE (web_task_retention_days: días que
    el dueño de una tarea web puede seguir viendo su resultado -- sin owner
    file, GET /api/command/{id} le da 404). Ajuste ilegible: no borra nada."""
    try:
        dias = await ajustes.valor(ajustes.RETENCION)
    except ajustes.AjusteIlegible as exc:
        logger.error("limpieza de owner files omitida: el ajuste %s está ilegible (%s); "
                     "no se borra nada hasta corregirlo", exc.clave, exc.motivo)
        return None
    return await asyncio.to_thread(reap_orphaned_command_owner_files, missions_dir, dias * SEGUNDOS_POR_DIA)


async def start_owner_file_cleanup():
    # Import diferido: no hay ciclo real (jax_engine.state sólo importa
    # .schemas/.events/.resource_manager/http_client), pero mantiene este
    # módulo sin depender de que api.command ya esté cargado en el momento
    # en que jax_engine.owner_cleanup se importa.
    from api.command import MISSIONS_DIR

    while True:
        try:
            await ciclo_de_limpieza(MISSIONS_DIR)
        except Exception:  # fail-soft: loop de limpieza en background, reintenta cada intervalo; documentado como best-effort explicito
            pass  # best-effort: nunca debe tumbar el proceso
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
