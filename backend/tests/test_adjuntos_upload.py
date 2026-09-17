"""POST /api/chat/upload (frente D, 2026-09-16; RD2 2026-09-17: por
referencia). El archivo se copia en streaming a JAX_ADJUNTOS_DIR, se
clasifica por bytes y la respuesta es un JSON chico con un id -- nunca
base64 ni el texto completo. Pide `client`: en el job sin DB se salta
(Regla 1 de conftest), salvo los tests que llaman a upload_file directo."""
import asyncio
import io
import json
import os
import stat
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

import api.upload as upload_mod
from adjuntos import almacen
from auth.models import AuthUser
from tests.adjuntos_muestras import PNG, pdf_con_texto
from tests.identidades import cabeceras

USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")


@pytest.fixture
def directorio(tmp_path, monkeypatch):
    d = tmp_path / "adjuntos"
    d.mkdir(mode=0o700)
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(d))
    return d


def _subir(client, contenido, nombre="x", tipo="application/octet-stream", headers=None):
    return client.post(
        "/api/chat/upload",
        files={"file": (nombre, contenido, tipo)},
        headers=headers if headers is not None else cabeceras(client, "test-adjuntos-upload"),
    )


def _archivo(datos, nombre="f", mime="application/octet-stream"):
    return UploadFile(io.BytesIO(datos), filename=nombre, headers=Headers({"content-type": mime}))


def _directo(datos, nombre="f", user=USUARIO):
    return asyncio.run(upload_mod.upload_file(file=_archivo(datos, nombre), user=user))


def _rechazo_directo(datos, nombre="f"):
    try:
        _directo(datos, nombre)
    except HTTPException as e:
        return e
    raise AssertionError("se esperaba HTTPException")


def _archivos(directorio: Path):
    # RD7: los archivos viven en la carpeta de cada usuario.
    return sorted(p.name for p in directorio.rglob("*") if p.is_file())


# ------------------------------------------------------------ por HTTP (DB)

def test_imagen_png_por_bytes_aunque_el_cliente_diga_texto(client):
    r = _subir(client, PNG, "foto.png", "text/plain")
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert almacen.id_valido(cuerpo.pop("id"))
    assert cuerpo == {"tipo": "imagen", "nombre": "foto.png", "mime": "image/png", "bytes": len(PNG)}


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
    cuerpo = r.json()
    assert almacen.id_valido(cuerpo.pop("id"))
    assert cuerpo == {"tipo": "texto", "origen": "texto", "nombre": "notas.md",
                      "bytes": len("ñandú y más".encode()), "caracteres": 5, "recortado": True,
                      "vista_previa": "ñandú"}


def test_pdf_devuelve_una_vista_previa_de_su_texto(client):
    r = _subir(client, pdf_con_texto(["informe trimestral"]), "i.pdf", "application/pdf")
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert (cuerpo["tipo"], cuerpo["origen"], cuerpo["recortado"]) == ("texto", "pdf", False)
    assert "informe trimestral" in cuerpo["vista_previa"]
    assert "contenido" not in cuerpo and "base64" not in cuerpo


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


# ------------------------------------------------- directo (sin DB): almacén

def test_la_imagen_queda_en_disco_atada_al_duenio(directorio):
    r = _directo(PNG, "foto.png")
    assert r == {"id": r["id"], "tipo": "imagen", "nombre": "foto.png", "mime": "image/png", "bytes": len(PNG)}
    assert _archivos(directorio) == sorted([f"{r['id']}.dato", f"{r['id']}.json"])
    meta, datos = asyncio.run(almacen.leer(r["id"], USUARIO))
    assert datos == PNG
    assert (meta["user_id"], meta["tenant_id"], meta["tipo"], meta["nombre"]) == ("5", "1", "imagen", "foto.png")
    for p in directorio.rglob("*"):
        assert stat.S_IMODE(p.stat().st_mode) == (0o700 if p.is_dir() else 0o600)
    assert [p.name for p in directorio.iterdir()] == ["5"]


def test_el_texto_guardado_es_el_extraido_y_la_respuesta_esta_acotada(directorio, monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_MAX_CHARS", "1000")
    texto = "línea\n" * 400  # 2400 caracteres
    r = _directo(texto.encode(), "n.txt")
    assert (r["caracteres"], r["recortado"]) == (1000, True)
    assert r["vista_previa"] == texto[:upload_mod.VISTA_PREVIA_CARACTERES]
    assert len(json.dumps(r)) < 600
    _, datos = asyncio.run(almacen.leer(r["id"], USUARIO))
    assert datos.decode() == texto[:1000]


def test_del_pdf_se_guarda_solo_el_texto(directorio):
    r = _directo(pdf_con_texto(["informe trimestral"]), "i.pdf")
    _, datos = asyncio.run(almacen.leer(r["id"], USUARIO))
    assert b"%PDF" not in datos and "informe trimestral" in datos.decode()
    assert len(_archivos(directorio)) == 2


def test_el_pdf_viaja_al_pool_como_ruta_no_como_bytes(directorio, monkeypatch):
    vistos = []
    original = upload_mod.extraer_texto_en_pool

    async def espia(origen, max_paginas, max_chars):
        vistos.append(origen)
        return await original(origen, max_paginas, max_chars)

    monkeypatch.setattr(upload_mod, "extraer_texto_en_pool", espia)
    _directo(pdf_con_texto(["hola"]), "i.pdf")
    (origen,) = vistos
    assert isinstance(origen, str) and Path(origen).parent == directorio / USUARIO.user_id
    assert Path(origen).name.startswith(almacen.PREFIJO_SUBIDA)


def test_el_cuerpo_no_se_lee_entero_de_una_vez(directorio, monkeypatch):
    """La copia va por bloques de <= 1 MB en un hilo, nunca un read() sin
    tamaño ni uno de max_bytes + 1."""
    tamanos = []

    class Espia(io.BytesIO):
        def read(self, n=-1):
            tamanos.append(n)
            return super().read(n)

    archivo = UploadFile(Espia(b"a" * (3 * 1024 * 1024)), filename="t.txt")
    asyncio.run(upload_mod.upload_file(file=archivo, user=USUARIO))
    assert tamanos and all(0 < n <= 1024 * 1024 for n in tamanos)


def test_413_en_streaming_no_deja_archivos(directorio, monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_MAX_BYTES", str(2 * 1024 * 1024))
    e = _rechazo_directo(b"a" * (5 * 1024 * 1024), "grande.txt")
    assert (e.status_code, e.detail) == (413, {"code": "adjunto_demasiado_grande", "max_bytes": 2 * 1024 * 1024})
    assert _archivos(directorio) == []


@pytest.mark.parametrize("datos,status,code", [
    (b"MZ\x90\x00\x03", 415, "adjunto_tipo_no_permitido"),
    (b"", 422, "adjunto_vacio"),
    (b"%PDF-1.4 basura" * 5, 422, "pdf_ilegible"),
    (pdf_con_texto([""]), 422, "pdf_sin_texto"),
    (b"a" * 100 + b"\xff", 415, "adjunto_tipo_no_permitido"),
])
def test_ningun_rechazo_deja_archivos(directorio, datos, status, code):
    e = _rechazo_directo(datos)
    assert (e.status_code, e.detail) == (status, {"code": code})
    assert _archivos(directorio) == []


def test_si_guardar_falla_no_quedan_archivos(directorio, monkeypatch):
    def rompe(*a, **k):
        raise OSError("disco lleno")

    monkeypatch.setattr(almacen, "_fsync_directorio", rompe)
    with pytest.raises(OSError):
        _directo(PNG, "f.png")
    assert _archivos(directorio) == []


def test_dos_subidas_concurrentes_dan_ids_distintos_y_archivos_correctos(directorio, monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", "2")
    otro = AuthUser(user_id="6", tenant_id="1", role="operator")
    png_b = PNG + b"-segunda"

    async def correr():
        return await asyncio.gather(
            upload_mod.upload_file(file=_archivo(PNG, "a.png"), user=USUARIO),
            upload_mod.upload_file(file=_archivo(png_b, "b.png"), user=otro))

    a, b = asyncio.run(correr())
    assert a["id"] != b["id"]
    assert asyncio.run(almacen.leer(a["id"], USUARIO))[1] == PNG
    assert asyncio.run(almacen.leer(b["id"], otro))[1] == png_b
    assert len(_archivos(directorio)) == 4
    # El de uno no es del otro.
    with pytest.raises(almacen.AdjuntoNoEncontrado):
        asyncio.run(almacen.obtener(a["id"], otro))


def test_sin_directorio_configurado_la_subida_falla_cerrado(monkeypatch):
    from adjuntos.limites import LimitesDeAdjuntosInvalidos
    monkeypatch.delenv("JAX_ADJUNTOS_DIR", raising=False)
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        _directo(PNG)


def test_una_subida_cancelada_no_deja_el_temporal(directorio, monkeypatch):
    """Cancelada mientras espera el turno (el cliente se fue): el temporal ya
    copiado se borra igual."""
    monkeypatch.setenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", "1")

    async def correr():
        from adjuntos.turno import turno_de_subida
        turno = turno_de_subida()
        await turno.acquire()  # ocupado: la subida queda esperando
        tarea = asyncio.create_task(upload_mod.upload_file(file=_archivo(PNG, "f.png"), user=USUARIO))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if any(n.startswith(almacen.PREFIJO_SUBIDA) for n in (p.name for p in directorio.rglob("*"))):
                break
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        turno.release()

    asyncio.run(correr())
    assert _archivos(directorio) == []
