"""El lifespan propio de main.py espera al despachador antes de cerrar HTTP."""
import asyncio
import types

import pytest


class _Nada:
    def __getattr__(self, _nombre):
        return self

    def __call__(self, *a, **k):
        return self

    def __await__(self):
        return iter(())


@pytest.mark.asyncio
async def test_lifespan_cancela_y_espera_despachador_antes_de_cerrar_http(monkeypatch):
    import main
    from api import chat
    from proyectos_documentos import despachador
    from webchat_f2d.repository import OutputOutboxRepository

    started = asyncio.Event()
    eventos = []
    tareas = []

    async def despachador_lento():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            eventos.append("despachador_cancelado")
            raise

    for nombre in main.lifespan.__wrapped__.__code__.co_names:
        if nombre in vars(main) and nombre not in {
            "asyncio", "logger", "despachador_de_documentos",
        }:
            monkeypatch.setattr(main, nombre, _Nada())
    monkeypatch.setattr(despachador, "start_despachador", despachador_lento)

    async def cerrar_http():
        eventos.append("http_cerrado")

    monkeypatch.setattr(main, "close_http_client", cerrar_http)
    monkeypatch.setattr(main, "close_pool", _Nada())
    monkeypatch.setattr(chat, "iniciar_cierre_por_inactividad", _iniciar_inactividad)
    monkeypatch.setattr(chat, "detener_cierre_por_inactividad", _detener_inactividad)
    monkeypatch.setattr(chat, "flush_open_conversations", _flush_vacio)

    async def snapshot_vacio(_self):
        return {}

    monkeypatch.setattr(OutputOutboxRepository, "recovery_snapshot", snapshot_vacio)

    def crear_tarea(coro):
        if coro.cr_code.co_name == "despachador_lento":
            tarea = asyncio.create_task(coro)
            tareas.append(tarea)
            return tarea
        coro.close()
        return None

    monkeypatch.setattr(main, "asyncio", types.SimpleNamespace(
        create_task=crear_tarea, to_thread=asyncio.to_thread, gather=asyncio.gather,
    ))

    async with main.lifespan(main.app):
        await asyncio.wait_for(started.wait(), 1)

    assert eventos == ["despachador_cancelado", "http_cerrado"]
    assert len(tareas) == 1 and tareas[0].done()


def test_logger_del_despachador_tiene_handler_de_journal_con_nombre():
    import logging
    import main  # configura los handlers de servicio

    logger = logging.getLogger("proyectos_documentos.despachador")
    handler = next(
        h for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and "proyectos_documentos.despachador" in h.formatter._fmt
    )
    assert handler.level == logging.WARNING
    assert logger.propagate


async def _iniciar_inactividad(_pool):
    return None


async def _detener_inactividad(_tarea):
    return None


async def _flush_vacio():
    return 0
