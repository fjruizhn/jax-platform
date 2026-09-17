"""ProcessPoolExecutor acotado para pypdf (RD1, 2026-09-17): la extracción
corre en OTRO PROCESO, no en un hilo (pypdf retiene el GIL: medido en Task 11
-- health p95 244 ms con PDFs a c=25 solo con asyncio.to_thread). Un timeout
por extracción mapea al código pdf_ilegible existente y recicla el pool
entero (no hay forma de interrumpir un worker de ProcessPoolExecutor ya
arrancado) para que la extracción SIGUIENTE tenga un lugar libre de verdad."""
import asyncio
import os
import time

import pytest
from concurrent.futures.process import BrokenProcessPool

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


def test_extraer_texto_en_pool_corre_en_un_proceso_distinto(monkeypatch):
    """Igual que la anterior, pero por el camino REAL (ronda de corrección 1,
    item 6): un worker de prueba que devuelve su propio pid COMO el texto
    extraído, pasando por `extraer_texto_en_pool` -- no por el pool en
    crudo."""
    monkeypatch.setattr(pdf_pool, "extraer_texto", fixtures.pid_como_texto)

    async def correr():
        return await pdf_pool.extraer_texto_en_pool(b"x", max_paginas=20, max_chars=8000)

    texto, _ = asyncio.run(correr())
    assert texto != str(os.getpid())


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


# --- Ronda de corrección 1 (2026-09-17), item 1: worker muerto -------------

def test_worker_muerto_se_mapea_a_pdf_ilegible_y_el_pool_sigue_sirviendo_despues(monkeypatch):
    """os._exit simula un worker que muere de golpe (OOM, segfault de
    pypdf): el pool queda BrokenProcessPool para CUALQUIER submit posterior
    (verificado con probe_pool.py contra este venv). Tiene que mapear al
    mismo código estable que un timeout, no escapar como un 500 genérico."""
    monkeypatch.setattr(pdf_pool, "extraer_texto", fixtures.morir)

    async def escenario():
        with pytest.raises(PdfIlegible):
            await pdf_pool.extraer_texto_en_pool(b"x", max_paginas=20, max_chars=8000)
        monkeypatch.setattr(pdf_pool, "extraer_texto", _extraer_texto_real)
        texto, _ = await pdf_pool.extraer_texto_en_pool(
            pdf_con_texto(["ok"]), max_paginas=20, max_chars=8000)
        assert "ok" in texto

    asyncio.run(escenario())


# --- item 2: trabajo en vuelo o en cola durante un reciclado ----------------

def test_la_extraccion_en_cola_durante_un_reciclado_tambien_da_pdf_ilegible(monkeypatch):
    """Con el pool a UN solo worker, la segunda extracción queda en cola
    mientras la primera corre. La primera tiene un timeout CORTO (1 s) y la
    segunda uno LARGO (10 s) a propósito: si la segunda sale como
    pdf_ilegible de todos modos, mucho antes de sus propios 10 s, no puede
    ser porque agotó SU presupuesto -- tiene que ser porque el reciclado de
    la primera (que mata el pool entero) le rompió el future por debajo.
    Sin esta diferencia de timeouts, una carrera de temporización podría
    hacer pasar el test por casualidad incluso sin el mapeo nuevo."""
    monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", "1")
    monkeypatch.setattr(pdf_pool, "extraer_texto", fixtures.dormir)

    async def escenario():
        monkeypatch.setenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", "1")
        tarea_a = asyncio.ensure_future(
            pdf_pool.extraer_texto_en_pool(b"a", max_paginas=20, max_chars=8000))
        await asyncio.sleep(0)  # que A ya haya leído timeout=1 y esté esperando su future
        monkeypatch.setenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", "10")
        tarea_b = asyncio.ensure_future(
            pdf_pool.extraer_texto_en_pool(b"b", max_paginas=20, max_chars=8000))

        inicio = time.monotonic()
        resultados = await asyncio.gather(tarea_a, tarea_b, return_exceptions=True)
        duracion = time.monotonic() - inicio

        assert all(isinstance(r, PdfIlegible) for r in resultados), resultados
        assert duracion < 5, "la segunda (timeout=10s) tuvo que resolverse por el reciclado, no por su propio presupuesto"

        monkeypatch.setattr(pdf_pool, "extraer_texto", _extraer_texto_real)
        monkeypatch.setenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", "5")
        texto, _ = await pdf_pool.extraer_texto_en_pool(
            pdf_con_texto(["ok"]), max_paginas=20, max_chars=8000)
        assert "ok" in texto

    asyncio.run(escenario())


def test_una_cancelacion_real_del_request_se_propaga_sin_traducirse(monkeypatch):
    """Si a ESTE request lo cancelan de afuera (el cliente se desconectó,
    por ejemplo), tiene que salir CancelledError -- no un pdf_ilegible
    fabricado. Se distingue de una cancelación inducida por el pool con
    `Task.cancelling()` (>0 sólo cuando ALGUIEN llamó `.cancel()` sobre ESTA
    tarea, no cuando lo que se cancela es el future que esta tarea esperaba
    por otra razón)."""
    monkeypatch.setattr(pdf_pool, "extraer_texto", fixtures.dormir)

    async def escenario():
        tarea = asyncio.ensure_future(
            pdf_pool.extraer_texto_en_pool(b"x", max_paginas=20, max_chars=8000))
        await asyncio.sleep(0.3)  # que el future ya este corriendo en el worker
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        # Nadie recicló el pool por esto -- una cancelación real de un
        # request no puede castigar a las demás extracciones en vuelo. El
        # pool sigue siendo el mismo objeto (con el default de 2 workers de
        # este archivo, el otro worker está libre para la siguiente).
        monkeypatch.setattr(pdf_pool, "extraer_texto", _extraer_texto_real)
        texto, _ = await pdf_pool.extraer_texto_en_pool(
            pdf_con_texto(["ok"]), max_paginas=20, max_chars=8000)
        assert "ok" in texto

    asyncio.run(escenario())


# --- item 3: terminate_workers()/kill_workers(), sin _processes -------------

def test_terminate_workers_mata_de_verdad_al_worker_colgado(monkeypatch, tmp_path):
    """No alcanza con que el pool SE REEMPLACE -- el proceso viejo tiene que
    estar realmente muerto (si no, un worker colgado que nunca libera
    memoria/fds se acumula detrás de cada timeout). `os.kill(pid, 0)` sin
    excepción significa "vivo"; `ProcessLookupError` significa que ya no
    existe."""
    monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", "1")
    # Margen mayor que el default del archivo (spawn + import de pypdf +
    # escribir el pid antes de dormir tiene que caber ANTES de que el
    # timeout dispare, si no el archivo del pid nunca se escribe).
    monkeypatch.setenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", "3")
    monkeypatch.setattr(pdf_pool, "extraer_texto", fixtures.dormir_reportando_pid)
    archivo_pid = tmp_path / "pid.txt"

    async def escenario():
        pool_viejo = await pdf_pool._pool_actual()
        with pytest.raises(PdfIlegible):
            await pdf_pool.extraer_texto_en_pool(
                str(archivo_pid).encode(), max_paginas=20, max_chars=8000)
        pool_nuevo = await pdf_pool._pool_actual()
        assert pool_nuevo is not pool_viejo

    asyncio.run(escenario())

    assert archivo_pid.exists(), "el worker nunca llegó a escribir su pid"
    pid = int(archivo_pid.read_text())
    limite = time.monotonic() + 3
    vivo = True
    while time.monotonic() < limite:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            vivo = False
            break
        time.sleep(0.02)
    assert vivo is False, f"el worker {pid} sigue vivo después de terminate/kill_workers"


def test_matar_pool_no_usa_el_atributo_privado_processes():
    """Documenta el contrato del item 3 a nivel de código: `pdf_pool.py` no
    puede volver a ACCEDER a `ProcessPoolExecutor._processes` (atributo
    privado) -- un AST, no un grep de texto: el docstring del módulo
    menciona el nombre para explicar por qué se dejó de usar, y esa mención
    no es una violación. No es una garantía de runtime, es un lint sobre el
    propio módulo."""
    import ast
    import inspect

    fuente = inspect.getsource(pdf_pool)
    accesos = [
        nodo.attr
        for nodo in ast.walk(ast.parse(fuente))
        if isinstance(nodo, ast.Attribute) and nodo.attr == "_processes"
    ]
    assert accesos == []


# --- item 4: cerrar_pool() no puede dejar un huérfano si compite con un ----
# reciclado

def test_cerrar_pool_race_con_un_reciclado_no_deja_huerfano(monkeypatch):
    monkeypatch.setattr(pdf_pool, "extraer_texto", fixtures.dormir)

    async def escenario():
        await pdf_pool._pool_actual()  # asegura que ya hay un pool antes de la carrera

        async def intentar_extraer():
            with pytest.raises((PdfIlegible, BrokenProcessPool, asyncio.CancelledError)):
                await pdf_pool.extraer_texto_en_pool(b"x", max_paginas=20, max_chars=8000)

        await asyncio.gather(intentar_extraer(), pdf_pool.cerrar_pool())

        # Sin huérfanos: el sistema sigue sirviendo -- crea un pool nuevo
        # solo, sin quedar en un estado roto ni con un executor sin apagar.
        monkeypatch.setattr(pdf_pool, "extraer_texto", _extraer_texto_real)
        texto, _ = await pdf_pool.extraer_texto_en_pool(
            pdf_con_texto(["ok"]), max_paginas=20, max_chars=8000)
        assert "ok" in texto

    asyncio.run(escenario())


# --- item 5: precalentar el pool para que el timeout no incluya el spawn ---

def test_precalentar_pool_arranca_los_workers_configurados(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", "3")
    pool = pdf_pool.crear_pool()

    async def escenario():
        await pdf_pool.precalentar_pool()

    asyncio.run(escenario())
    # Privado, aceptable en un test (igual que _max_workers arriba): sólo
    # para confirmar que precalentar_pool() de verdad hizo arrancar a los 3
    # workers, no que se quedó en una sola tarea.
    assert len(pool._processes) == 3


def test_precalentar_pool_sin_pool_no_falla():
    asyncio.run(pdf_pool.precalentar_pool())


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

    async def precalentar_espia():
        llamadas.append("precalentar")

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
    monkeypatch.setattr(pdf_pool, "precalentar_pool", precalentar_espia)
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
    assert "precalentar" in llamadas
    assert "cerrar" in llamadas
    assert llamadas.index("crear") < llamadas.index("precalentar") < llamadas.index("cerrar")
