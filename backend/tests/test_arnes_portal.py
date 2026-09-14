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
import pytest


def test_pytest_fail_dentro_del_portal_no_mata_la_sesion(client):
    """Rojo visto (mutación probada en esta tarea: sacar el envoltorio de
    conftest.py): sin él, el `BlockingPortal` de anyio cancela su grupo de
    tareas en segundo plano cuando una `BaseException` escapa -- el cierre
    no es instantáneo (es una carrera), así que una llamada inmediata al
    portal a veces todavía funciona. El `sleep` de abajo le da tiempo al
    cierre para completarse: con eso, sin el envoltorio, la SEGUNDA llamada
    da de forma determinística `RuntimeError: This portal is not running`
    -- la primera, con el `pytest.fail` sin capturar, ya mató el portal.
    Con el envoltorio (código real), las dos llamadas conviven sin error."""
    async def explota():
        pytest.fail("mensaje de prueba, a propósito (test_arnes_portal)")

    with pytest.raises(pytest.fail.Exception):
        client.portal.call(explota)

    import time
    time.sleep(0.2)

    async def sigue_viva():
        return "el portal sigue vivo"

    assert client.portal.call(sigue_viva) == "el portal sigue vivo"
