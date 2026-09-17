"""Ajustes de administración que mandan (spec 2026-09-16-hallazgos-auditoria §C).

Puros: la validación por clave (rangos del SERVIDOR, no del <input>), el TTL
del entorno, el caché (TTL, invalidación explícita, y que una invalidación
durante una lectura en vuelo no deje guardado lo viejo), los límites públicos,
el proceso único y la respuesta 503. Con DB (jax_memory_test): la lectura
real, que una fila ausente o inválida sea un ERROR y no un default, y el
EXPLAIN de la consulta real.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ajustes

BACKEND = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ puros

def test_entero_acepta_los_bordes_y_rechaza_fuera_de_rango():
    sesion = ajustes.DEFINICIONES[ajustes.SESION].interpretar
    assert sesion("15") == 15 and sesion("10080") == 10080
    for texto in ("14", "10081", "0"):
        with pytest.raises(ajustes.ValorInvalido):
            sesion(texto)
    tope = ajustes.DEFINICIONES[ajustes.MAX_PIPELINES].interpretar
    assert [tope("1"), tope("3")] == [1, 3]
    with pytest.raises(ajustes.ValorInvalido):
        tope("4")


def test_entero_rechaza_lo_que_no_es_un_entero_canonico():
    retencion = ajustes.DEFINICIONES[ajustes.RETENCION].interpretar
    for texto in ("", " 30", "30 ", "+30", "30.0", "030", "٣٠", "treinta", "-1"):
        with pytest.raises(ajustes.ValorInvalido):
            retencion(texto)


def test_idioma_solo_admite_es_y_en():
    idioma = ajustes.DEFINICIONES[ajustes.IDIOMA].interpretar
    assert idioma("es") == "es" and idioma("en") == "en"
    for texto in ("", "ES", "fr", "es ", "español"):
        with pytest.raises(ajustes.ValorInvalido):
            idioma(texto)


def test_nombre_del_sistema_sin_espacios_alrededor_ni_control_ni_largo_de_mas():
    nombre = ajustes.DEFINICIONES[ajustes.NOMBRE].interpretar
    assert nombre("Axioma") == "Axioma"
    assert nombre("x" * ajustes.NOMBRE_MAX) == "x" * ajustes.NOMBRE_MAX
    for texto in ("", " ", " Axioma", "Axioma ", "x" * (ajustes.NOMBRE_MAX + 1), "Axi\noma", "Axi\x00oma"):
        with pytest.raises(ajustes.ValorInvalido):
            nombre(texto)


def test_ttl_del_entorno_falla_fuerte_si_no_es_positivo_y_finito():
    assert ajustes.ttl_desde_entorno("30") == 30.0
    for texto in ("", "abc", "0", "-5", "nan", "inf"):
        with pytest.raises(ValueError, match="JAX_AJUSTES_TTL_S"):
            ajustes.ttl_desde_entorno(texto)


class _Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _cargador(respuestas):
    llamadas = []

    async def cargar():
        llamadas.append(1)
        return dict(respuestas[min(len(llamadas), len(respuestas)) - 1])

    return cargar, llamadas


async def test_la_cache_no_recarga_dentro_del_ttl():
    cargar, llamadas = _cargador([{"max_pipelines": "3"}])
    reloj = _Reloj()
    cache = ajustes.CacheDeAjustes(cargar, ttl_s=30, reloj=reloj)
    assert await cache.filas() == {"max_pipelines": "3"}
    reloj.t += 29.9
    assert await cache.filas() == {"max_pipelines": "3"}
    assert len(llamadas) == 1


async def test_la_cache_recarga_al_vencer_el_ttl():
    cargar, llamadas = _cargador([{"max_pipelines": "3"}, {"max_pipelines": "2"}])
    reloj = _Reloj()
    cache = ajustes.CacheDeAjustes(cargar, ttl_s=30, reloj=reloj)
    await cache.filas()
    reloj.t += 30
    assert await cache.filas() == {"max_pipelines": "2"}
    assert len(llamadas) == 2


async def test_invalidar_obliga_a_recargar_aunque_no_haya_vencido():
    cargar, llamadas = _cargador([{"max_pipelines": "3"}, {"max_pipelines": "2"}])
    cache = ajustes.CacheDeAjustes(cargar, ttl_s=30, reloj=_Reloj())
    await cache.filas()
    cache.invalidar()
    assert await cache.filas() == {"max_pipelines": "2"}
    assert len(llamadas) == 2


async def test_una_invalidacion_durante_la_carga_no_deja_guardado_lo_viejo():
    entro, soltar = asyncio.Event(), asyncio.Event()
    respuestas = [{"max_pipelines": "3"}, {"max_pipelines": "1"}]
    llamadas = []

    async def cargar():
        llamadas.append(1)
        if len(llamadas) == 1:
            entro.set()
            await soltar.wait()
        return dict(respuestas[len(llamadas) - 1])

    cache = ajustes.CacheDeAjustes(cargar, ttl_s=30, reloj=_Reloj())
    en_vuelo = asyncio.create_task(cache.filas())
    await entro.wait()
    cache.invalidar()  # el PUT confirmó un valor nuevo mientras la lectura vieja seguía en vuelo
    soltar.set()
    assert await en_vuelo == {"max_pipelines": "3"}  # quien pidió antes recibe lo que leyó
    assert await cache.filas() == {"max_pipelines": "1"}  # pero no quedó guardado
    assert len(llamadas) == 2


def test_limites_publicos_y_tope_espejado_de_jacobs():
    assert ajustes.MAX_PARALLEL_PIPELINES == 3
    assert ajustes.limites() == {
        "session_timeout_min": {"min": 15, "max": 10080},
        "max_pipelines": {"min": 1, "max": 3},
        "web_task_retention_days": {"min": 1, "max": 365},
        "lang_default": {"opciones": ["es", "en"]},
        "system_name": {"max_largo": 60},
    }


def test_el_modulo_se_niega_a_correr_con_varios_workers():
    r = subprocess.run(
        [sys.executable, "-c", "import ajustes"],
        cwd=BACKEND, env={**os.environ, "WEB_CONCURRENCY": "2"},
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode != 0
    assert "workers" in r.stderr


async def test_ajuste_ilegible_responde_503_con_codigo_y_clave():
    r = await ajustes.respuesta_de_ajuste_ilegible(None, ajustes.AjusteIlegible("max_pipelines", "invalido"))
    assert r.status_code == 503
    assert json.loads(r.body) == {"detail": {"code": "ajuste_ilegible", "clave": "max_pipelines"}}


# ------------------------------------------------------------------ con DB

async def _leer_todos():
    return {clave: await ajustes.valor(clave) for clave in ajustes.CLAVES}


async def _error_de(clave):
    try:
        await ajustes.valor(clave)
    except ajustes.AjusteIlegible as exc:
        return (exc.clave, exc.motivo)
    return None


async def _explain():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("EXPLAIN " + ajustes.CONSULTA, ajustes.CLAVES)
            return await cur.fetchall(), [d[0] for d in cur.description]


def test_lee_los_valores_tipados_de_la_tabla(client, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "max_pipelines": "2", "lang_default": "en"})
    assert client.portal.call(_leer_todos) == {
        "session_timeout_min": 10080, "max_pipelines": 2, "web_task_retention_days": 30,
        "lang_default": "en", "system_name": "Axioma",
    }


def test_fila_ausente_es_un_error_y_no_tumba_a_los_demas(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    ajustes_en_db.quitar("max_pipelines")
    assert client.portal.call(_error_de, "max_pipelines") == ("max_pipelines", "ausente")
    assert client.portal.call(_error_de, "system_name") is None


def test_valor_invalido_es_un_error_y_no_un_default(client, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "60 min"})
    assert client.portal.call(_error_de, "session_timeout_min") == ("session_timeout_min", "invalido")


def test_explain_de_la_consulta_real_va_por_primary(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    filas, columnas = client.portal.call(_explain)
    plan = dict(zip(columnas, filas[0]))
    assert plan["key"] == "PRIMARY", plan
    extra = plan.get("Extra") or ""
    assert "filesort" not in extra and "temporary" not in extra, plan
