"""El canario registra su uso (PR 8 del diseno del tablero de consumo,
2026-10-06, ~/encargos-codex/diseno-tablero-consumo.md #12).

Hallazgo MAJOR del diseno: `facet_canary.py:306` invocaba `_invoke_facet` y
descartaba el `UsageInfo` -- cada sonda es una llamada PAGA y no dejaba fila
en `axioma_usage` (~700 `canary_periodic ok` por faceta y mes mientras Costos
mostraba «sin consumo» desde el 2026-09-20). Estos tests fijan el contrato:
la sonda registra su uso por la MISMA via que el chat (`record_usage`, nunca
un INSERT propio), como `request_type='canario'`, y SIN usuario: el canario
no es una persona, la fila entra con tenant/user NULL -- nunca con el
DEFAULT 1 de la columna, que le atribuiria el gasto de infraestructura a un
tenant de verdad. Mismo tratamiento que las llamadas de sistema del repo jax
(`preflight_probe`, jacobs/usage_writer.py).

NINGUN test de este archivo llama a un proveedor: _invoke_facet esta
parcheado en todos, misma regla que test_facet_canary.py.
"""
import asyncio

import pytest

from api import chat as chat_mod
from api.chat import UsageInfo
from jax_engine import facet_canary
from jax_engine.facet_canary import (
    SOURCE_CANARY_PERIODIC,
    probe_facet,
)


def _config():
    return {"personalities": {"thot": {}, "ada": {}}}


@pytest.fixture
def sonda_falsa(monkeypatch):
    """probe_facet con el dispatch bajo control: _invoke_facet devolviendo
    usage (llamada real pagada) o no (AvisoDeChat: no hubo gasto), y las
    lecturas de base de la sonda en silencio, como el fixture
    _sin_lecturas_de_base de test_facet_canary.py."""
    async def sin_mision():
        return None

    monkeypatch.setattr(facet_canary.misiones, "turno_en_curso", sin_mision)
    registro = {"usage": None, "texto": None}

    async def invoke_falso(facet, config, user_id, message, *, source=None, **kw):
        if registro["usage"] is None:
            return chat_mod.AvisoDeChat(code="faceta_no_autorizada"), None
        return registro["texto"] or "listo", registro["usage"]

    monkeypatch.setattr(facet_canary, "_invoke_facet", invoke_falso)
    return registro


def _invoca(registro, **kw):
    registro.update(kw)
    return asyncio.run(probe_facet("thot", _config(), SOURCE_CANARY_PERIODIC))


# --- puros: que registre, y que no mienta -------------------------------


def test_la_sonda_registra_su_uso_como_canario(sonda_falsa, monkeypatch):
    llamadas = []

    async def record_espia(*args, **kwargs):
        llamadas.append((args, kwargs))

    monkeypatch.setattr(facet_canary, "record_usage", record_espia)
    out = _invoca(sonda_falsa, usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7))

    assert out is None
    assert llamadas == [
        ((None, None, "thot", "deepseek", "deepseek-v4-flash", 5, 7,
          facet_canary.REQUEST_TYPE_CANARIO), {})
    ], "la sonda paga una llamada real: tiene que dejar la misma fila de uso que el chat, como canario"


def test_una_sonda_sin_dispatch_real_no_registra_uso(sonda_falsa, monkeypatch):
    """Una denegacion del gate vuelve NORMALMENTE con usage None: no hubo
    gasto, no hay fila. Una fila ahi contaria un consumo que no existio."""
    llamadas = []

    async def record_espia(*args, **kwargs):
        llamadas.append((args, kwargs))

    monkeypatch.setattr(facet_canary, "record_usage", record_espia)
    out = _invoca(sonda_falsa, usage=None)

    assert out is None
    assert llamadas == [], "sin dispatch real no hay gasto: no se registra uso"


def test_un_fallo_del_registro_no_convierte_una_sonda_sana_en_fallida(
        sonda_falsa, monkeypatch):
    """La salud la decide _invoke_facet; el costo es OTRO registro. Si
    record_usage rompiera su promesa de no propagar, la sonda ya invocada y
    sana NO puede volverse probe_error -- seria una fila de salud falsa por
    un problema de contabilidad."""
    async def record_roto(*args, **kwargs):
        raise RuntimeError("promesa rota: record_usage no debe lanzar")

    monkeypatch.setattr(facet_canary, "record_usage", record_roto)
    out = _invoca(sonda_falsa, usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7))

    assert out is None, "el registro de uso no es parte del veredicto de salud de la sonda"


# --- con DB: la fila de verdad, por la via de verdad ----------------------


async def _filas_del_canario():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT tenant_id, user_id, facet, model, tokens_in, tokens_out, "
                "cost_usd, request_type FROM axioma_usage "
                "WHERE request_type = 'canario'", ())
            return list(await cur.fetchall())


async def _sembrar_precio():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO model (provider_id, model_id, status, source, source_checked_at, "
                "price_input_per_1m_usd, price_output_per_1m_usd) "
                "VALUES (%s, %s, 'available', 'manual', NOW(), %s, %s) "
                "ON DUPLICATE KEY UPDATE price_input_per_1m_usd=%s, price_output_per_1m_usd=%s",
                ("deepseek", "deepseek-v4-flash", 0.14, 0.28, 0.14, 0.28))
        await conn.commit()


async def _limpiar_filas_del_canario():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM axioma_usage WHERE request_type = 'canario'", ())
        await conn.commit()


def test_la_fila_del_canario_entra_de_verdad_en_axioma_usage(
        client, sonda_falsa):
    """Integracion: la fila entra por record_usage (el mismo INSERT del
    chat), con el costo calculado desde el catalogo `model` y SIN tenant ni
    usuario. Este es el test que hoy esta en rojo: el canario pagaba sin
    dejar rastro."""
    client.portal.call(_sembrar_precio)
    sonda_falsa["usage"] = UsageInfo("deepseek", "deepseek-v4-flash", 1000, 500)
    try:
        out = client.portal.call(
            probe_facet, "thot", _config(), SOURCE_CANARY_PERIODIC)
        filas = client.portal.call(_filas_del_canario)
    finally:
        client.portal.call(_limpiar_filas_del_canario)

    assert out is None
    assert len(filas) == 1, "la sonda paga una llamada: tiene que quedar una fila de uso"
    tenant, user, facet, model, tin, tout, cost, rtype = filas[0]
    assert (tenant, user) == (None, None), \
        "el canario no es una persona: NULL, nunca el DEFAULT 1 de la columna"
    assert facet == "thot"
    assert model == "deepseek-v4-flash"
    assert (tin, tout) == (1000, 500)
    assert rtype == "canario"
    expected = (1000 * 0.14 + 500 * 0.28) / 1_000_000
    assert abs(float(cost) - expected) < 1e-9
