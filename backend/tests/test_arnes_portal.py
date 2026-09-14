"""Arnés del portal (tanda A, hallazgo de Tarea 1, 2026-09-14).

`client.portal.call(func)` corre `func` en el `BlockingPortal` de anyio que
levanta `TestClient`. El fixture `client` es `scope="session"`
(conftest.py): si una excepción de pytest (`Failed`, que hereda de
`BaseException`, no de `Exception`) escapa DENTRO de una corrutina corrida
por el portal, el portal muere para el RESTO de la sesión -- cualquier test
posterior que llame a `client.portal.call()` revienta con
`RuntimeError: This portal is not running`, sin relación con lo que ese
test prueba. Medido en Tarea 1 (reporte del implementador bloqueado):
635 -> 352 passed / 184 failed / 108 errors, un solo test causante.

Regla de Fernando: sin hallazgos diferidos. `_envolver_portal_call` en
conftest.py captura la excepción DENTRO de la corrutina y la relanza
AFUERA, en el hilo sincrónico -- el test falla como corresponde y el
portal sigue vivo. Este archivo es el test de ese envoltorio, no de
capability.mode."""
import time

import pytest


def _esperar_portal_muerto(portal, tope_seg: float = 0.5) -> None:
    """Poll del estado REAL del portal, no un `sleep` fijo a ciegas (fix
    round 1, Minor 6 de la revisión).

    Sin el envoltorio, el `BlockingPortal` de anyio no muere de forma
    síncrona en cuanto escapa la `BaseException`: cancela su grupo de
    tareas en segundo plano, y hay una carrera -- una llamada inmediata al
    portal a veces todavía funciona. Medido (2026-09-14): con un `sleep`
    fijo de 0.2s el rojo salía determinístico en esta máquina, pero es un
    número adivinado, no una señal real. `BlockingPortal._check_running()`
    (`anyio/from_thread.py`) usa `self._event_loop_thread_id is None` como
    la condición exacta de "el portal ya no corre" -- es el mismo atributo
    que se cae cuando el hilo del loop termina tras la cancelación. Pollear
    ESO en vez de dormir un tiempo fijo espera lo mínimo necesario (típico:
    unos pocos milisegundos) y no depende de que 0.2s alcance en cualquier
    máquina/carga. `tope_seg` es solo el límite superior por si el portal
    NO muere (con el envoltorio puesto, nunca lo hace): ahí se sale del
    poll y la llamada de abajo simplemente funciona, sin gastar el tope
    completo -- se mide, no se supone.

    Es un atributo "privado" de anyio (con `_`), aceptable acá porque el
    test existe justamente para ejercitar ESE mecanismo interno, no un
    detalle de implementación ajeno a lo que se está probando."""
    paso = 0.005
    transcurrido = 0.0
    while getattr(portal, "_event_loop_thread_id", None) is not None and transcurrido < tope_seg:
        time.sleep(paso)
        transcurrido += paso


def test_pytest_fail_dentro_del_portal_no_mata_la_sesion(client):
    """Rojo visto (mutación probada en esta tarea: sacar el envoltorio de
    conftest.py): sin él, la primera llamada (el `pytest.fail` sin
    capturar) mata el `BlockingPortal`; `_esperar_portal_muerto` espera a
    que ese cierre en segundo plano termine de verdad (en vez de dormir un
    tiempo fijo adivinado) y entonces la SEGUNDA llamada da, de forma
    determinística, `RuntimeError: This portal is not running`. Con el
    envoltorio (código real), el portal nunca llega a ese estado: el poll
    agota su tope sin encontrarlo muerto y la segunda llamada funciona."""
    async def explota():
        pytest.fail("mensaje de prueba, a propósito (test_arnes_portal)")

    with pytest.raises(pytest.fail.Exception):
        client.portal.call(explota)

    _esperar_portal_muerto(client.portal)

    async def sigue_viva():
        return "el portal sigue vivo"

    assert client.portal.call(sigue_viva) == "el portal sigue vivo"
