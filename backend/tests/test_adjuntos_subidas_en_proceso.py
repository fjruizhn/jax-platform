"""Tope de subidas pesadas en proceso a la vez (revisión final, 2026-09-17).

Medido en staging (final-fix-report.md): /api/health con 5 VUs tenía p95 0,3
ms solo, 65 ms junto a upload_imagen_max c=25 y 244 ms junto a upload_pdf_max
c=25. El trabajo de /api/chat/upload corre en asyncio.to_thread, pero
b64encode y pypdf retienen el GIL: 25 hilos a la vez se lo disputan al event
loop. JAX_ADJUNTO_SUBIDAS_EN_PROCESO limita cuántas subidas hacen ese trabajo
a la vez; sin default (fail-closed), como los otros límites."""
import asyncio
import io
import threading
import time

import pytest
from starlette.datastructures import Headers, UploadFile

import api.upload as upload_mod
from auth.models import AuthUser
from tests.adjuntos_muestras import PNG, pdf_con_texto

_USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")


def _archivo(datos, nombre, mime):
    return UploadFile(io.BytesIO(datos), filename=nombre, headers=Headers({"content-type": mime}))


def _espiar_trabajo(monkeypatch, nombre):
    """Cuenta cuántos hilos ejecutan a la vez la función pesada `nombre`."""
    candado = threading.Lock()
    estado = {"activos": 0, "maximo": 0}
    original = getattr(upload_mod, nombre)

    def espia(*a, **k):
        with candado:
            estado["activos"] += 1
            estado["maximo"] = max(estado["maximo"], estado["activos"])
        try:
            time.sleep(0.05)
            return original(*a, **k)
        finally:
            with candado:
                estado["activos"] -= 1

    monkeypatch.setattr(upload_mod, nombre, espia)
    return estado


def _espiar_trabajo_async(monkeypatch, nombre):
    """Igual que `_espiar_trabajo`, pero para una función pesada que ahora es
    una corutina (RD1, 2026-09-17: `extraer_texto_en_pool` ya no corre pypdf
    en un hilo del proceso web -- lo manda al ProcessPoolExecutor de
    adjuntos/pdf_pool.py -- así que ya no hay hilos reales del proceso web
    que contar; el conteo de "cuántas a la vez" se hace en concurrencia de
    corutinas del event loop, que es exactamente lo que sigue acotando
    turno_de_subida)."""
    estado = {"activos": 0, "maximo": 0}
    original = getattr(upload_mod, nombre)

    async def espia(*a, **k):
        estado["activos"] += 1
        estado["maximo"] = max(estado["maximo"], estado["activos"])
        try:
            await asyncio.sleep(0.05)
            return await original(*a, **k)
        finally:
            estado["activos"] -= 1

    monkeypatch.setattr(upload_mod, nombre, espia)
    return estado


def _subir_a_la_vez(archivos):
    async def correr():
        return await asyncio.gather(*(upload_mod.upload_file(file=f(), user=_USUARIO) for f in archivos))
    return asyncio.run(correr())


@pytest.mark.parametrize("tope", [1, 2])
def test_las_subidas_de_imagen_respetan_el_tope(monkeypatch, tope):
    monkeypatch.setenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", str(tope))
    estado = _espiar_trabajo(monkeypatch, "clasificar_archivo")
    resultados = _subir_a_la_vez([lambda: _archivo(PNG, "f.png", "image/png")] * 5)
    assert estado["maximo"] == tope
    assert all(r["tipo"] == "imagen" and r["bytes"] == len(PNG) for r in resultados)


@pytest.mark.parametrize("tope", [1, 2])
def test_las_subidas_de_pdf_respetan_el_tope_tambien_en_pypdf(monkeypatch, tope):
    monkeypatch.setenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", str(tope))
    estado = _espiar_trabajo_async(monkeypatch, "extraer_texto_en_pool")
    pdf = pdf_con_texto(["hola"])
    resultados = _subir_a_la_vez([lambda: _archivo(pdf, "i.pdf", "application/pdf")] * 5)
    assert estado["maximo"] == tope
    assert all(r["origen"] == "pdf" and "hola" in r["vista_previa"] for r in resultados)


def test_el_turno_se_libera_si_la_subida_falla(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", "1")
    malo = b"%PDF-roto"

    async def correr():
        for _ in range(3):   # con el turno retenido, la segunda quedaría colgada
            with pytest.raises(Exception) as e:
                await asyncio.wait_for(upload_mod.upload_file(file=_archivo(malo, "m.pdf", "application/pdf"),
                                                              user=_USUARIO), 5)
            assert getattr(e.value, "status_code", None) == 422
    asyncio.run(correr())


def test_sin_tope_configurado_la_subida_falla_cerrado(monkeypatch):
    from adjuntos.limites import LimitesDeAdjuntosInvalidos
    monkeypatch.delenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", raising=False)
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        _subir_a_la_vez([lambda: _archivo(PNG, "f.png", "image/png")])
