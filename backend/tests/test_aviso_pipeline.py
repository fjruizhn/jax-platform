"""Task 8 (2026-09-18): el correo al terminar un pipeline.

Fernando lanza un pipeline, se va, y hoy no se entera cuando termina --
_poll_one_pipeline detecta la transición a completed/failed pero nadie fuera
de la sesión WS/SSE se entera. aviso_pipeline.py es el correo. Copia el
patrón de auth.py::_procesar_recuperacion (BackgroundTasks + asyncio.to_thread)
pero el poller NO es un request, así que no hay BackgroundTasks a mano:
encolar_aviso_fin_pipeline dispara su propia Task y suelta el control de
inmediato -- eso es lo que test_encolar_no_bloquea_al_llamador prueba.

Tres garantías bajo test:
  1. el envío NUNCA ocurre dentro del tick del poller (no bloquea);
  2. el correo no sale dos veces para el mismo pipeline_id (dedup en DB,
     pipeline_aviso_enviado, INSERT IGNORE);
  3. un aviso que falla no lanza y queda logueado, nunca en silencio.

Los tests DB-backed piden `client` (conftest.py) y corren en su loop via
`client.portal.call(...)` -- el pool de conexiones es un singleton atado al
loop que arrancó `client` (mismo patrón que test_pipeline_ownership.py).
"""
import asyncio

import pytest

import aviso_pipeline
import smtp_config
from db.connection import get_pool

# La implementación REAL, capturada ANTES de que el autouse
# `_aviso_pipeline_no_dispara_solo` (conftest.py) la reemplace por un no-op
# para todos los demás tests del repo. Los tests de este archivo que
# necesitan el comportamiento real la reponen con
# `monkeypatch.setattr(aviso_pipeline, "encolar_aviso_fin_pipeline", _ENCOLAR_REAL)`.
_ENCOLAR_REAL = aviso_pipeline.encolar_aviso_fin_pipeline


async def _borrar_reclamo(pid):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM pipeline_aviso_enviado WHERE pipeline_id = %s", (pid,))
        await conn.commit()


@pytest.fixture
def reclamo_limpio(client):
    """Borra la fila de dedup que el test haya reclamado, sin importar el
    pipeline_id exacto: se registra acá y se borra al terminar."""
    pids = []

    def usar(pid):
        pids.append(pid)
        return pid

    yield usar
    for pid in pids:
        client.portal.call(_borrar_reclamo, pid)


def _settings_de_prueba():
    return smtp_config.SmtpSettings(
        host="mail.example.test", port=587, encryption="tls",
        user="no-reply@example.test", password="clave-de-prueba",
        from_name="Axioma", from_email="no-reply@example.test",
    )


# ------------------------------------------------------- no bloquea el poller

async def test_encolar_no_bloquea_al_llamador(monkeypatch):
    """encolar_aviso_fin_pipeline es SÍNCRONA y devuelve el control ANTES de
    que el envío corra -- si no, un SMTP lento congelaría el tick del poller
    para TODAS las pipelines activas, no solo la que terminó."""
    empezo = asyncio.Event()
    puede_seguir = asyncio.Event()

    async def _procesar_lento(pid, tenant_id, user_id, status, nombre):
        empezo.set()
        await puede_seguir.wait()

    monkeypatch.setattr(aviso_pipeline, "_procesar_aviso", _procesar_lento)
    # repone la implementación real: el autouse _aviso_pipeline_no_dispara_solo
    # (conftest.py) ya la reemplazó por un no-op para todos los tests.
    monkeypatch.setattr(aviso_pipeline, "encolar_aviso_fin_pipeline", _ENCOLAR_REAL)

    aviso_pipeline.encolar_aviso_fin_pipeline("pid-no-bloquea", "t1", "1", "completed", "Demo")

    # encolar_aviso_fin_pipeline ya devolvió el control acá arriba: si fuera
    # bloqueante, _procesar_lento ya habría corrido hasta el await y "empezo"
    # ya estaría seteado ANTES incluso de que el test pueda comprobarlo con
    # un timeout corto -- lo que se prueba es que hace falta CEDER el loop
    # (el await de abajo) para que la Task arranque.
    assert not empezo.is_set()

    await asyncio.wait_for(empezo.wait(), timeout=1)
    puede_seguir.set()
    # drena la Task para no dejarla pendiente al cerrar el loop de sesión
    for tarea in list(aviso_pipeline._TAREAS_EN_VUELO):
        await tarea


# ------------------------------------------------------- se llama desde el poller

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


def _make_state_with_pipeline(pid, tenant_id="tenant-aviso", user_id="user-aviso", status="running"):
    from jax_engine.schemas import PipelineState
    from jax_engine.state import JAXEngineState

    state = JAXEngineState()
    state._state.active_pipelines[pid] = PipelineState(
        pipeline_id=pid, tenant_id=tenant_id, user_id=user_id,
        name="Pipeline de prueba", status=status,
    )
    return state


async def test_poll_one_pipeline_encola_el_aviso_al_completar(monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        aviso_pipeline, "encolar_aviso_fin_pipeline",
        lambda pid, tenant_id, user_id, status, nombre: llamadas.append((pid, tenant_id, user_id, status, nombre)),
    )

    pid = "pid-completa"
    state = _make_state_with_pipeline(pid, tenant_id="t-aviso", user_id="u-aviso")
    pipeline = state._state.active_pipelines[pid]
    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "completed"}, "steps": []}))

    await state._poll_one_pipeline(client, pid, pipeline)

    assert llamadas == [(pid, "t-aviso", "u-aviso", "completed", "Pipeline de prueba")]


async def test_poll_one_pipeline_encola_el_aviso_al_fallar(monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        aviso_pipeline, "encolar_aviso_fin_pipeline",
        lambda pid, tenant_id, user_id, status, nombre: llamadas.append(status),
    )

    pid = "pid-falla"
    state = _make_state_with_pipeline(pid)
    pipeline = state._state.active_pipelines[pid]
    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "failed"}, "steps": []}))

    await state._poll_one_pipeline(client, pid, pipeline)

    assert llamadas == ["failed"]


async def test_poll_one_pipeline_no_avisa_en_una_transicion_que_no_es_fin(monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        aviso_pipeline, "encolar_aviso_fin_pipeline",
        lambda *a, **k: llamadas.append(a),
    )

    pid = "pid-gate"
    state = _make_state_with_pipeline(pid, status="running")
    pipeline = state._state.active_pipelines[pid]
    client = _FakeClient(_FakeResponse(200, {"pipeline": {"status": "interrupted"}, "steps": []}))

    await state._poll_one_pipeline(client, pid, pipeline)

    assert llamadas == []


# ------------------------------------------------------- dedup (DB real)

def test_reclamar_gana_una_sola_vez(client, reclamo_limpio):
    pid = reclamo_limpio("pid-dedup-1")

    primero = client.portal.call(aviso_pipeline._reclamar, pid)
    segundo = client.portal.call(aviso_pipeline._reclamar, pid)

    assert primero is True
    assert segundo is False


# ------------------------------------------------------- destinatario = dueño real

def test_correo_del_dueno_usuario_activo(client, usuarios):
    user_id, email = usuarios(status="active")

    resuelto = client.portal.call(aviso_pipeline._correo_del_dueno, str(user_id))

    assert resuelto == email


def test_correo_del_dueno_usuario_inactivo_no_manda(client, usuarios):
    user_id, _email = usuarios(status="inactive")

    resuelto = client.portal.call(aviso_pipeline._correo_del_dueno, str(user_id))

    assert resuelto is None


def test_correo_del_dueno_usuario_inexistente_no_manda(client):
    resuelto = client.portal.call(aviso_pipeline._correo_del_dueno, "999999999")

    assert resuelto is None


def test_correo_del_dueno_user_id_no_numerico_no_lanza(client):
    """El poller de test usa placeholders no numéricos ("release-test-user",
    etc.) -- _correo_del_dueno tiene que devolver None, nunca un ValueError
    sin atrapar que tumbe la Task de fondo."""
    resuelto = client.portal.call(aviso_pipeline._correo_del_dueno, "no-es-un-id")

    assert resuelto is None


# ------------------------------------------------------- extremo a extremo (sin red real)

def test_procesar_aviso_manda_una_sola_vez_al_correo_del_dueno(client, usuarios, monkeypatch, reclamo_limpio):
    user_id, email = usuarios(status="active")
    pid = reclamo_limpio("pid-e2e-1")

    async def cargar_ok():
        return _settings_de_prueba()

    enviados = []
    monkeypatch.setattr(smtp_config, "cargar_settings", cargar_ok)
    monkeypatch.setattr(
        aviso_pipeline, "_enviar_aviso",
        lambda settings, to_email, pid_, status, nombre: enviados.append((to_email, pid_, status, nombre)),
    )

    client.portal.call(aviso_pipeline._procesar_aviso, pid, "t1", str(user_id), "completed", "Mi Pipeline")
    client.portal.call(aviso_pipeline._procesar_aviso, pid, "t1", str(user_id), "completed", "Mi Pipeline")

    assert enviados == [(email, pid, "completed", "Mi Pipeline")]


def test_procesar_aviso_sin_smtp_configurado_no_manda_y_no_lanza(client, usuarios, monkeypatch,
                                                                  reclamo_limpio, caplog):
    user_id, _email = usuarios(status="active")
    pid = reclamo_limpio("pid-e2e-2")

    async def sin_configurar():
        raise smtp_config.SmtpNoConfigurado()

    llamado = []
    monkeypatch.setattr(smtp_config, "cargar_settings", sin_configurar)
    monkeypatch.setattr(aviso_pipeline, "_enviar_aviso", lambda *a, **k: llamado.append(True))

    with caplog.at_level("ERROR", logger="aviso_pipeline"):
        client.portal.call(aviso_pipeline._procesar_aviso, pid, "t1", str(user_id), "completed", "Mi Pipeline")

    assert llamado == []
    assert "correo deshabilitado" in caplog.text


def test_procesar_aviso_con_envio_que_falla_no_lanza_y_queda_logueado(client, usuarios, monkeypatch,
                                                                       reclamo_limpio, caplog):
    user_id, _email = usuarios(status="active")
    pid = reclamo_limpio("pid-e2e-3")

    async def cargar_ok():
        return _settings_de_prueba()

    def revienta(settings, to_email, pid_, status, nombre):
        raise OSError("host inválido de prueba")

    monkeypatch.setattr(smtp_config, "cargar_settings", cargar_ok)
    monkeypatch.setattr(aviso_pipeline, "_enviar_aviso", revienta)

    with caplog.at_level("ERROR", logger="aviso_pipeline"):
        # No debe lanzar -- si lanzara, esta llamada (síncrona, en el loop
        # del portal, no una Task de fondo) haría fallar el test acá mismo.
        client.portal.call(aviso_pipeline._procesar_aviso, pid, "t1", str(user_id), "completed", "Mi Pipeline")

    assert "falló el envío del aviso" in caplog.text


# ------------------------------------------------------- contenido del correo

def test_enlace_detalle_sin_url_hardcodeada(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://ejemplo-de-prueba.test")
    monkeypatch.setenv("PIPELINE_DETAIL_PATH", "/mi-ruta/{pipeline_id}")

    assert aviso_pipeline._enlace_detalle("pid-123") == "https://ejemplo-de-prueba.test/mi-ruta/pid-123"


def test_asunto_distingue_completado_de_fallido():
    assert aviso_pipeline._asunto("completed") != aviso_pipeline._asunto("failed")
