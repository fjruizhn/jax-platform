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
        raise OSError("promesa rota: record_usage no debe lanzar")

    monkeypatch.setattr(facet_canary, "record_usage", record_roto)
    out = _invoca(sonda_falsa, usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7))

    assert out is None, "el registro de uso no es parte del veredicto de salud de la sonda"


def test_un_bug_nuestro_en_el_registro_no_se_traga(sonda_falsa, monkeypatch):
    """Solo los errores de la base o del tiempo (_ERRORES_DE_BASE) son
    fail-soft; un AttributeError/TypeError es un bug nuestro y tiene que
    verse, igual que en las lecturas de la sonda."""
    async def record_con_bug(*args, **kwargs):
        raise TypeError("bug nuestro")

    monkeypatch.setattr(facet_canary, "record_usage", record_con_bug)
    with pytest.raises(TypeError):
        _invoca(sonda_falsa, usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7))


# --- record_usage colgado: tope propio, encolado explicito, salud intacta --

SIN_RESPUESTA = "la sonda no volvio: record_usage colgado sin tope propio"


def _invoca_con_red(registro, **kw):
    """Como _invoca, pero con una red de 10 s: si el codigo no tiene tope
    propio para record_usage la sonda no vuelve nunca, y eso tiene que fallar
    por ASERCION (SIN_RESPUESTA != None), no colgando la suite."""
    registro.update(kw)

    async def con_red():
        try:
            return await asyncio.wait_for(
                probe_facet("thot", _config(), SOURCE_CANARY_PERIODIC), 10)
        except TimeoutError:
            return SIN_RESPUESTA

    return asyncio.run(con_red())


@pytest.fixture
def base_colgada(monkeypatch, tmp_path):
    """La base no contesta: get_pool nunca vuelve (pool agotado, metadata
    lock). Respaldo durable en un directorio propio; contadores en cero."""
    from api.admin import usage as usage_mod
    from uso import cola

    monkeypatch.setenv(cola.VARIABLE_DIRECTORIO, str(tmp_path))
    usage_mod.reset_registros_perdidos()
    cola.reset_estado()

    async def pool_que_no_vuelve():
        await asyncio.Event().wait()

    monkeypatch.setattr(usage_mod, "get_pool", pool_que_no_vuelve)
    monkeypatch.setattr(facet_canary, "CANARY_USAGE_TIMEOUT_SECONDS", 0.5, raising=False)
    yield tmp_path
    usage_mod.reset_registros_perdidos()
    cola.reset_estado()


def test_record_usage_colgado_no_pierde_la_fila_ni_cambia_la_salud(
        sonda_falsa, base_colgada):
    """MAJOR-1 de la auditoria: record_usage corria dentro del tope de salud;
    una base colgada cancelaba el registro con CancelledError (que ningun
    `except Exception` atrapa): la faceta sana quedaba probe_error y la fila
    pagada se perdia con 0 en la cola. Ahora el registro tiene su tope y, al
    vencerse, la fila se encola explicitamente."""
    from api.admin import usage as usage_mod
    import json

    out = _invoca_con_red(sonda_falsa, usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7))

    assert out is None, "la salud de la faceta no cambia por un fallo de contabilidad"
    archivos = [p for p in base_colgada.iterdir()
                if p.is_file() and p.suffix == ".json"]
    assert len(archivos) == 1, "la fila pagada tiene que quedar en la cola durable"
    fila = json.loads(archivos[0].read_text())
    assert (fila["facet"], fila["request_type"], fila["tokens_in"], fila["tokens_out"]) == \
        ("thot", "canario", 5, 7)
    assert (fila["tenant_id"], fila["user_id"]) == (None, None)
    assert fila["cost_usd"] is None, "sin precio: nunca un numero inventado"
    assert usage_mod._registros_perdidos == 0


def test_record_usage_colgado_bajo_el_tope_de_salud_deja_la_faceta_sana(
        sonda_falsa, base_colgada, monkeypatch):
    """El escenario EXACTO de la auditoria, por la puerta real del barrido
    (_sondear_con_tope): con el registro dentro del tope de salud, la base
    colgada cancelaba la sonda sana y el barrido la veia probe_error. Con el
    tope de salud holgado frente al del registro, el veredicto es `ok`."""
    from api.admin import usage as usage_mod

    escritas = []

    async def sin_fila_de_salud(*args, **kwargs):
        escritas.append(args)

    monkeypatch.setattr(facet_canary, "record_facet_health", sin_fila_de_salud)
    monkeypatch.setattr(facet_canary, "CANARY_FACET_TIMEOUT_SECONDS", 3)
    sonda_falsa["usage"] = UsageInfo("deepseek", "deepseek-v4-flash", 5, 7)

    async def barrido():
        try:
            return await asyncio.wait_for(facet_canary._sondear_con_tope(
                "thot", _config(), SOURCE_CANARY_PERIODIC), 10)
        except TimeoutError:
            return SIN_RESPUESTA

    out = asyncio.run(barrido())

    assert out is None, "faceta sana: un fallo de contabilidad no la vuelve probe_error"
    assert escritas == [], "ninguna fila de salud de error: la sonda fue ok"
    assert len([p for p in base_colgada.iterdir() if p.suffix == ".json"]) == 1
    assert usage_mod._registros_perdidos == 0


def test_si_ni_la_cola_puede_la_fila_se_cuenta_perdida(
        sonda_falsa, base_colgada, monkeypatch):
    """La fila solo cuenta en registros_perdidos si tampoco se pudo encolar."""
    from api.admin import usage as usage_mod
    from uso import cola

    async def cola_rota(fila):
        return None

    monkeypatch.setattr(cola, "encolar", cola_rota)
    out = _invoca_con_red(sonda_falsa, usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7))

    assert out is None
    assert usage_mod._registros_perdidos == 1


def test_la_cancelacion_de_afuera_nunca_se_traga(sonda_falsa, base_colgada, monkeypatch):
    """Un tope de salud (o el cierre del servicio) que cancela a la sonda
    mientras record_usage esta colgado tiene que propagar CancelledError, no
    convertirlo en un retorno normal."""
    monkeypatch.setattr(facet_canary, "CANARY_USAGE_TIMEOUT_SECONDS", 30)
    sonda_falsa["usage"] = UsageInfo("deepseek", "deepseek-v4-flash", 5, 7)

    async def escenario():
        tarea = asyncio.ensure_future(
            probe_facet("thot", _config(), SOURCE_CANARY_PERIODIC))
        await asyncio.sleep(0.2)
        tarea.cancel()
        await tarea

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(escenario())


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


# --- el canario en las pantallas: el dinero suma, las peticiones no -----------
# MAJOR-2 de la auditoria (decision de Hyde 2026-10-07): ~144+ sondas/dia
# pasaban a medir sondas en «Peticiones hoy» y en el grafico por faceta. El
# conteo de PETICIONES excluye request_type='canario'; el DINERO lo suma
# siempre (los canarios cuestan).


async def _insertar_uso(facet, tipo, costo):
    from tests.identidades import sql
    return await sql(
        "INSERT INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, "
        "tokens_out, cost_usd, request_type) VALUES (NULL, NULL, %s, 'm-canario', "
        "1, 1, %s, %s)", (facet, costo, tipo))


async def _borrar_uso(ids):
    from tests.identidades import sql
    for i in ids:
        await sql("DELETE FROM axioma_usage WHERE id = %s", (i,))


def test_una_fila_canario_no_cuenta_en_messages_today_pero_si_en_el_costo(client):
    import uuid
    from tests.identidades import token_de

    faceta = f"cnr-{uuid.uuid4().hex[:10]}"
    cab = {"Authorization": f"Bearer {token_de(client, 'canario-pantallas', 'superadmin', '1')}"}
    antes = client.get("/api/admin/dashboard", headers=cab).json()["stats"]["messages_today"]
    ids = [
        client.portal.call(_insertar_uso, faceta, "canario", 0.5),
        client.portal.call(_insertar_uso, faceta, "canario", 0.25),
        client.portal.call(_insertar_uso, faceta, "chat", 0.1),
    ]
    try:
        despues = client.get("/api/admin/dashboard", headers=cab).json()["stats"]["messages_today"]
        uso = client.get("/api/admin/usage?period=day", headers=cab).json()
    finally:
        client.portal.call(_borrar_uso, ids)

    assert despues - antes == 1, \
        "«Peticiones hoy» cuenta la peticion de chat y NO las 2 sondas del canario"
    mias = {f["request_type"]: f for f in uso["by_facet"] if f["facet"] == faceta}
    assert "canario" in mias, "la fila canario tiene que verse en Costos (columna Tipo)"
    assert mias["canario"]["cost_usd"] == pytest.approx(0.75), \
        "el dinero suma siempre: los canarios cuestan"
    assert mias["canario"]["requests"] == 2
    assert "chat" in mias and mias["chat"]["cost_usd"] == pytest.approx(0.1)
    # El grafico por faceta cuenta peticiones: 1 de chat, 0 de canario.
    assert sum(uso["chart_data"]["datasets"].get(faceta, [])) == 1


def test_las_consultas_de_peticiones_excluyen_canario_y_la_de_dinero_no():
    from api.admin import dashboard, usage as usage_mod

    assert "request_type <=> 'canario'" in dashboard.SQL_USO_DEL_DIA
    assert "request_type <=> 'canario'" in usage_mod.SQL_USO_GRAFICO
    assert "canario" not in usage_mod.SQL_USO_POR_FACETA, \
        "el dinero (y la columna Tipo) ven al canario: la consulta por faceta no filtra"
