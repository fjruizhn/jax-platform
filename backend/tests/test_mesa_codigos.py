"""Frente A (2026-09-16): la Mesa no recibe texto en español del backend.
A-51: detail con codigo estable en image/pipelines/upload. A-14:
image_generated y command_started no tienen consumidor. (T16, 2026-10-02: se
retiro /command y con el sus pruebas de codigos A-51/A-53.) A-30: locales de un
solo uso. Puros."""
import ast
import asyncio
import io
import typing
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException, UploadFile

from adjuntos.limites import cargar_limites
from api import image as image_mod
from api import pipelines as pipelines_mod
from api import upload as upload_mod
from auth.models import AuthUser
from credential_resolver import CredentialUnavailableError
from jax_engine import events as events_mod
from jax_engine.schemas import EventType

BACKEND = Path(__file__).resolve().parent.parent
USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")
MODULOS = ("api/chat.py", "api/image.py", "api/pipelines.py", "api/upload.py")


def _error(corutina) -> HTTPException:
    try:
        asyncio.run(corutina)
    except HTTPException as e:
        return e
    raise AssertionError("se esperaba HTTPException")


@pytest.mark.parametrize("rel", MODULOS)
def test_ningun_detail_es_texto_libre(rel):
    """detail: un codigo snake_case, un dict con 'code' o un nombre (variable)."""
    malos = []
    for nodo in ast.walk(ast.parse((BACKEND / rel).read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.Call) and getattr(nodo.func, "id", None) == "HTTPException":
            for kw in nodo.keywords:
                if kw.arg != "detail":
                    continue
                v = kw.value
                ok = (isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.replace("_", "").isalpha()
                      and v.value == v.value.lower()) \
                    or (isinstance(v, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "code" for k in v.keys)) \
                    or isinstance(v, ast.Name)
                if not ok:
                    malos.append(f"{rel}:{nodo.lineno}")
    assert malos == []


def test_los_eventos_sin_consumidor_no_existen():
    tipos = typing.get_args(EventType)
    assert "image_generated" not in tipos and "command_started" not in tipos


def test_imagen_sin_credencial_es_503_con_codigo(monkeypatch):
    async def sin_credencial(_p):
        raise CredentialUnavailableError("openai")
    monkeypatch.setattr(image_mod, "resolve_credential", sin_credencial)
    e = _error(image_mod.generate_image(image_mod.ImageRequest(prompt="x"), user=USUARIO))
    assert (e.status_code, e.detail) == (503, {"code": "credencial_no_disponible", "provider": "openai"})


def test_la_imagen_no_publica_un_evento(monkeypatch):
    async def credencial(_p):
        return "sk-x"

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"b64_json": "QQ=="}]}

    class _Cliente:
        async def post(self, *a, **k):
            return _Resp()

    async def cliente():
        return _Cliente()

    publicados = []

    async def publicar(evento):
        publicados.append(evento)

    async def uso(*a, **k):
        return None

    monkeypatch.setattr(image_mod, "resolve_credential", credencial)
    monkeypatch.setattr(image_mod, "get_http_client", cliente)
    monkeypatch.setattr(image_mod, "record_usage", uso)
    # image.py ya no importa event_bus: se parcha el bus compartido, asi cualquier
    # publicacion (por el import que sea) queda registrada.
    monkeypatch.setattr(events_mod.event_bus, "publish", publicar)
    r = asyncio.run(image_mod.generate_image(image_mod.ImageRequest(prompt="un gato"), user=USUARIO))
    assert (r.url, r.revised_prompt) == ("data:image/png;base64,QQ==", "un gato")
    assert publicados == []


def test_el_limite_de_pipelines_es_un_codigo_con_el_maximo(monkeypatch):
    # Frente C (2026-09-16): el máximo es el ajuste max_pipelines leído por
    # request (ajustes.valor), no una constante del módulo.
    async def ajuste(clave):
        assert clave == pipelines_mod.ajustes.MAX_PIPELINES
        return 5

    async def lleno(_t, limite):
        assert limite == 5
        return False
    monkeypatch.setattr(pipelines_mod.ajustes, "valor", ajuste)
    monkeypatch.setattr(pipelines_mod.resource_manager, "can_start_pipeline", lleno)
    e = _error(pipelines_mod.create_pipeline(request=None, user=USUARIO))
    assert (e.status_code, e.detail) == (429, {"code": "limite_de_pipelines", "max": 5})


class _Request:
    # Spec 2026-09-17: la creación exige pasos (pre-vuelo antes de gastar).
    async def json(self):
        return {"objective": "x", "steps": [{"facet": "thot", "capability": "critique", "prompt": "x"}]}


def _cliente_jacobs(monkeypatch, respuesta=None, excepcion=None):
    """`respuesta`/`excepcion` son lo que devuelve/lanza la llamada de
    CREACIÓN (POST /pipeline); el pre-vuelo (POST /preflight) siempre
    aprueba acá -- así el status/código que afirma cada test sale del sitio
    que su nombre dice, no del pre-vuelo (fix round 1 ítem 6)."""
    aprobado = httpx.Response(200, json={"ok": True, "violaciones": [], "costo_max_usd": "0.00",
                                         "pasos_costo": [], "sondeadas": []})

    class _C:
        async def post(self, url, *a, **k):
            if url.endswith("/preflight"):
                return aprobado
            if excepcion:
                raise excepcion
            return respuesta

    async def cliente():
        return _C()

    # Frente C (2026-09-16): create_pipeline lee el ajuste max_pipelines antes
    # del cupo; estos tests miran a Jacobs, no el cupo, y no tocan la DB.
    async def ajuste(_clave):
        return 3

    async def libre(_t, _limite):
        return True

    monkeypatch.setattr(pipelines_mod.ajustes, "valor", ajuste)
    monkeypatch.setattr(pipelines_mod, "get_http_client", cliente)
    monkeypatch.setattr(pipelines_mod.resource_manager, "can_start_pipeline", libre)


def test_un_rechazo_de_jacobs_es_un_codigo(monkeypatch):
    req = httpx.Request("POST", "http://j.test/pipeline")
    _cliente_jacobs(monkeypatch, respuesta=httpx.Response(423, json={"detail": "kill switch"}, request=req))
    e = _error(pipelines_mod.create_pipeline(request=_Request(), user=USUARIO))
    assert (e.status_code, e.detail) == (423, {"code": "jacobs_rechazo", "status": 423, "motivo": "kill switch"})


def test_jacobs_caido_es_un_codigo(monkeypatch):
    _cliente_jacobs(monkeypatch, excepcion=httpx.ConnectError("refused"))
    e = _error(pipelines_mod.create_pipeline(request=_Request(), user=USUARIO))
    assert (e.status_code, e.detail["code"]) == (502, "jacobs_no_responde")


def test_pipeline_id_invalido_es_un_codigo():
    e = _error(pipelines_mod._require_pipeline_owner("no-es-uuid", USUARIO))
    assert (e.status_code, e.detail) == (400, "pipeline_id_invalido")


def test_adjunto_demasiado_grande_es_un_codigo():
    max_bytes = cargar_limites().max_bytes
    grande = UploadFile(file=io.BytesIO(b"x" * (max_bytes + 1)), filename="a.txt")
    e = _error(upload_mod.upload_file(file=grande, user=USUARIO))
    assert (e.status_code, e.detail) == (413, {"code": "adjunto_demasiado_grande",
                                               "max_bytes": max_bytes})
