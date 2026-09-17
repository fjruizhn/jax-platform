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
from jax_engine import events as events_mod
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
    assert await kill_switch.activar(ADMIN) == {"activo": True, "cambio": True, "heredada": False}
    assert json.loads(ruta.read_text())["user_id"] == "7"
    assert eventos == [("kill_switch_activated", {"activo": True})]
    assert base.filas == [("activar", 7)]


async def test_activar_dos_veces_no_duplica_nada(entorno):
    _, eventos, base = entorno
    await kill_switch.activar(ADMIN)
    assert await kill_switch.activar(ADMIN) == {"activo": True, "cambio": False, "heredada": False}
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
    assert await kill_switch.reanudar(ADMIN) == {"activo": False, "cambio": False, "heredada": False}
    assert eventos == [] and base.filas == []


async def test_reanudar_audita_quita_y_avisa(entorno):
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")
    assert await kill_switch.reanudar(ADMIN) == {"activo": False, "cambio": True, "heredada": False}
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


# --- Fix round 1 (revisión del controlador): R9, "el archivo es la verdad" ---


async def test_reanudar_con_fsync_roto_tras_borrar_repone_el_freno_y_marca_auditoria_fallida(entorno, monkeypatch):
    """`borrar_pausa` hace os.unlink y DESPUÉS _sincronizar_directorio: si el
    unlink surtió efecto y el fsync del directorio explota, el freno ya está
    afuera de verdad -- decir "nada cambió" (503) sería mentir. R9: se repone
    el freno (ante la duda, frenado) y se avisa como auditoría fallida (500),
    nunca como InterruptorNoEscribible."""
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")

    def _fsync_roto(_directorio):
        raise OSError("fsync roto")

    monkeypatch.setattr(interruptor, "_sincronizar_directorio", _fsync_roto)
    with pytest.raises(kill_switch.AuditoriaDelInterruptorFallida):
        await kill_switch.reanudar(ADMIN)
    # el unlink SÍ surtió efecto y R9 repuso el freno con contenido nuevo
    assert interruptor.interruptor_activo(ruta)
    assert eventos == []
    # el INSERT quedó pendiente dentro de la transacción que la excepción
    # abortó -- el fake nunca llega a "confirmar" (mismo criterio que el
    # commit fallido: sin fila, porque no hubo auditoría real).
    assert base.filas == []


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
async def test_reanudar_con_fsync_roto_sin_haber_borrado_no_repone_nada(entorno, monkeypatch):
    """Si `_sincronizar_directorio` explota ANTES de que el unlink real haya
    surtido efecto (permiso denegado sobre el propio archivo, no sobre el
    directorio), el freno sigue puesto -- R9 no debe reponer lo que nunca se
    quitó, solo relanzar."""
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")
    ruta.parent.chmod(0o500)
    try:
        with pytest.raises(kill_switch.InterruptorNoEscribible):
            await kill_switch.reanudar(ADMIN)
    finally:
        ruta.parent.chmod(0o700)
    assert interruptor.interruptor_activo(ruta)
    assert eventos == [] and base.filas == []


async def test_activar_con_fsync_roto_tras_poner_el_freno_sigue_el_camino_de_cambio_real(entorno, monkeypatch):
    """`escribir_pausa` hace os.link y DESPUÉS _sincronizar_directorio: si el
    link surtió efecto (el freno YA está puesto de verdad) y el fsync del
    directorio explota, activar no debe tratarlo como "no escribible" -- el
    freno está puesto, así que sigue el camino de cambio real: difusión +
    auditoría, cambio=True."""
    ruta, eventos, base = entorno

    def _fsync_roto(_directorio):
        raise OSError("fsync roto")

    monkeypatch.setattr(interruptor, "_sincronizar_directorio", _fsync_roto)
    assert await kill_switch.activar(ADMIN) == {"activo": True, "cambio": True, "heredada": False}
    assert interruptor.interruptor_activo(ruta)
    assert eventos == [("kill_switch_activated", {"activo": True})]
    assert base.filas == [("activar", 7)]


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
async def test_activar_con_fsync_roto_y_el_freno_realmente_no_puesto_relanza(entorno, monkeypatch):
    """Si el link ni siquiera llegó a surtir efecto (el freno sigue sin
    estar puesto), R9 no debe inventar un cambio -- se relanza
    InterruptorNoEscribible tal cual."""
    ruta, eventos, base = entorno
    ruta.parent.chmod(0o500)
    try:
        with pytest.raises(kill_switch.InterruptorNoEscribible):
            await kill_switch.activar(ADMIN)
    finally:
        ruta.parent.chmod(0o700)
    assert not interruptor.interruptor_activo(ruta)
    assert eventos == [] and base.filas == []


# --- Fix round 2 (re-revisión del controlador): R10, "antes" se mide antes de escribir ---


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
async def test_activar_con_el_freno_ya_puesto_y_directorio_sin_permiso_no_inventa_un_cambio(entorno):
    """Hallazgo del re-review: si el freno YA estaba puesto antes de esta
    llamada y la escritura falla ANTES de tocar el archivo real (mkstemp sin
    permiso de escritura en el directorio), el archivo sigue ahí por la
    misma razón de SIEMPRE (estaba puesto de antes), no porque esta llamada
    lo haya puesto. R10: "antes" se mide ANTES de escribir -- si "antes" ya
    era True, no hay cambio real que anunciar, se relanza tal cual."""
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")
    ruta.parent.chmod(0o500)
    try:
        with pytest.raises(kill_switch.InterruptorNoEscribible):
            await kill_switch.activar(ADMIN)
    finally:
        ruta.parent.chmod(0o700)
    assert interruptor.interruptor_activo(ruta)
    assert eventos == [] and base.filas == []


async def test_activar_con_fail_closed_solo_tras_el_fallo_de_escritura_cuenta_como_cambio(entorno, monkeypatch):
    """R10, el caso opuesto explícito de la ruling: si "antes" se pudo medir
    limpio (freno SUELTO) y el fallo posterior de `interruptor_activo` (la
    relectura DESPUÉS del error de escritura) da True por fail-closed --no
    pudo ni mirar el archivo, no que lo haya visto puesto-- igual cuenta
    como cambio real a propósito: cualquier otro lector de JAX en ese mismo
    instante también lo vería fail-closed (frenado), así que "no cambió
    nada" sería la mentira peor. Se mockea `_escribir` (falla siempre) y
    `interruptor.os.stat` (primera llamada limpia -> False; segunda llamada
    en adelante -> OSError generico -> fail-closed) para separar las dos
    lecturas sin depender de permisos reales de archivo."""
    ruta, eventos, base = entorno
    llamadas = {"n": 0}

    def _escribir_roto(_ruta, _contenido):
        raise kill_switch.InterruptorNoEscribible("disco caído a mitad de camino")

    stat_real = interruptor.os.stat

    def _stat_limpio_y_luego_fail_closed(_ruta_arg, *a, **kw):
        # Task H: sólo el archivo propio; la ruta heredada sigue su stat real
        # (el conftest la desvía a un temporal inexistente).
        if str(_ruta_arg) != str(ruta):
            return stat_real(_ruta_arg, *a, **kw)
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise FileNotFoundError()
        raise OSError("nfs caído")  # ni FileNotFoundError: interruptor_activo lo lee fail-closed

    monkeypatch.setattr(interruptor.os, "stat", _stat_limpio_y_luego_fail_closed)
    monkeypatch.setattr(kill_switch, "_escribir", _escribir_roto)
    resultado = await kill_switch.activar(ADMIN)
    assert resultado == {"activo": True, "cambio": True, "heredada": False}
    assert eventos == [("kill_switch_activated", {"activo": True})]
    assert base.filas == [("activar", 7)]


async def test_reanudar_si_reponer_el_freno_tambien_falla_igual_se_lanza_auditoria_fallida(entorno, monkeypatch, caplog):
    """Si el intento de REPONER el freno (dentro del except genérico) también
    lanza InterruptorNoEscribible, esa excepción de reposición no debe
    reemplazar a AuditoriaDelInterruptorFallida -- se logea y se sigue de
    largo con el tipo correcto, encadenada a la causa original."""
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")
    base.falla_commit = RuntimeError("commit perdido")

    def _escribir_roto(_ruta, _contenido):
        raise kill_switch.InterruptorNoEscribible("no se pudo reponer")

    monkeypatch.setattr(kill_switch, "_escribir", _escribir_roto)
    with caplog.at_level("ERROR"):
        with pytest.raises(kill_switch.AuditoriaDelInterruptorFallida):
            await kill_switch.reanudar(ADMIN)
    assert eventos == [] and base.filas == []
    # el borrado real SÍ surtió efecto y la reposición (mockeada) fracasó:
    # el freno queda afuera -- doble falla real, sin invención de un estado.
    assert not interruptor.interruptor_activo(ruta)
    # fix round 2 (Minor): la reposición NO surtió efecto de verdad (el mock
    # nunca escribe nada) -- el log tiene que decir "NO pudo reponer", no
    # "repuso el freno".
    assert any("NO pudo reponer" in m for m in caplog.messages)
    assert not any("repuso el freno" in m for m in caplog.messages)


async def test_reanudar_si_el_reintento_de_reponer_igual_pone_el_freno_el_log_lo_dice(entorno, monkeypatch, caplog):
    """Fix round 2 (Minor): si la reposición de emergencia también choca con
    un fsync roto pero su `os.link` SÍ surte efecto, el freno queda puesto
    de verdad -- el log tiene que decirlo ("repuso el freno"), no "no pudo
    reponer" (que sería mentir, mismo principio R9 aplicado al mensaje)."""
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")

    def _fsync_siempre_roto(_directorio):
        raise OSError("fsync roto")

    monkeypatch.setattr(interruptor, "_sincronizar_directorio", _fsync_siempre_roto)
    with caplog.at_level("ERROR"):
        with pytest.raises(kill_switch.AuditoriaDelInterruptorFallida):
            await kill_switch.reanudar(ADMIN)
    # el unlink original surtió efecto, y el link de la reposición TAMBIÉN
    # -- el freno queda puesto de verdad, aunque las dos veces el fsync haya
    # fallado.
    assert interruptor.interruptor_activo(ruta)
    assert eventos == []
    assert any("repuso el freno" in m for m in caplog.messages)
    assert not any("NO pudo reponer" in m for m in caplog.messages)


# --- Fix round 1: R8, difusión concurrente con timeout por suscriptor ---


async def test_publicar_a_todos_no_se_cuelga_con_un_suscriptor_colgado(monkeypatch):
    """Un suscriptor colgado (WS sin ping, ~40 s) no puede retener
    `publicar_a_todos` -- cada suscriptor tiene un tope propio
    (TIEMPO_MAXIMO_POR_SUSCRIPTOR) y se manda a todos CONCURRENTE, no en
    serie."""
    monkeypatch.setattr(events_mod, "TIEMPO_MAXIMO_POR_SUSCRIPTOR", 0.05)
    bus = EventBus()
    recibidos = []

    async def bien(evento):
        recibidos.append(evento.user_id)

    async def colgado(evento):
        await asyncio.sleep(999)

    await bus.subscribe("t1", "u1", bien)
    await bus.subscribe("t1", "u2", colgado)
    inicio = asyncio.get_event_loop().time()
    resultado = await asyncio.wait_for(
        bus.publicar_a_todos("kill_switch_released", {"activo": False}), timeout=3)
    duracion = asyncio.get_event_loop().time() - inicio
    assert resultado == 1
    assert recibidos == ["u1"]
    # muy por debajo del límite duro de la prueba; generoso sobre el tope
    # por suscriptor mockeado, para no ser frágil bajo carga de CI.
    assert duracion < events_mod.TIEMPO_MAXIMO_POR_SUSCRIPTOR * 20


async def test_activar_reanudar_activar_no_se_cuelgan_con_un_suscriptor_colgado(entorno, monkeypatch):
    """Con el bus REAL (no el de mentira del fixture `entorno`) y un
    suscriptor colgado, la secuencia activar/reanudar/activar completa
    rápido -- ni siquiera el primer `activar` puede quedar retenido por un
    WS muerto antes de terminar de escribir el freno de emergencia."""
    monkeypatch.setattr(events_mod, "TIEMPO_MAXIMO_POR_SUSCRIPTOR", 0.05)
    bus_real = EventBus()

    async def colgado(evento):
        await asyncio.sleep(999)

    await bus_real.subscribe("1", "9", colgado)
    monkeypatch.setattr(kill_switch, "event_bus", bus_real)

    async def secuencia():
        await kill_switch.activar(ADMIN)
        await kill_switch.reanudar(ADMIN)
        await kill_switch.activar(ADMIN)

    await asyncio.wait_for(secuencia(), timeout=3)
