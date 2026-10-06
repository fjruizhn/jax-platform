"""Pruebas del contrato de lectura de estado sin MariaDB."""
import asyncio

import pytest
from fastapi import HTTPException

from api import proyectos_documentos as api
from auth.models import AuthUser


USUARIO = AuthUser(user_id="7", tenant_id="3", role="operator")


def test_estado_devuelve_solo_campos_cerrados_y_usa_el_proyecto_autorizado(monkeypatch):
    llamadas = []

    async def con_papel(user, project_id, *, escribe, activo=False):
        llamadas.append((user.user_id, project_id, escribe))
        return {"id": project_id, "uuid": "proyecto-uno", "papel": "VIEWER", "estado": "ACTIVE"}

    async def estado_documento(pool, *, project_id, documento_id):
        llamadas.append((project_id, documento_id))
        return {"id": documento_id, "nombre": "estado.pdf", "estado": "error", "error": "ocr_sin_texto"}

    monkeypatch.setattr(api, "_con_papel", con_papel)
    monkeypatch.setattr(api.repo, "estado_documento", estado_documento)
    monkeypatch.setattr(api, "get_pool", lambda: asyncio.sleep(0, result=object()))

    resultado = asyncio.run(api.estado(41, 9001, USUARIO))

    assert resultado == {"id": 9001, "nombre": "estado.pdf", "estado": "error", "error": "ocr_sin_texto"}
    assert llamadas == [("7", 41, False), (41, 9001)]


def test_estado_no_revela_un_documento_de_otro_proyecto(monkeypatch):
    async def con_papel(user, project_id, *, escribe, activo=False):
        return {"id": project_id, "uuid": "proyecto-otro", "papel": "VIEWER", "estado": "ACTIVE"}

    async def estado_documento(pool, *, project_id, documento_id):
        assert (project_id, documento_id) == (42, 9001)
        return None

    monkeypatch.setattr(api, "_con_papel", con_papel)
    monkeypatch.setattr(api.repo, "estado_documento", estado_documento)
    monkeypatch.setattr(api, "get_pool", lambda: asyncio.sleep(0, result=object()))

    with pytest.raises(HTTPException) as error:
        asyncio.run(api.estado(42, 9001, USUARIO))
    assert (error.value.status_code, error.value.detail) == (404, {"code": "documento_no_encontrado"})
