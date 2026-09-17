"""Frente A (2026-09-16): la Mesa no recibe texto en español del backend.
A-51: detail con codigo estable en image/command/pipelines/upload. A-53: el
resultado de un comando sin output, fallido o simulado es un codigo. A-14:
image_generated y command_started no tienen consumidor. A-30: locales de un
solo uso. Puros."""
import ast
import asyncio
import io
import json
import os
import stat
import typing
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException, UploadFile

from adjuntos.limites import cargar_limites
from api import command as command_mod
from api import image as image_mod
from api import pipelines as pipelines_mod
from api import upload as upload_mod
from auth.models import AuthUser
from credential_resolver import CredentialUnavailableError
from jax_engine import events as events_mod
from jax_engine.schemas import EventType

BACKEND = Path(__file__).resolve().parent.parent
USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")
MODULOS = ("api/chat.py", "api/image.py", "api/command.py", "api/pipelines.py", "api/upload.py")


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
    monkeypatch.setattr(image_mod, "resolve_credential_instrumented", sin_credencial)
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

    monkeypatch.setattr(image_mod, "resolve_credential_instrumented", credencial)
    monkeypatch.setattr(image_mod, "get_http_client", cliente)
    monkeypatch.setattr(image_mod, "record_usage", uso)
    # image.py ya no importa event_bus: se parcha el bus compartido, asi cualquier
    # publicacion (por el import que sea) queda registrada.
    monkeypatch.setattr(events_mod.event_bus, "publish", publicar)
    r = asyncio.run(image_mod.generate_image(image_mod.ImageRequest(prompt="un gato"), user=USUARIO))
    assert (r.url, r.revised_prompt) == ("data:image/png;base64,QQ==", "un gato")
    assert publicados == []


@pytest.fixture
def misiones(tmp_path, monkeypatch):
    monkeypatch.setattr(command_mod, "MISSIONS_DIR", tmp_path)
    eventos = []

    async def publicar(evento):
        eventos.append(evento)

    async def estado(*a, **k):
        return None

    monkeypatch.setattr(command_mod.event_bus, "publish", publicar)
    monkeypatch.setattr(command_mod.engine_state, "set_facet_status", estado)
    return tmp_path, eventos


def _binario(tmp_path, cuerpo):
    ruta = tmp_path / "jax-falso"
    ruta.write_text(f"#!/bin/sh\n{cuerpo}\n")
    ruta.chmod(ruta.stat().st_mode | stat.S_IEXEC)
    return ruta


def _correr(tmp_path, eventos, modo="execute"):
    tid = "44444444-dddd-4ddd-8ddd-000000000004"
    mision = tmp_path / f"web-task-{tid}.md"
    mision.write_text("---\nfaceta: hyde\n---\n\nlistar\n")
    (tmp_path / f"web-task-{tid}_owner.json").write_text(json.dumps({"tenant_id": "1", "user_id": "5"}))
    resultado = tmp_path / f"web-task-{tid}_result.md"
    asyncio.run(command_mod._run_command(tid, mision, resultado, "1", "5", modo))
    (evento,) = [e for e in eventos if e.event_type == "command_completed"]
    return tid, evento.payload, resultado


def test_comando_sin_output_es_un_codigo(misiones, monkeypatch):
    tmp, eventos = misiones
    monkeypatch.setattr(command_mod, "JAX_BIN", _binario(tmp, "exit 0"))
    tid, payload, resultado = _correr(tmp, eventos)
    assert payload == {"task_id": tid, "status": "completed", "code": "comando_sin_resultado", "result": ""}
    assert resultado.read_text() == ""
    assert asyncio.run(command_mod.get_command_result(tid, user=USUARIO)) == {
        "status": "completed", "result": "", "code": "comando_sin_resultado"}


def test_comando_que_falla_es_un_codigo_con_motivo(misiones, monkeypatch):
    tmp, eventos = misiones
    monkeypatch.setattr(command_mod, "JAX_BIN", tmp / "no-existe")
    tid, payload, _ = _correr(tmp, eventos)
    assert (payload["status"], payload["code"], payload["result"]) == ("failed", "comando_fallo", "")
    assert "no-existe" in payload["motivo"]
    consulta = asyncio.run(command_mod.get_command_result(tid, user=USUARIO))
    assert (consulta["status"], consulta["code"]) == ("failed", "comando_fallo")


def test_dry_run_es_un_codigo_con_la_mision(misiones):
    tmp, eventos = misiones
    tid, payload, _ = _correr(tmp, eventos, "dry_run")
    assert (payload["status"], payload["code"]) == ("completed", "comando_simulado")
    assert payload["result"] == "---\nfaceta: hyde\n---\n\nlistar\n"


def test_crear_comando_no_publica_command_started(misiones, monkeypatch):
    tmp, eventos = misiones

    async def sin_correr(*a, **k):
        return None

    monkeypatch.setattr(command_mod, "_run_command", sin_correr)
    asyncio.run(command_mod.create_command(command_mod.CommandRequest(command="x"), user=USUARIO))
    assert [e.event_type for e in eventos] == []


def test_task_id_invalido_es_un_codigo():
    e = _error(command_mod.get_command_result("no-es-uuid", user=USUARIO))
    assert (e.status_code, e.detail) == (400, "task_id_invalido")


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
    async def json(self):
        return {"objective": "x"}


def _cliente_jacobs(monkeypatch, respuesta=None, excepcion=None):
    class _C:
        async def post(self, *a, **k):
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


def test_sin_archivo_de_duenio_el_fallo_se_publica_igual_y_hyde_vuelve_a_idle(tmp_path, monkeypatch):
    """Fix round 1 (R7): si registrar el fallo en disco revienta, el evento
    failed y el estado idle salen igual (antes: hyde quedaba en thinking)."""
    monkeypatch.setattr(command_mod, "MISSIONS_DIR", tmp_path)
    monkeypatch.setattr(command_mod, "JAX_BIN", tmp_path / "no-existe")
    eventos, estados = [], []

    async def publicar(evento):
        eventos.append(evento)

    async def estado(*a, **k):
        estados.append(a)

    monkeypatch.setattr(command_mod.event_bus, "publish", publicar)
    monkeypatch.setattr(command_mod.engine_state, "set_facet_status", estado)
    tid = "55555555-eeee-4eee-8eee-000000000005"
    mision = tmp_path / f"web-task-{tid}.md"
    mision.write_text("---\nfaceta: hyde\n---\n\nlistar\n")
    asyncio.run(command_mod._run_command(tid, mision, tmp_path / f"web-task-{tid}_result.md", "1", "5", "execute"))
    (evento,) = eventos
    assert (evento.event_type, evento.payload["status"], evento.payload["code"]) == (
        "command_completed", "failed", "comando_fallo")
    assert estados == [("hyde", "idle", "1", "5")]


def test_si_falla_publicar_despues_de_un_resultado_valido_no_se_marca_fallo(misiones, monkeypatch):
    """Fix round 1 (R7): solo la ejecucion marca fallo; un publish roto despues
    de un resultado valido no convierte la tarea en failed."""
    tmp, _ = misiones
    monkeypatch.setattr(command_mod, "JAX_BIN", _binario(tmp, 'printf hecho > "$(dirname "$2")/$(basename "$2" .md)_result.md"'))

    async def roto(evento):
        raise RuntimeError("bus caido")

    monkeypatch.setattr(command_mod.event_bus, "publish", roto)
    tid = "66666666-ffff-4fff-8fff-000000000006"
    mision = tmp / f"web-task-{tid}.md"
    mision.write_text("x\n")
    (tmp / f"web-task-{tid}_owner.json").write_text(json.dumps({"tenant_id": "1", "user_id": "5"}))
    with pytest.raises(RuntimeError):
        asyncio.run(command_mod._run_command(tid, mision, tmp / f"web-task-{tid}_result.md", "1", "5", "execute"))
    assert "fallo" not in json.loads((tmp / f"web-task-{tid}_owner.json").read_text())
    assert asyncio.run(command_mod.get_command_result(tid, user=USUARIO)) == {"status": "completed", "result": "hecho"}


def test_dry_run_por_get_tambien_es_simulado(misiones):
    """Fix round 1 (R8): al recargar, GET no presenta un dry run como resultado real."""
    tmp, eventos = misiones
    tid, _, _ = _correr(tmp, eventos, "dry_run")
    assert asyncio.run(command_mod.get_command_result(tid, user=USUARIO)) == {
        "status": "completed", "result": "---\nfaceta: hyde\n---\n\nlistar\n", "code": "comando_simulado"}


# Ronda final M5 (2026-09-16): command.py leia y escribia el archivo de dueño,
# la mision y el resultado DENTRO de async def (bloqueante en el loop). Se
# registra en que hilo corre cada operacion de disco sobre MISSIONS_DIR
# mientras corren los handlers: ninguna puede ser del hilo del loop.
@pytest.fixture
def disco_en_el_loop(misiones, monkeypatch):
    import os as os_mod
    import pathlib
    import threading

    tmp, _ = misiones
    activo, en_el_loop = [False], []

    def vigilar(nombre, original, ruta_de):
        def envoltura(*a, **k):
            ruta = str(ruta_de(*a))
            if activo[0] and ruta.startswith(str(tmp)) and threading.current_thread() is threading.main_thread():
                en_el_loop.append((nombre, os_mod.path.basename(ruta)))
            return original(*a, **k)
        return envoltura

    for nombre in ("read_text", "write_text", "exists", "mkdir"):
        monkeypatch.setattr(pathlib.Path, nombre, vigilar(nombre, getattr(pathlib.Path, nombre), lambda p, *r: p))
    monkeypatch.setattr(command_mod.os, "replace", vigilar("os.replace", os_mod.replace, lambda a, *r: a))

    def correr(corutina):
        activo[0] = True
        try:
            return asyncio.run(corutina)
        finally:
            activo[0] = False
    return tmp, correr, en_el_loop


def test_crear_y_consultar_un_comando_no_tocan_el_disco_en_el_loop(disco_en_el_loop, monkeypatch):
    tmp, correr, en_el_loop = disco_en_el_loop

    async def sin_correr(*a, **k):
        return None

    monkeypatch.setattr(command_mod, "_run_command", sin_correr)
    tid = correr(command_mod.create_command(command_mod.CommandRequest(command="x"), user=USUARIO))["task_id"]
    assert correr(command_mod.get_command_result(tid, user=USUARIO)) == {"status": "running"}
    (tmp / f"web-task-{tid}_result.md").write_text("hecho")
    assert correr(command_mod.get_command_result(tid, user=USUARIO)) == {"status": "completed", "result": "hecho"}
    assert en_el_loop == []


@pytest.mark.parametrize("modo, cuerpo", [
    ("dry_run", None),
    ("execute", 'printf hecho > "$(dirname "$2")/$(basename "$2" .md)_result.md"'),
    ("execute", "exit 0"),
    ("execute", "__no_existe__"),
])
def test_correr_un_comando_no_toca_el_disco_en_el_loop(disco_en_el_loop, monkeypatch, modo, cuerpo):
    tmp, correr, en_el_loop = disco_en_el_loop
    if cuerpo == "__no_existe__":
        monkeypatch.setattr(command_mod, "JAX_BIN", tmp / "no-existe")
    elif cuerpo:
        monkeypatch.setattr(command_mod, "JAX_BIN", _binario(tmp, cuerpo))
    tid = "77777777-aaaa-4aaa-8aaa-000000000007"
    mision = tmp / f"web-task-{tid}.md"
    mision.write_text("x\n")
    (tmp / f"web-task-{tid}_owner.json").write_text(json.dumps({"tenant_id": "1", "user_id": "5"}))
    correr(command_mod._run_command(tid, mision, tmp / f"web-task-{tid}_result.md", "1", "5", modo))
    assert en_el_loop == []
    esperado = {"dry_run": "comando_simulado", "__no_existe__": "comando_fallo", "exit 0": "comando_sin_resultado"}
    consulta = asyncio.run(command_mod.get_command_result(tid, user=USUARIO))
    assert consulta.get("code") == esperado.get(modo if modo == "dry_run" else cuerpo)
