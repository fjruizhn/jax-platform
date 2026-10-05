"""Las tareas de fondo del motor (_poll_las_manos, _poll_pipelines) no arrancan bajo
pytest (2026-10-04, auditoría del PR #193). El fixture `client` levanta el lifespan
entero; _poll_las_manos pedía /health cada 30 s con el http_client GLOBAL, y un test
que lo reemplaza (test_sync_provider_models_gemini_pagina_...) contaba esa 3a llamada
de forma intermitente. Mismo contrato que start_reintento_de_uso y start_facet_canary.
"""
import asyncio
import logging

from jax_engine.state import JAXEngineState


def _estado_con_polls_falsos(llamadas):
    estado = JAXEngineState()

    async def poll_manos():
        llamadas.append("manos")

    async def poll_pipelines():
        llamadas.append("pipelines")

    estado._poll_las_manos = poll_manos
    estado._poll_pipelines = poll_pipelines
    return estado


async def test_no_arrancan_bajo_pytest(caplog):
    llamadas = []
    estado = _estado_con_polls_falsos(llamadas)
    with caplog.at_level(logging.WARNING):
        estado.start_background_tasks()
    await asyncio.sleep(0.05)
    assert llamadas == []
    assert "pytest" in caplog.text


async def test_forzado_arranca_las_dos():
    llamadas = []
    estado = _estado_con_polls_falsos(llamadas)
    estado.start_background_tasks(forzado=True)
    await asyncio.sleep(0.05)
    assert sorted(llamadas) == ["manos", "pipelines"]
