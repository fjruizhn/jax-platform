"""El kill switch de la Mesa (plan 2026-09-16-frente-b-kill-switch, Task 6).
Puro: la transacción y la difusión se sustituyen; el archivo es el REAL de
la ruta temporal que fija conftest.py. Corre en los dos jobs de CI."""
import asyncio
import json
import os
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

import interruptor
import kill_switch
from auth.models import AuthUser
from jax_engine.events import EventBus
from jax_engine.schemas import JAXEvent

ADMIN = AuthUser(user_id="7", tenant_id="1", role="superadmin")
OPERADOR = AuthUser(user_id="8", tenant_id="1", role="operator")
ES_ROOT = os.geteuid() == 0


class _Base:
    """Transacción de mentira con el mismo contrato que db.transaccion:
    confirma al salir, y ante una excepción no confirma."""

    def __init__(self):
        self.filas = []
        self.commits = 0
        self.falla_insert = None
        self.falla_commit = None

    @asynccontextmanager
    async def transaccion(self, aislamiento=None):
        assert aislamiento == "READ COMMITTED"
        base = self
        pendientes = []

        class _Cursor:
            async def execute(self, sql, args=()):
                if base.falla_insert is not None:
                    raise base.falla_insert
                pendientes.append(args)

        yield _Cursor()
        if self.falla_commit is not None:
            raise self.falla_commit
        self.filas.extend(pendientes)
        self.commits += 1


@pytest.fixture
def entorno(monkeypatch):
    ruta = interruptor.ruta_del_interruptor()
    eventos = []

    async def publicar(tipo, payload):
        eventos.append((tipo, payload))
        return 0

    base = _Base()
    monkeypatch.setattr(kill_switch.event_bus, "publicar_a_todos", publicar)
    monkeypatch.setattr(kill_switch, "transaccion", base.transaccion)
    return ruta, eventos, base


async def test_activar_pone_el_freno_avisa_y_audita(entorno):
    ruta, eventos, base = entorno
    assert await kill_switch.activar(ADMIN) == {"activo": True, "cambio": True}
    assert json.loads(ruta.read_text())["user_id"] == "7"
    assert eventos == [("kill_switch_activated", {"activo": True})]
    assert base.filas == [("activar", 7)]


async def test_activar_dos_veces_no_duplica_nada(entorno):
    _, eventos, base = entorno
    await kill_switch.activar(ADMIN)
    assert await kill_switch.activar(ADMIN) == {"activo": True, "cambio": False}
    assert len(eventos) == 1 and len(base.filas) == 1


async def test_activar_con_la_auditoria_caida_deja_el_freno_puesto(entorno):
    ruta, eventos, base = entorno
    base.falla_insert = RuntimeError("base caída")
    with pytest.raises(kill_switch.AuditoriaDelInterruptorFallida):
        await kill_switch.activar(ADMIN)
    assert interruptor.interruptor_activo(ruta)
    assert eventos == [("kill_switch_activated", {"activo": True})]


async def test_reanudar_sin_freno_no_audita(entorno):
    _, eventos, base = entorno
    assert await kill_switch.reanudar(ADMIN) == {"activo": False, "cambio": False}
    assert eventos == [] and base.filas == []


async def test_reanudar_audita_quita_y_avisa(entorno):
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")
    assert await kill_switch.reanudar(ADMIN) == {"activo": False, "cambio": True}
    assert not interruptor.interruptor_activo(ruta)
    assert base.filas == [("reanudar", 7)]
    assert eventos == [("kill_switch_released", {"activo": False})]


async def test_reanudar_si_la_confirmacion_falla_vuelve_a_poner_el_freno(entorno):
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")
    base.falla_commit = RuntimeError("commit perdido")
    with pytest.raises(kill_switch.AuditoriaDelInterruptorFallida):
        await kill_switch.reanudar(ADMIN)
    assert interruptor.interruptor_activo(ruta)
    assert eventos == [] and base.filas == []


async def test_reanudar_si_el_insert_falla_no_toca_el_freno(entorno):
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, '{"original": true}')
    base.falla_insert = RuntimeError("base caída")
    with pytest.raises(kill_switch.AuditoriaDelInterruptorFallida):
        await kill_switch.reanudar(ADMIN)
    assert json.loads(ruta.read_text()) == {"original": True}
    assert eventos == []


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
async def test_reanudar_sin_permiso_de_escritura_no_cambia_nada(entorno):
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")
    ruta.parent.chmod(0o500)
    try:
        with pytest.raises(kill_switch.InterruptorNoEscribible):
            await kill_switch.reanudar(ADMIN)
    finally:
        ruta.parent.chmod(0o700)
    assert interruptor.interruptor_activo(ruta)
    assert base.commits == 0 and eventos == []


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
async def test_activar_sin_permiso_de_escritura_no_avisa_ni_audita(entorno):
    ruta, eventos, base = entorno
    ruta.parent.chmod(0o500)
    try:
        with pytest.raises(kill_switch.InterruptorNoEscribible):
            await kill_switch.activar(ADMIN)
    finally:
        ruta.parent.chmod(0o700)
    assert not interruptor.interruptor_activo(ruta)
    assert eventos == [] and base.filas == []


async def test_veinte_activaciones_simultaneas_ponen_el_freno_una_vez(entorno):
    _, eventos, base = entorno
    respuestas = await asyncio.gather(*(kill_switch.activar(ADMIN) for _ in range(20)))
    assert sum(r["cambio"] for r in respuestas) == 1
    assert len(base.filas) == 1 and len(eventos) == 1


async def test_la_mesa_libre_deja_pasar_y_frenada_responde_423(entorno):
    ruta, _, _ = entorno
    assert await kill_switch.exigir_mesa_libre(OPERADOR) is OPERADOR
    interruptor.escribir_pausa(ruta, "{}")
    with pytest.raises(HTTPException) as frenada:
        await kill_switch.exigir_mesa_libre(OPERADOR)
    assert (frenada.value.status_code, frenada.value.detail) == (423, "kill_switch_activo")


async def test_publicar_a_todos_llega_a_cada_suscriptor_y_uno_roto_no_corta():
    bus = EventBus()
    recibidos = []

    async def bien(evento):
        recibidos.append((evento.tenant_id, evento.user_id, evento.event_type, evento.payload))

    async def roto(evento):
        raise RuntimeError("socket muerto")

    await bus.subscribe("t1", "u1", bien)
    await bus.subscribe("t1", "u2", roto)
    await bus.subscribe("t2", "u3", bien)
    assert await bus.publicar_a_todos("kill_switch_released", {"activo": False}) == 2
    assert sorted(recibidos) == [
        ("t1", "u1", "kill_switch_released", {"activo": False}),
        ("t2", "u3", "kill_switch_released", {"activo": False}),
    ]


def test_el_evento_liberado_es_un_tipo_valido():
    JAXEvent(event_type="kill_switch_released", tenant_id="1", user_id="1")
