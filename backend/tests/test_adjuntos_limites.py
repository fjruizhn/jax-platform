"""Límites de adjuntos (frente D, 2026-09-16): salen de /etc/jax/.env y, si
faltan o no son enteros > 0, el servicio NO arranca. Sin default silencioso:
un límite ausente leído como "sin límite" es un fail-open."""
import asyncio

import pytest

from adjuntos import limites as mod

_VALORES = {
    "JAX_ADJUNTO_MAX_BYTES": "10485760",
    "JAX_ADJUNTO_MAX_CHARS": "8000",
    "JAX_ADJUNTO_MAX_PAGINAS": "20",
    "JAX_ADJUNTO_MAX_POR_MENSAJE": "1",
}


def _fijar(monkeypatch, **cambios):
    for variable, valor in {**_VALORES, **cambios}.items():
        if valor is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, valor)


def test_lee_los_cuatro_limites(monkeypatch):
    _fijar(monkeypatch)
    assert mod.cargar_limites() == mod.LimitesDeAdjuntos(
        max_bytes=10485760, max_chars=8000, max_paginas=20, max_por_mensaje=1)


def test_variable_ausente_no_arranca_y_la_nombra(monkeypatch):
    _fijar(monkeypatch, JAX_ADJUNTO_MAX_CHARS=None)
    with pytest.raises(mod.LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_limites()
    assert "JAX_ADJUNTO_MAX_CHARS" in str(e.value)


@pytest.mark.parametrize("valor", ["0", "-5", "diez"])
def test_valor_no_positivo_o_no_entero_no_arranca(monkeypatch, valor):
    _fijar(monkeypatch, JAX_ADJUNTO_MAX_BYTES=valor)
    with pytest.raises(mod.LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_limites()
    assert "JAX_ADJUNTO_MAX_BYTES" in str(e.value)


def test_lifespan_valida_los_limites_antes_de_abrir_la_base(monkeypatch):
    import main

    llamadas = []

    def sin_limites():
        llamadas.append("limites")
        raise mod.LimitesDeAdjuntosInvalidos("JAX_ADJUNTO_MAX_BYTES=None")

    async def pool_espia():
        llamadas.append("pool")

    monkeypatch.setattr(mod, "cargar_limites", sin_limites)
    monkeypatch.setattr(main, "get_pool", pool_espia)

    async def arrancar():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(mod.LimitesDeAdjuntosInvalidos):
        asyncio.run(arrancar())
    assert llamadas == ["limites"]


# --- R16 (2026-09-17): tope de imágenes pesadas en proceso a la vez ----------

@pytest.mark.parametrize("valor", [None, "0", "-1", "uno"])
def test_tope_de_imagenes_en_proceso_ausente_o_invalido_no_arranca(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("JAX_ADJUNTO_IMAGENES_EN_PROCESO", raising=False)
    else:
        monkeypatch.setenv("JAX_ADJUNTO_IMAGENES_EN_PROCESO", valor)
    with pytest.raises(mod.LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_imagenes_en_proceso()
    assert "JAX_ADJUNTO_IMAGENES_EN_PROCESO" in str(e.value)


def test_tope_de_imagenes_en_proceso_se_lee(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_IMAGENES_EN_PROCESO", "3")
    assert mod.cargar_imagenes_en_proceso() == 3


def test_lifespan_valida_el_tope_de_imagenes_antes_de_abrir_la_base(monkeypatch):
    import main

    llamadas = []

    def sin_tope():
        llamadas.append("tope")
        raise mod.LimitesDeAdjuntosInvalidos("JAX_ADJUNTO_IMAGENES_EN_PROCESO=None")

    async def pool_espia():
        llamadas.append("pool")

    monkeypatch.setattr(mod, "cargar_imagenes_en_proceso", sin_tope)
    monkeypatch.setattr(main, "get_pool", pool_espia)

    async def arrancar():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(mod.LimitesDeAdjuntosInvalidos):
        asyncio.run(arrancar())
    assert llamadas == ["tope"]


# --- Revisión final (2026-09-17): tope de subidas pesadas en proceso a la vez --

@pytest.mark.parametrize("valor", [None, "0", "-1", "uno"])
def test_tope_de_subidas_en_proceso_ausente_o_invalido_no_arranca(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", raising=False)
    else:
        monkeypatch.setenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", valor)
    with pytest.raises(mod.LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_subidas_en_proceso()
    assert "JAX_ADJUNTO_SUBIDAS_EN_PROCESO" in str(e.value)


def test_tope_de_subidas_en_proceso_se_lee(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", "3")
    assert mod.cargar_subidas_en_proceso() == 3


def test_lifespan_valida_el_tope_de_subidas_antes_de_abrir_la_base(monkeypatch):
    import main

    llamadas = []

    def sin_tope():
        llamadas.append("tope")
        raise mod.LimitesDeAdjuntosInvalidos("JAX_ADJUNTO_SUBIDAS_EN_PROCESO=None")

    async def pool_espia():
        llamadas.append("pool")

    monkeypatch.setattr(mod, "cargar_subidas_en_proceso", sin_tope, raising=False)
    monkeypatch.setattr(main, "get_pool", pool_espia)

    async def arrancar():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(mod.LimitesDeAdjuntosInvalidos):
        asyncio.run(arrancar())
    assert llamadas == ["tope"]


# --- RD1 (2026-09-17): tamaño del ProcessPoolExecutor de pypdf y timeout ----
# de cada extracción. Los dos acotados por arriba (no solo "entero > 0"): un
# valor sin techo es tan fail-open como uno ausente -- un .env con un cero de
# más arrancaría un pool que se come toda la máquina o un timeout que nunca
# corta un PDF patológico.

@pytest.mark.parametrize("valor", [None, "0", "-1", "uno"])
def test_tope_de_procesos_de_pdf_ausente_o_invalido_no_arranca(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("JAX_ADJUNTO_PDF_PROCESOS", raising=False)
    else:
        monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", valor)
    with pytest.raises(mod.LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_procesos_de_pdf()
    assert "JAX_ADJUNTO_PDF_PROCESOS" in str(e.value)


def test_tope_de_procesos_de_pdf_por_encima_del_maximo_no_arranca(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", str(mod.LIMITE_PROCESOS_DE_PDF + 1))
    with pytest.raises(mod.LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_procesos_de_pdf()
    assert "JAX_ADJUNTO_PDF_PROCESOS" in str(e.value)


def test_tope_de_procesos_de_pdf_en_el_maximo_arranca(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", str(mod.LIMITE_PROCESOS_DE_PDF))
    assert mod.cargar_procesos_de_pdf() == mod.LIMITE_PROCESOS_DE_PDF


def test_tope_de_procesos_de_pdf_se_lee(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_PDF_PROCESOS", "2")
    assert mod.cargar_procesos_de_pdf() == 2


def test_lifespan_valida_el_tope_de_procesos_de_pdf_antes_de_abrir_la_base(monkeypatch):
    import main

    llamadas = []

    def sin_tope():
        llamadas.append("tope")
        raise mod.LimitesDeAdjuntosInvalidos("JAX_ADJUNTO_PDF_PROCESOS=None")

    async def pool_espia():
        llamadas.append("pool")

    monkeypatch.setattr(mod, "cargar_procesos_de_pdf", sin_tope, raising=False)
    monkeypatch.setattr(main, "get_pool", pool_espia)

    async def arrancar():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(mod.LimitesDeAdjuntosInvalidos):
        asyncio.run(arrancar())
    assert llamadas == ["tope"]


@pytest.mark.parametrize("valor", [None, "0", "-1", "uno"])
def test_timeout_de_pdf_ausente_o_invalido_no_arranca(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", raising=False)
    else:
        monkeypatch.setenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", valor)
    with pytest.raises(mod.LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_timeout_de_pdf()
    assert "JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS" in str(e.value)


def test_timeout_de_pdf_por_encima_del_maximo_no_arranca(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", str(mod.LIMITE_TIMEOUT_DE_PDF_SEGUNDOS + 1))
    with pytest.raises(mod.LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_timeout_de_pdf()
    assert "JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS" in str(e.value)


def test_timeout_de_pdf_se_lee(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS", "7")
    assert mod.cargar_timeout_de_pdf() == 7


def test_lifespan_valida_el_timeout_de_pdf_antes_de_abrir_la_base(monkeypatch):
    import main

    llamadas = []

    def sin_tope():
        llamadas.append("tope")
        raise mod.LimitesDeAdjuntosInvalidos("JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS=None")

    async def pool_espia():
        llamadas.append("pool")

    monkeypatch.setattr(mod, "cargar_timeout_de_pdf", sin_tope, raising=False)
    monkeypatch.setattr(main, "get_pool", pool_espia)

    async def arrancar():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(mod.LimitesDeAdjuntosInvalidos):
        asyncio.run(arrancar())
    assert llamadas == ["tope"]
