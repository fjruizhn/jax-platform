"""cancel_pipeline() already released a tenant's concurrency slot
(resource_manager.release_pipeline) on explicit cancel, but a pipeline that
finishes on its own (completed/failed, detected by the poller) never did --
every non-cancelled pipeline permanently consumed one of the tenant's
concurrent slots (max_pipelines, ajustes.py -- era una constante hasta el frente C).
"""
import pytest

from jax_engine.resource_manager import resource_manager
from jax_engine.schemas import PipelineState
from jax_engine.state import JAXEngineState

TENANT_ID = "release-test-tenant"
USER_ID = "release-test-user"


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def get(self, url, *args, **kwargs):
        return self._response


def _make_state_with_pipeline(pid: str) -> JAXEngineState:
    state = JAXEngineState()
    state._state.active_pipelines[pid] = PipelineState(
        pipeline_id=pid, tenant_id=TENANT_ID, user_id=USER_ID,
        name="Test Pipeline", status="running",
    )
    return state


async def test_poll_one_pipeline_releases_the_resource_slot_on_natural_completion():
    pid = "pid-release-1"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    await resource_manager.admit_pipeline(TENANT_ID, pid)

    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "completed"}, "steps": []}))
    await state._poll_one_pipeline(client, pid, pipeline)

    assert pid not in state._state.active_pipelines
    assert await resource_manager.active_count(TENANT_ID) == 0


async def test_poll_one_pipeline_releases_the_resource_slot_on_failure():
    pid = "pid-release-2"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    await resource_manager.admit_pipeline(TENANT_ID, pid)

    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "failed"}, "steps": []}))
    await state._poll_one_pipeline(client, pid, pipeline)

    assert await resource_manager.active_count(TENANT_ID) == 0


# Importante B (revisión final, 2026-09-18): _JACOBS_STATUS_MAP no tenía la
# clave "expired" -- .get(jacobs_status, "running") lo dejaba mapeado a
# "running" para siempre. Un pipeline cosechado por jacobs/reaper.py (T4,
# PipelineStatus.expired) nunca entraba al `if updated.status in
# ("completed", "failed")`: remove_pipeline() no corría, el cupo del tenant
# quedaba fugado para siempre, y encolar_aviso_fin_pipeline() nunca se
# llamaba -- ni correo ni Telegram para el pipeline que murió solo, que es
# justo el caso que nadie mira.
async def test_poll_one_pipeline_releases_the_resource_slot_on_expired():
    pid = "pid-release-3"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    await resource_manager.admit_pipeline(TENANT_ID, pid)

    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "expired"}, "steps": []}))
    await state._poll_one_pipeline(client, pid, pipeline)

    assert pid not in state._state.active_pipelines
    assert await resource_manager.active_count(TENANT_ID) == 0


# Bloqueante 2 (revisión final, 2026-09-18): encolar_aviso_fin_pipeline()
# vivía ANTES de remove_pipeline()/release_pipeline(), los tres adentro del
# mismo `except Exception: pass` de más abajo -- cuyo comentario decía "Sin
# try propio: release_pipeline no puede lanzar" (cierto de release_pipeline
# solamente). Si encolar_aviso_fin_pipeline() lanzaba (asyncio.create_task
# sin loop corriendo, o cualquier excepción de _TAREAS_EN_VUELO.add), el
# except se la tragaba ANTES de liberar el cupo -- exactamente el defecto
# que ese bloque ya había arreglado una vez (Task 3, 2026-09-15).
async def test_poll_one_pipeline_libera_el_cupo_aunque_el_aviso_explote(monkeypatch):
    import aviso_pipeline

    def _explota(*a, **k):
        raise RuntimeError("simulado: create_task sin loop / MemoryError en el add")

    monkeypatch.setattr(aviso_pipeline, "encolar_aviso_fin_pipeline", _explota)

    pid = "pid-release-5"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    await resource_manager.admit_pipeline(TENANT_ID, pid)

    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "completed"}, "steps": []}))
    await state._poll_one_pipeline(client, pid, pipeline)

    assert pid not in state._state.active_pipelines
    assert await resource_manager.active_count(TENANT_ID) == 0


async def test_poll_one_pipeline_dispara_el_aviso_para_expired_igual_que_completed_y_failed(monkeypatch):
    import aviso_pipeline

    llamados = []
    monkeypatch.setattr(
        aviso_pipeline, "encolar_aviso_fin_pipeline",
        lambda pid, tenant_id, user_id, status, nombre: llamados.append(status),
    )

    pid = "pid-release-4"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    await resource_manager.admit_pipeline(TENANT_ID, pid)

    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "expired"}, "steps": []}))
    await state._poll_one_pipeline(client, pid, pipeline)

    assert llamados == ["failed"]  # expired se mapea a failed para el poller local (mismo trato que aborted)


# Ronda `feat/estado-disputed` (2026-09-18): jax agrega `disputed` -- el
# árbitro agotó el tope de devoluciones con una objeción SIN RESOLVER
# (jacobs/devolucion.py, rama feat/arbitro-devuelve). Terminal, no ocupa
# cupo, y DISTINTO de completed/failed genérico: por eso NO se mapea a
# "failed" como aborted/expired -- tiene su propio status "disputed" en el
# panel (jax_engine/schemas.py::PipelineStatus), así el Historial y el
# correo lo pueden mostrar como lo que es, no como un fallo cualquiera. El
# defecto que este test evita es el MISMO que _JACOBS_STATUS_MAP ya tuvo con
# `expired`: sin la entrada, .get(jacobs_status, "running") deja el pipeline
# pegado en "running" para siempre.
async def test_poll_one_pipeline_releases_the_resource_slot_on_disputed():
    pid = "pid-release-6"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    await resource_manager.admit_pipeline(TENANT_ID, pid)

    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "disputed"}, "steps": []}))
    await state._poll_one_pipeline(client, pid, pipeline)

    assert pid not in state._state.active_pipelines
    assert await resource_manager.active_count(TENANT_ID) == 0


async def test_poll_one_pipeline_dispara_el_aviso_para_disputed_con_su_propio_status(monkeypatch):
    import aviso_pipeline

    llamados = []
    monkeypatch.setattr(
        aviso_pipeline, "encolar_aviso_fin_pipeline",
        lambda pid, tenant_id, user_id, status, nombre: llamados.append(status),
    )

    pid = "pid-release-7"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    await resource_manager.admit_pipeline(TENANT_ID, pid)

    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "disputed"}, "steps": []}))
    await state._poll_one_pipeline(client, pid, pipeline)

    # "disputed", NO "failed": aviso_pipeline._asunto/_enviar_aviso necesitan
    # el status crudo para escribir "objeción sin resolver" en vez de un
    # correo genérico de fallo (ver test_aviso_pipeline.py).
    assert llamados == ["disputed"]


# Task 4 (2026-09-22, descartar-pipelines, Ruling 5 del controlador): `jax`
# agrega DOS estados terminales nuevos, `discarded` y `hidden` -- MISMO
# defecto exacto que `expired` y `disputed` tuvieron: sin entrada en
# _JACOBS_STATUS_MAP, .get(jacobs_status, "running") deja el pipeline pegado
# en "running" para siempre. El escenario real: el dueño descarta un
# `aborted` ANTES de que el poller (cada 5 s) haya procesado su transición a
# terminal -- el pipeline sigue en `active_pipelines`, y el próximo poll
# devuelve `discarded` (o, más tarde, `hidden` si el superadmin lo oculta
# mientras sigue ahí). Sin la entrada, el cupo del tenant queda fugado para
# siempre, igual que le pasó a `expired` hasta el 2026-09-18.
@pytest.mark.parametrize("status_crudo", ["discarded", "hidden"])
async def test_poll_one_pipeline_releases_the_resource_slot_on_discard_o_hide(status_crudo):
    pid = f"pid-release-{status_crudo}"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    await resource_manager.admit_pipeline(TENANT_ID, pid)

    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": status_crudo}, "steps": []}))
    await state._poll_one_pipeline(client, pid, pipeline)

    assert pid not in state._state.active_pipelines
    assert await resource_manager.active_count(TENANT_ID) == 0


# Fix round 1, Ruling 12 (2026-09-22): discarded/hidden son un pedido
# EXPLÍCITO del propio usuario (o del superadmin) -- a diferencia de
# completed/failed/disputed/expired, que el pipeline alcanza SOLO. Un aviso
# "tu pipeline terminó" ahí es ruido: quien lo pidió ya lo sabe. Sin este
# filtro, aviso_pipeline._asunto/_enviar_aviso (sin rama para estos dos
# estados) mandarían el texto genérico de "terminó", que además es
# ENGAÑOSO -- lee como si el pipeline hubiera fallado o completado solo.
@pytest.mark.parametrize("status_crudo", ["discarded", "hidden"])
async def test_poll_one_pipeline_no_dispara_aviso_para_discard_o_hide(monkeypatch, status_crudo):
    import aviso_pipeline

    llamados = []
    monkeypatch.setattr(
        aviso_pipeline, "encolar_aviso_fin_pipeline",
        lambda pid, tenant_id, user_id, status, nombre: llamados.append(status),
    )

    pid = f"pid-sin-aviso-{status_crudo}"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    await resource_manager.admit_pipeline(TENANT_ID, pid)

    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": status_crudo}, "steps": []}))
    await state._poll_one_pipeline(client, pid, pipeline)

    # El cupo se libera igual (Ruling 5); lo único que cambia es que NO se
    # llama a encolar_aviso_fin_pipeline.
    assert pid not in state._state.active_pipelines
    assert await resource_manager.active_count(TENANT_ID) == 0
    assert llamados == []
