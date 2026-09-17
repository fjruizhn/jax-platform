"""La Mesa frenada (plan 2026-09-16-frente-b-kill-switch, Task 8). Con el
freno puesto, las rutas que EJECUTAN responden 423 antes de tocar un modelo,
Jacobs o el disco; al reanudar vuelven a aceptar. Cancelar NO se frena."""
import pytest
from fastapi.routing import APIRoute, iter_route_contexts

import kill_switch
from tests.identidades import auth, cabeceras, token_para

pytestmark = pytest.mark.usefixtures("chat_sin_memoria")


def _dependencias(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependencias(sub)


def test_las_rutas_que_ejecutan_piden_la_mesa_libre():
    from main import app

    efectivas = [rc for rc in iter_route_contexts(app.routes) if isinstance(rc.original_route, APIRoute)]
    assert ("POST", "/api/chat") in {(m, rc.path) for rc in efectivas for m in rc.methods}
    frenadas = {
        (metodo, rc.path)
        for rc in efectivas
        if kill_switch.exigir_mesa_libre in set(_dependencias(rc.dependant))
        for metodo in rc.methods
    }
    assert frenadas == set(kill_switch.RUTAS_FRENADAS)
    assert ("POST", "/api/pipelines/{pipeline_id}/cancel") not in frenadas


def test_peor_caso_en_la_mesa(client, usuarios, monkeypatch):
    from api import chat as chat_mod
    import http_client

    async def sin_modelo(*args, **kwargs):
        raise AssertionError("con el freno puesto no se llama a ningún modelo")

    class _SinJacobs:
        async def post(self, *args, **kwargs):
            raise AssertionError("con el freno puesto no se habla con Jacobs")

    async def cliente_sin_jacobs():
        return _SinJacobs()

    monkeypatch.setattr(chat_mod, "_invoke_facet", sin_modelo)
    monkeypatch.setattr(http_client, "get_http_client", cliente_sin_jacobs)
    for modulo in ("api.pipelines", "api.image"):
        mod = __import__(modulo, fromlist=["x"])
        if hasattr(mod, "get_http_client"):
            monkeypatch.setattr(mod, "get_http_client", cliente_sin_jacobs)

    admin_id, _ = usuarios(role="superadmin")
    admin = auth(token_para(admin_id, role="superadmin"))
    operador = cabeceras(client, "ks-mesa-operador")

    assert client.post("/api/admin/kill-switch/activar", headers=admin).status_code == 200
    casos = [
        ("/api/chat", {"message": "hola", "facet": "jax_local"}),
        ("/api/image/generate", {"prompt": "un faro"}),
        ("/api/command", {"command": "ls", "mode": "dry_run"}),
        ("/api/pipelines", {"name": "t", "objective": "o"}),
        ("/api/pipelines/00000000-0000-0000-0000-000000000000/resume", None),
    ]
    for ruta, cuerpo in casos:
        r = client.post(ruta, json=cuerpo, headers=operador)
        assert (r.status_code, r.json().get("detail")) == (423, "kill_switch_activo"), ruta

    assert client.post("/api/admin/kill-switch/reanudar", headers=admin).status_code == 200
    r = client.post("/api/chat", json={"message": "hola", "facet": "__no_existe__"}, headers=operador)
    assert r.status_code == 400  # pasó el freno; lo detiene la validación, nunca el modelo
