"""POST /api/chat/upload (frente D, 2026-09-16). Pide `client`: en el job sin
DB se salta (Regla 1 de conftest)."""
import base64

from tests.adjuntos_muestras import PNG, pdf_con_texto
from tests.identidades import cabeceras


def _subir(client, contenido, nombre="x", tipo="application/octet-stream", headers=None):
    return client.post(
        "/api/chat/upload",
        files={"file": (nombre, contenido, tipo)},
        headers=headers if headers is not None else cabeceras(client, "test-adjuntos-upload"),
    )


def test_imagen_png_por_bytes_aunque_el_cliente_diga_texto(client):
    r = _subir(client, PNG, "foto.png", "text/plain")
    assert r.status_code == 200, r.text
    assert r.json() == {"tipo": "imagen", "nombre": "foto.png", "mime": "image/png",
                        "bytes": len(PNG), "base64": base64.b64encode(PNG).decode()}


def test_binario_que_dice_ser_png_es_415(client):
    r = _subir(client, b"MZ\x90\x00\x03\x00\x00\x00", "foto.png", "image/png")
    assert r.status_code == 415
    assert r.json()["detail"] == {"code": "adjunto_tipo_no_permitido"}


def test_demasiado_grande_es_413_con_el_maximo(client, monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_MAX_BYTES", "16")
    r = _subir(client, b"a" * 17, "largo.txt", "text/plain")
    assert r.status_code == 413
    assert r.json()["detail"] == {"code": "adjunto_demasiado_grande", "max_bytes": 16}


def test_texto_utf8_recortado_por_caracteres(client, monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_MAX_CHARS", "5")
    r = _subir(client, "ñandú y más".encode(), "notas.md", "text/markdown")
    assert r.status_code == 200, r.text
    assert r.json() == {"tipo": "texto", "origen": "texto", "nombre": "notas.md",
                        "bytes": len("ñandú y más".encode()), "contenido": "ñandú", "recortado": True}


def test_pdf_devuelve_su_texto(client):
    r = _subir(client, pdf_con_texto(["informe trimestral"]), "i.pdf", "application/pdf")
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert (cuerpo["tipo"], cuerpo["origen"], cuerpo["recortado"]) == ("texto", "pdf", False)
    assert "informe trimestral" in cuerpo["contenido"]


def test_pdf_ilegible_es_422_con_codigo(client):
    r = _subir(client, b"%PDF-1.4 basura" * 5, "roto.pdf", "application/pdf")
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "pdf_ilegible"}


def test_pdf_sin_texto_es_422_con_codigo(client):
    r = _subir(client, pdf_con_texto([""]), "escaneo.pdf", "application/pdf")
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "pdf_sin_texto"}


def test_vacio_es_422(client):
    r = _subir(client, b"", "vacio.txt", "text/plain")
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "adjunto_vacio"}


def test_sin_sesion_es_401(client):
    assert _subir(client, PNG, headers={}).status_code == 401
