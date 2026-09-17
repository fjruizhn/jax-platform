"""Datos que el pre-vuelo necesita, medidos y no inventados (spec 2026-09-17
§4.4 y §7 F). La medición (sólo lectura contra producción) y su evidencia
están en el comentario de MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17 y en el commit."""
import pytest

from db import migrations
from db.migrations import (
    MIGRACION_MIN_OUTPUT_TOKENS_V1,
    MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17,
    _semilla_min_output_tokens_v1,
)
from tests.identidades import sql

TRANSPORTES_QUE_COBRAN = ("http_openai_compat", "http_gemini")


def test_lo_medido_son_multiplos_de_1024_positivos():
    assert MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17, "la medición no dejó ningún valor"
    malos = {k: v for k, v in MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17.items()
             if not (isinstance(v, int) and v > 0 and v % 1024 == 0)}
    assert malos == {}


def test_todo_modelo_de_la_semilla_que_cobra_declara_su_tope_de_salida():
    """Sin tope no hay costo máximo: una base vacía no puede nacer con una
    faceta que el pre-vuelo rechaza con sin_contrato_de_salida."""
    transporte = {key: t for key, _n, _i, _c, t, _a in migrations._FACET_SEED}
    topes = {(p, mo) for p, mo, _ in migrations._MODEL_MAX_OUTPUT_TOKENS_SEED}
    faltan = [(f, p, mo) for f, p, mo in migrations._FACET_BINDING_SEED
              if transporte[f] in TRANSPORTES_QUE_COBRAN and (p, mo) not in topes]
    assert faltan == []


async def _migrar():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _semilla_min_output_tokens_v1(cur)
        await conn.commit()


@pytest.fixture
def sin_marca_min(client):
    marca = client.portal.call(sql, "SELECT nombre FROM axioma_migracion_de_datos WHERE nombre = %s",
                               (MIGRACION_MIN_OUTPUT_TOKENS_V1,), True)
    antes = client.portal.call(sql, "SELECT `key`, min_output_tokens FROM capability", (), True)
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_MIN_OUTPUT_TOKENS_V1,))
    client.portal.call(sql, "UPDATE capability SET min_output_tokens = 0", ())
    yield
    for clave, valor in antes:
        client.portal.call(sql, "UPDATE capability SET min_output_tokens = %s WHERE `key` = %s", (valor, clave))
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_MIN_OUTPUT_TOKENS_V1,))
    if marca:
        client.portal.call(sql, "INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)",
                           (MIGRACION_MIN_OUTPUT_TOKENS_V1,))


def _minimos(client):
    return dict(client.portal.call(sql, "SELECT `key`, min_output_tokens FROM capability", (), True))


def test_primera_corrida_siembra_lo_medido_y_deja_el_resto_en_cero(client, sin_marca_min):
    client.portal.call(_migrar)
    filas = _minimos(client)
    presentes = {k: v for k, v in MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17.items() if k in filas}
    assert presentes, "ninguna capability medida existe en jax_memory_test"
    assert {k: filas[k] for k in presentes} == presentes
    assert {k: v for k, v in filas.items() if k not in MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17 and v != 0} == {}


def test_segunda_corrida_no_pisa_lo_que_el_admin_cambio(client, sin_marca_min):
    client.portal.call(_migrar)
    clave = next(k for k in MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17 if k in _minimos(client))
    client.portal.call(sql, "UPDATE capability SET min_output_tokens = 7 WHERE `key` = %s", (clave,))
    client.portal.call(_migrar)
    assert _minimos(client)[clave] == 7
