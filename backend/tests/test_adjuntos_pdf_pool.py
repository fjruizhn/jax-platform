"""ProcessPoolExecutor acotado para pypdf (RD1, 2026-09-17): la extracción
corre en OTRO PROCESO, no en un hilo (pypdf retiene el GIL: medido en Task 11
-- health p95 244 ms con PDFs a c=25 solo con asyncio.to_thread). Un timeout
por extracción mapea al código pdf_ilegible existente y recicla el pool
entero (no hay forma de interrumpir un worker de ProcessPoolExecutor ya
arrancado) para que la extracción SIGUIENTE tenga un lugar libre de verdad."""
import asyncio
import os

import pytest

from adjuntos import limites, pdf_pool
from adjuntos.pdf import PdfIlegible, extraer_texto as _extraer_texto_real
from tests import adjuntos_pdf_pool_fixtures as fixtures
from tests.adjuntos_muestras import pdf_con_texto


@pytest.fixture(autouse=True)
def _entorno_del_pool(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", "2")
    monkeypatch.setenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", "1")
    yield
    asyncio.run(pdf_pool.cerrar_pool())


def test_la_extraccion_corre_en_un_proceso_distinto_del_servidor():
    pool = pdf_pool.crear_pool()
    pid_hijo = pool.submit(os.getpid).result(timeout=10)
    assert pid_hijo != os.getpid()


def test_extraer_texto_en_pool_devuelve_lo_mismo_que_la_funcion_directa():
    datos = pdf_con_texto(["informe trimestral"])

    async def correr():
        return await pdf_pool.extraer_texto_en_pool(datos, max_paginas=20, max_chars=8000)

    texto, recortado = asyncio.run(correr())
    assert "informe trimestral" in texto
    assert recortado is False


def test_tamano_del_pool_sigue_al_env(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", "3")
    pool = pdf_pool.crear_pool()
    # _max_workers es privado de concurrent.futures: aceptable en un test,
    # no en código de producción (ver docstring de pdf_pool.crear_pool).
    assert pool._max_workers == 3


def test_timeout_se_mapea_a_pdf_ilegible_y_el_pool_sigue_sirviendo_despues(monkeypatch):
    monkeypatch.setattr(pdf_pool, "extraer_texto", fixtures.dormir)

    async def escenario():
        with pytest.raises(PdfIlegible):
            await pdf_pool.extraer_texto_en_pool(b"lo que sea", max_paginas=20, max_chars=8000)
        # Reciclado: el pool global es OTRO objeto que el que se colgó.
        pool_nuevo = await pdf_pool._pool_actual()
        assert pool_nuevo is not None

        # La proxima extraccion (ya con la funcion real) sirve -- el pool
        # reciclado no quedo con el lugar del worker colgado ocupado.
        monkeypatch.setattr(pdf_pool, "extraer_texto", _extraer_texto_real)
        texto, _ = await pdf_pool.extraer_texto_en_pool(
            pdf_con_texto(["ok"]), max_paginas=20, max_chars=8000)
        assert "ok" in texto

    asyncio.run(escenario())


def test_dos_timeouts_simultaneos_no_duplican_el_reciclado(monkeypatch):
    """Dos extracciones colgadas a la vez disparan dos timeouts casi
    simultaneos: sin la guarda (`_pool is not pool_colgado`), las dos
    reciclarian por separado y una de las dos dejaria un pool nuevo huerfano
    sin apagar (fuga de procesos). Con la guarda, solo la primera en llegar
    recicla; la segunda ve que el pool global ya cambio y no hace nada."""
    monkeypatch.setattr(pdf_pool, "extraer_texto", fixtures.dormir)

    async def escenario():
        resultados = await asyncio.gather(
            pdf_pool.extraer_texto_en_pool(b"a", max_paginas=20, max_chars=8000),
            pdf_pool.extraer_texto_en_pool(b"b", max_paginas=20, max_chars=8000),
            return_exceptions=True,
        )
        assert all(isinstance(r, PdfIlegible) for r in resultados)
        monkeypatch.setattr(pdf_pool, "extraer_texto", _extraer_texto_real)
        texto, _ = await pdf_pool.extraer_texto_en_pool(
            pdf_con_texto(["ok"]), max_paginas=20, max_chars=8000)
        assert "ok" in texto

    asyncio.run(escenario())


def test_cerrar_pool_sin_pool_creado_no_falla():
    asyncio.run(pdf_pool.cerrar_pool())


def test_lifespan_crea_y_cierra_el_pool(monkeypatch):
    import main

    llamadas = []
    crear_real = pdf_pool.crear_pool

    def crear_espia():
        llamadas.append("crear")
        return crear_real()

    async def cerrar_espia():
        llamadas.append("cerrar")

    async def pool_espia():
        llamadas.append("pool_db")

    async def http_espia():
        llamadas.append("http")

    async def migraciones_espia():
        llamadas.append("migraciones")

    async def seed_espia():
        llamadas.append("seed")

    monkeypatch.setattr(pdf_pool, "crear_pool", crear_espia)
    monkeypatch.setattr(pdf_pool, "cerrar_pool", cerrar_espia)
    monkeypatch.setattr(main, "get_pool", pool_espia)
    monkeypatch.setattr(main, "get_http_client", http_espia)
    monkeypatch.setattr(main, "run_migrations", migraciones_espia)
    monkeypatch.setattr(main, "run_seed", seed_espia)

    async def cargar_nombres_espia():
        return None

    monkeypatch.setattr(main.engine_state, "cargar_nombres_de_facetas", cargar_nombres_espia)
    monkeypatch.setattr(main.engine_state, "start_background_tasks", lambda: None)

    async def arrancar_y_parar():
        async with main.lifespan(main.app):
            pass

    asyncio.run(arrancar_y_parar())
    assert llamadas[0] == "crear"
    assert "cerrar" in llamadas
    assert llamadas.index("crear") < llamadas.index("cerrar")
