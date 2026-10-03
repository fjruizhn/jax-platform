import asyncio
import json
import secrets

import httpx

import credencial_las_manos as cred
from proyectos_documentos import despachador


def _owner():
    return cred.PlatformProcessingOwnership(tenant_id=11, user_id=22, project_id=33)


def _expected_headers(token):
    return {
        cred.ENCABEZADO: token,
        "X-Jax-Processing-Owner-Version": "processing-owner.1",
        "X-Jax-Processing-Tenant-Id": "11",
        "X-Jax-Processing-User-Id": "22",
        "X-Jax-Processing-Project-Id": "33",
    }


def test_dispatch_uses_owner_headers_and_never_sends_usuario(monkeypatch):
    token, seen = secrets.token_urlsafe(32), []
    monkeypatch.setenv(cred.VARIABLE, token)

    async def handler(request):
        seen.append(request)
        return httpx.Response(202, json={"job_id": "job-1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def get_client():
        return client

    async def claimed(_pool, *, ids, job_id, owner):
        assert ids == [1] and job_id == "job-1" and owner == _owner()
        return ids

    monkeypatch.setattr(despachador, "get_http_client", get_client)
    monkeypatch.setattr(despachador.repo, "marcar_despachadas", claimed)
    try:
        assert asyncio.run(despachador._despachar_trozo(
            object(), "project-uuid", _owner(), [{"id": 1, "ruta_entrada": "proyectos/project-uuid/entrada/l/a.pdf"}]
        )) == "seguir"
    finally:
        asyncio.run(client.aclose())
    assert json.loads(seen[0].content) == {"project_uuid": "project-uuid", "rutas": ["proyectos/project-uuid/entrada/l/a.pdf"]}
    assert {key: seen[0].headers[key] for key in _expected_headers(token)} == _expected_headers(token)


def test_status_get_uses_the_same_owner_headers(monkeypatch):
    token, seen = secrets.token_urlsafe(32), []
    monkeypatch.setenv(cred.VARIABLE, token)

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"estado": "running", "resultados": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def get_client():
        return client

    async def rows(_pool, *, job_id, owner):
        assert job_id == "job-1" and owner == _owner()
        return []

    monkeypatch.setattr(despachador, "get_http_client", get_client)
    monkeypatch.setattr(despachador.repo, "filas_abiertas_de_trabajo", rows)
    try:
        asyncio.run(despachador._sincronizar_trabajo(object(), "job-1", _owner()))
    finally:
        asyncio.run(client.aclose())
    assert {key: seen[0].headers[key] for key in _expected_headers(token)} == _expected_headers(token)
