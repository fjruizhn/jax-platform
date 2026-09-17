"""Task 15 R12(b) (2026-09-16): GET /api/admin/repo/file con un png de 2 MB a
c=25 subia el p95 de /api/health a 70-90 ms (reposo 0,6 ms), contra el
comentario "un archivo grande no congela el loop". Medido en scratch:
- el costo no es del disco: con cuerpo chico y el mismo trabajo en el hilo el
  congelamiento sigue (read + base64 + armar el str retienen el GIL);
- 25 hilos disputando el GIL hacen convoy contra el loop: con la lectura de a
  UNA (cupo por loop) la sonda queda en 2,7-3,9x el reposo;
- ademas FastAPI serializaba en el LOOP un JSON de 2,7 MB: ahora el cuerpo se
  arma en el hilo y sale como bytes (p95 del endpoint 183-205 -> 84-109 ms).
El contrato HTTP no cambia: el mismo JSON con `data:<mime>;base64,...`.
Puros: llaman al handler directo sobre un tmp_path."""
import asyncio
import base64
import json
import threading
import time

import pytest
from fastapi import HTTPException
from starlette.responses import Response

import api.admin.repository as repo
from auth.models import AuthUser

ADMIN = AuthUser(user_id="1", tenant_id="1", role="superadmin")


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    for carpeta in repo.ALLOWED_FOLDERS:
        (base / carpeta).mkdir(parents=True)
    monkeypatch.setattr(repo, "REPO_BASE", base)
    return base


def _pedir(ruta):
    resp = asyncio.run(repo.get_file(path=ruta, user=ADMIN))
    assert isinstance(resp, Response), type(resp)
    assert resp.media_type == "application/json"
    return json.loads(resp.body)


def test_imagen_el_cuerpo_sale_armado_en_bytes_con_el_mismo_contrato(raiz):
    datos = bytes(range(256)) * 3000  # ~768 KB: mas de un trozo de codificacion
    (raiz / "images" / 'raro "ñ".png').write_bytes(datos)
    assert _pedir('images/raro "ñ".png') == {
        "name": 'raro "ñ".png', "type": "image",
        "base64": f"data:image/png;base64,{base64.b64encode(datos).decode()}"}


def test_imagen_vacia(raiz):
    (raiz / "images" / "v.gif").write_bytes(b"")
    assert _pedir("images/v.gif")["base64"] == "data:image/gif;base64,"


def test_texto_y_markdown_con_el_mismo_contrato(raiz):
    (raiz / "documents" / "nota.md").write_text('# hola "ñ€"\n\\ fin', encoding="utf-8")
    (raiz / "documents" / "nota.txt").write_bytes(b"ok \xff")
    assert _pedir("documents/nota.md") == {"name": "nota.md", "type": "markdown", "content": '# hola "ñ€"\n\\ fin'}
    assert _pedir("documents/nota.txt") == {"name": "nota.txt", "type": "text", "content": "ok �"}


def _medir_concurrencia(monkeypatch, raiz, n=5):
    (raiz / "documents" / "a.md").write_text("x")
    activos, pico, cerrojo = [0], [0], threading.Lock()

    def lento(destino):
        with cerrojo:
            activos[0] += 1
            pico[0] = max(pico[0], activos[0])
        time.sleep(0.03)
        with cerrojo:
            activos[0] -= 1
        return b"{}"

    monkeypatch.setattr(repo, "_leer", lento)

    async def lote():
        await asyncio.gather(*(repo.get_file(path="documents/a.md", user=ADMIN) for _ in range(n)))
    return lote, pico


def test_la_lectura_de_archivos_va_de_a_una(monkeypatch, raiz):
    lote, pico = _medir_concurrencia(monkeypatch, raiz)
    asyncio.run(lote())
    assert pico[0] == 1


def test_el_cupo_sirve_en_loops_distintos(monkeypatch, raiz):
    """Un asyncio.Semaphore de modulo queda atado al primer loop que lo
    disputa; el portal de tests y uvicorn usan loops distintos."""
    lote, pico = _medir_concurrencia(monkeypatch, raiz)
    asyncio.run(lote())
    asyncio.run(lote())
    assert pico[0] == 1


def test_una_ruta_invalida_no_espera_el_cupo(monkeypatch, raiz):
    """Ronda final (2026-09-16): antes media `espera < 0.03` s, fragil en un
    runner compartido. Ahora sin reloj: la lectura que tiene el cupo queda
    BLOQUEADA hasta que llega el 400; si el 400 hiciera fila detras del cupo,
    llegaria recien cuando la lectura se rinde (timeout) y ya habria terminado."""
    (raiz / "documents" / "a.md").write_text("x")
    adentro, soltar, termino = threading.Event(), threading.Event(), threading.Event()

    def bloqueada(destino):
        adentro.set()
        soltar.wait(timeout=5)
        termino.set()
        return b"{}"

    monkeypatch.setattr(repo, "_leer", bloqueada)

    async def mezcla():
        lectura = asyncio.create_task(repo.get_file(path="documents/a.md", user=ADMIN))
        while not adentro.is_set():
            await asyncio.sleep(0.001)
        try:
            with pytest.raises(HTTPException) as e:
                await repo.get_file(path="otra/x.md", user=ADMIN)
            assert e.value.status_code == 400
            return termino.is_set()
        finally:
            soltar.set()
            await lectura
    assert asyncio.run(mezcla()) is False
