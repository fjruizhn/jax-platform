import asyncio

import pytest
from starlette.requests import Request

import api.admin.models as admin_models
import db.transaccion as db_transaccion
from auth.models import AuthUser


class _Cursor:
    def __init__(self, events, fail_audit=False):
        self.events = events
        self.fail_audit = fail_audit

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, statement, parameters=()):
        self.events.append(("execute", statement))
        if "INSERT INTO model_catalog_audit" in statement and self.fail_audit:
            raise RuntimeError("audit unavailable")

    async def fetchone(self):
        return ("provider", "model", None, None)


class _Connection:
    def __init__(self, events, fail_audit=False):
        self.events = events
        self.fail_audit = fail_audit

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def cursor(self):
        return _Cursor(self.events, self.fail_audit)

    async def begin(self):
        self.events.append(("begin", None))

    async def commit(self):
        self.events.append(("commit", None))

    async def rollback(self):
        self.events.append(("rollback", None))


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return self.conn


def _request():
    return Request({
        "type": "http",
        "method": "PUT",
        "path": "/api/admin/models/7/contrato-dispatch",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    })


def _user():
    return AuthUser(user_id="1", tenant_id="1", role="superadmin", email="admin@example.test")


def _configure(monkeypatch, conn):
    events = conn.events

    async def get_pool():
        return _Pool(conn)

    monkeypatch.setattr(db_transaccion, "get_pool", get_pool)
    monkeypatch.setattr(admin_models, "errores_del_contrato", lambda *_: [])
    monkeypatch.setattr(admin_models, "ip_de", lambda *_: "127.0.0.1")
    monkeypatch.setattr(admin_models.facet_resolver, "_tocar_sello", lambda: None)
    return events


def test_declarar_contrato_abre_transaccion_antes_de_leer_y_confirmar(monkeypatch):
    events = []
    conn = _Connection(events)
    _configure(monkeypatch, conn)

    asyncio.run(admin_models.declarar_contrato_dispatch(
        7,
        admin_models.ContratoDispatchRequest(max_tokens_param="max_tokens", max_output_tokens=4096),
        _request(),
        _user(),
    ))

    begin = events.index(("begin", None))
    assert events[-1] == ("commit", None)
    assert begin < next(
        index for index, event in enumerate(events)
        if event[0] == "execute" and event[1] == admin_models._SQL_DECLARAR_CONTRATO
    )
    assert next(
        index for index, event in enumerate(events)
        if event[0] == "execute" and event[1] == admin_models._SQL_CONTRATO_ACTUAL
    ) > begin


def test_error_al_insertar_auditoria_revierte_el_cambio_del_modelo(monkeypatch):
    events = []
    conn = _Connection(events, fail_audit=True)
    _configure(monkeypatch, conn)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        asyncio.run(admin_models.declarar_contrato_dispatch(
            7,
            admin_models.ContratoDispatchRequest(max_tokens_param="max_tokens", max_output_tokens=4096),
            _request(),
            _user(),
        ))

    assert ("begin", None) in events
    assert events[-1] == ("rollback", None)
    assert ("commit", None) not in events
