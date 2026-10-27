"""Cancelled HTTP requests must not release authority while SMTP is active."""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from api.admin import smtp


@pytest.mark.parametrize("endpoint", ["connection", "send"])
def test_cancelled_smtp_request_keeps_authority_until_thread_finishes(monkeypatch, endpoint):
    started = threading.Event()
    finish = threading.Event()
    released = threading.Event()

    @asynccontextmanager
    async def authority_guard(_user):
        try:
            yield None
        finally:
            released.set()

    def blocked_smtp(*_args):
        started.set()
        assert finish.wait(5), "SMTP fixture timed out"

    monkeypatch.setattr(smtp, "_office_smtp_guard", authority_guard)
    monkeypatch.setattr(smtp, "_limitar", lambda *_args: None)
    user = SimpleNamespace(user_id=1)
    if endpoint == "connection":
        monkeypatch.setattr(smtp.smtp_config, "probar_conexion", blocked_smtp)
        request = smtp.SmtpConexion(host="smtp.example.test", port=465, encryption="ssl",
                                    user="office", password="fixture-password")
        operation = smtp.probar_conexion_smtp(request, user=user)
    else:
        async def config_rows():
            return {"smtp.test_to": "test@example.test"}

        monkeypatch.setattr(smtp.smtp_config, "leer_filas", config_rows)
        monkeypatch.setattr(smtp.smtp_config, "interpretar", lambda _rows: object())
        monkeypatch.setattr(smtp.smtp_config, "destinatario_de_prueba", lambda *_args: "test@example.test")
        monkeypatch.setattr(smtp.smtp_config, "construir_mensaje", lambda *_args: object())
        monkeypatch.setattr(smtp.smtp_config, "enviar", blocked_smtp)
        operation = smtp.enviar_prueba_smtp(smtp.SmtpPrueba(to="test@example.test"), user=user)

    async def scenario():
        task = asyncio.create_task(operation)
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
            task.cancel()
            await asyncio.sleep(0.02)
            assert not released.is_set(), "authority released while SMTP continued"
            assert not task.done(), "request completed before SMTP effect ended"
            task.cancel()
            await asyncio.sleep(0.02)
            assert not released.is_set(), "a second cancellation released authority"
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert released.is_set()
        finally:
            finish.set()

    asyncio.run(scenario())
