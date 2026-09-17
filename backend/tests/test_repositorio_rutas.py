"""Frente A (2026-09-16): /api/admin/repo.

A-40 DEFECTO: _safe_path comparaba con `target.startswith(base)` sin
separador, asi que `documents/../../repo-x/secreto.txt` pasaba si existia una
carpeta hermana cuyo nombre empieza con "repo". Leia o BORRABA fuera del
repositorio (superadmin). Ahora Path.resolve() + is_relative_to(base).
A-08: el MIME salia `image/jpeg` para todo lo que no fuera .png.
A-07: POST /repo/save no tenia llamador ni tests (camino de escritura muerto).
A-26/A-31: una sola validacion de ruta, sin parametros por defecto muertos.
Puros: llaman a los handlers directo sobre un tmp_path."""
import asyncio
import base64
import inspect

import pytest
from fastapi import HTTPException

import api.admin.repository as repo
from auth.models import AuthUser

ADMIN = AuthUser(user_id="1", tenant_id="1", role="superadmin")


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    for carpeta in repo.ALLOWED_FOLDERS:
        (base / carpeta).mkdir(parents=True)
    hermana = tmp_path / "repo-x"
    hermana.mkdir()
    (hermana / "secreto.txt").write_text("no deberia salir")
    monkeypatch.setattr(repo, "REPO_BASE", base)
    return base


def _error(corutina) -> HTTPException:
    try:
        asyncio.run(corutina)
    except HTTPException as e:
        return e
    raise AssertionError("se esperaba HTTPException")


def test_leer_una_carpeta_hermana_que_empieza_igual_es_400(raiz):
    e = _error(repo.get_file(path="documents/../../repo-x/secreto.txt", user=ADMIN))
    assert (e.status_code, e.detail) == (400, "ruta_invalida")


def test_borrar_en_una_carpeta_hermana_es_400_y_no_borra(raiz):
    e = _error(repo.delete_file(path="documents/../../repo-x/secreto.txt", user=ADMIN))
    assert (e.status_code, e.detail) == (400, "ruta_invalida")
    assert (raiz.parent / "repo-x" / "secreto.txt").exists()


def test_carpeta_no_permitida_es_400(raiz):
    e = _error(repo.get_file(path="otra/archivo.txt", user=ADMIN))
    assert (e.status_code, e.detail) == (400, "ruta_invalida")


def test_archivo_inexistente_es_404(raiz):
    e = _error(repo.get_file(path="documents/no-existe.md", user=ADMIN))
    assert (e.status_code, e.detail) == (404, "archivo_no_encontrado")


@pytest.mark.parametrize("nombre, mime", [
    ("a.png", "image/png"), ("b.JPG", "image/jpeg"), ("c.gif", "image/gif"),
    ("d.webp", "image/webp"), ("e.svg", "image/svg+xml"),
])
def test_el_mime_de_la_imagen_es_el_real(raiz, nombre, mime):
    (raiz / "images" / nombre).write_bytes(b"\x00\x01")
    datos = asyncio.run(repo.get_file(path=f"images/{nombre}", user=ADMIN))
    assert datos["type"] == "image"
    assert datos["base64"] == f"data:{mime};base64,{base64.b64encode(b'\x00\x01').decode()}"


def test_markdown_y_texto_se_leen_igual_que_antes(raiz):
    (raiz / "documents" / "nota.md").write_text("# hola")
    (raiz / "documents" / "nota.txt").write_text("hola")
    assert asyncio.run(repo.get_file(path="documents/nota.md", user=ADMIN)) == {
        "name": "nota.md", "type": "markdown", "content": "# hola"}
    assert asyncio.run(repo.get_file(path="documents/nota.txt", user=ADMIN))["type"] == "text"


def test_borrar_un_archivo_propio_funciona(raiz):
    (raiz / "missions" / "m.md").write_text("x")
    assert asyncio.run(repo.delete_file(path="missions/m.md", user=ADMIN)) == {"ok": True}
    assert not (raiz / "missions" / "m.md").exists()


def test_save_no_existe():
    assert not any(getattr(r, "path", "") == "/api/admin/repo/save" for r in repo.router.routes)
    assert not hasattr(repo, "SaveFileRequest")


def test_sin_parametros_por_defecto_muertos():
    assert list(inspect.signature(repo._file_info).parameters) == ["path"]
    assert not hasattr(repo, "_safe_path")
