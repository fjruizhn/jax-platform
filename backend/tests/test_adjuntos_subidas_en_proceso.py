"""Tope de subidas pesadas en proceso a la vez (revisión final, 2026-09-17).

Medido en staging (final-fix-report.md): /api/health con 5 VUs tenía p95 0,3
ms solo, 65 ms junto a upload_imagen_max c=25 y 244 ms junto a upload_pdf_max
c=25. JAX_ADJUNTO_SUBIDAS_EN_PROCESO limita cuántas subidas clasifican a la
vez (en un hilo, reteniendo el GIL por bloque); sin default (fail-closed),
como los otros límites. pypdf ya corre en otro proceso (RD1) y, desde el Final
fix wave #2 (I1), fuera de este turno: lo acota el turno de pdf
(JAX_ADJUNTO_PDF_PROCESOS)."""
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


def _espiar_envios_al_pool(monkeypatch):
    """Cuántas extracciones están ENVIADAS al ProcessPoolExecutor a la vez
    (submit hecho, future sin terminar). Final fix wave #2 (I1): eso es lo que
    acota el turno de pdf; lo que corre de verdad ya lo acota el pool."""
    import adjuntos.pdf_pool as pdf_pool
    # El done-callback corre en el hilo de gestión del executor, el submit en
    # el del loop: el contador se toca bajo candado (R34, minor del re-review).
    candado = threading.Lock()
    estado = {"activos": 0, "maximo": 0}
    pool = pdf_pool.crear_pool()
    original = pool.submit

    def espia(*a, **k):
        futuro = original(*a, **k)
        with candado:
            estado["activos"] += 1
            estado["maximo"] = max(estado["maximo"], estado["activos"])

        def listo(_):
            with candado:
                estado["activos"] -= 1
        futuro.add_done_callback(listo)
        return futuro

    monkeypatch.setattr(pool, "submit", espia)
    return estado


@pytest.fixture
def _pool_de_pdf_limpio():
    import adjuntos.pdf_pool as pdf_pool
    pdf_pool._cerrado = False
    yield
    asyncio.run(pdf_pool.cerrar_pool())
    pdf_pool._cerrado = False


@pytest.mark.parametrize("subidas,procesos", [(1, 2), (2, 1)])
def test_las_subidas_de_pdf_respetan_el_tope_al_clasificar_y_el_de_procesos_en_pypdf(
        monkeypatch, _pool_de_pdf_limpio, subidas, procesos):
    """Final fix wave #2 (I1): la clasificación sigue dentro de
    turno_de_subida (JAX_ADJUNTO_SUBIDAS_EN_PROCESO); pypdf ya no, lo acota el
    turno de pdf (JAX_ADJUNTO_PDF_PROCESOS) antes del submit. Antes, con
    subidas=1 y procesos=2 solo había 1 envío a la vez (el turno de subida
    retenido durante pypdf), y con subidas=2 y procesos=1 había 2 envíos: el
    segundo esperaba dentro del pool con su timeout corriendo."""
    import functools
    import adjuntos.pdf_pool as pdf_pool
    from tests import adjuntos_pdf_pool_fixtures as fixtures
    monkeypatch.setenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", str(subidas))
    monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", str(procesos))
    monkeypatch.setattr(pdf_pool, "extraer_texto", functools.partial(fixtures.trabajar, 0.4))
    clasificando = _espiar_trabajo(monkeypatch, "clasificar_archivo")
    envios = _espiar_envios_al_pool(monkeypatch)
    pdf = pdf_con_texto(["hola"])
    resultados = _subir_a_la_vez([lambda: _archivo(pdf, "i.pdf", "application/pdf")] * 5)
    assert clasificando["maximo"] == subidas
    assert envios["maximo"] == procesos
    assert all(r["origen"] == "pdf" and r["vista_previa"].startswith("ok") for r in resultados)


def test_un_pdf_lento_no_frena_una_subida_de_imagen_ya_clasificado(monkeypatch, _pool_de_pdf_limpio):
    """Final fix wave #2 (I1): con JAX_ADJUNTO_SUBIDAS_EN_PROCESO=1, un PDF que
    tarda en pypdf ya soltó el turno de subida al terminar de clasificarse.
    Antes lo retenía durante toda la extracción y una imagen esperaba detrás."""
    import functools
    import adjuntos.pdf_pool as pdf_pool
    from tests import adjuntos_pdf_pool_fixtures as fixtures
    monkeypatch.setenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", "1")
    monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", "1")
    monkeypatch.setattr(pdf_pool, "extraer_texto", functools.partial(fixtures.trabajar, 2.0))
    envios = _espiar_envios_al_pool(monkeypatch)
    pdf = pdf_con_texto(["hola"])

    async def correr():
        lento = asyncio.ensure_future(upload_mod.upload_file(
            file=_archivo(pdf, "i.pdf", "application/pdf"), user=_USUARIO))
        limite = time.monotonic() + 5
        while envios["activos"] == 0:
            assert time.monotonic() < limite, "el PDF nunca llegó al pool"
            await asyncio.sleep(0.01)
        inicio = time.monotonic()
        imagen = await upload_mod.upload_file(file=_archivo(PNG, "f.png", "image/png"), user=_USUARIO)
        demora = time.monotonic() - inicio
        assert not lento.done()
        return imagen, demora, await lento

    imagen, demora, pdf_hecho = asyncio.run(correr())
    assert imagen["tipo"] == "imagen"
    assert demora < 1.0, f"la imagen esperó {demora:.2f} s detrás del PDF"
    assert pdf_hecho["origen"] == "pdf"


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


def test_con_el_pool_saturado_la_subida_de_pdf_sale_503_sin_temporal_ni_reserva(
        monkeypatch, tmp_path, _pool_de_pdf_limpio):
    """Ruling R34: N workers ocupados más allá del plazo. La subida siguiente
    espera el turno de pdf a lo sumo JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS y recibe
    503 `adjuntos_reintentar`: sin temporal, sin reserva de cuota retenida
    (`_cuentas == {}`) y con el semáforo exactamente en N. Cuando los workers
    se liberan, un PDF nuevo entra."""
    import functools
    from fastapi import HTTPException
    import adjuntos.pdf_pool as pdf_pool
    from adjuntos import almacen, cuota
    from adjuntos.turno import turno_de_pdf
    from tests import adjuntos_pdf_pool_fixtures as fixtures
    directorio = tmp_path / "adjuntos"
    directorio.mkdir(mode=0o700)
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(directorio))
    monkeypatch.setenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", "4")
    monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", "2")
    monkeypatch.setenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", "10")
    monkeypatch.setattr(pdf_pool, "extraer_texto", functools.partial(fixtures.trabajar, 2.5))
    cuota._cuentas.clear()
    pdf = pdf_con_texto(["hola"])

    def subir():
        return upload_mod.upload_file(file=_archivo(pdf, "i.pdf", "application/pdf"), user=_USUARIO)

    async def correr():
        await pdf_pool._pool_actual()
        await pdf_pool.precalentar_pool()
        ocupadas = [asyncio.ensure_future(subir()) for _ in range(2)]
        limite = time.monotonic() + 5
        while turno_de_pdf()._value > 0:
            assert time.monotonic() < limite, "los PDFs nunca llegaron al pool"
            await asyncio.sleep(0.01)
        monkeypatch.setenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", "1")
        inicio = time.monotonic()
        with pytest.raises(HTTPException) as e:
            await asyncio.wait_for(subir(), 5)
        demora = time.monotonic() - inicio
        assert (e.value.status_code, e.value.detail) == (503, {"code": "adjuntos_reintentar"})
        assert 0.9 <= demora < 1.8, demora
        # Solo quedan los temporales y las reservas de las dos que corren.
        temporales = [p for p in directorio.rglob("*") if p.name.startswith(almacen.PREFIJO_SUBIDA)]
        assert len(temporales) == 2, temporales
        cuenta = cuota._cuentas["5"]
        assert (cuenta.en_uso, cuenta.reservado) == (2, 2 * len(pdf))
        monkeypatch.setenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", "10")
        hechas = await asyncio.gather(*ocupadas)
        assert all(r["origen"] == "pdf" for r in hechas)
        assert turno_de_pdf()._value == 2
        assert cuota._cuentas == {}
        assert not [p for p in directorio.rglob("*") if p.name.startswith(almacen.PREFIJO_SUBIDA)]
        nueva = await subir()
        assert nueva["origen"] == "pdf"
        assert turno_de_pdf()._value == 2

    asyncio.run(correr())
    assert cuota._cuentas == {}
    assert not [p for p in directorio.rglob("*") if p.name.startswith(almacen.PREFIJO_SUBIDA)]
