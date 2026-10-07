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
import uuid

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
        await asyncio.sleep(registro.get("demora", 0))
        if registro["usage"] is None:
            return chat_mod.AvisoDeChat(code="faceta_no_autorizada"), None
        return registro["texto"] or "listo", registro["usage"]

    monkeypatch.setattr(facet_canary, "_invoke_facet", invoke_falso)
    return registro


@pytest.fixture
def costo_fijo(monkeypatch):
    """Los tests puros no tocan la base: el precio sale de un doble."""
    async def costo(provider_id, model, tokens_in, tokens_out):
        return 0.25

    monkeypatch.setattr(facet_canary, "calcular_costo", costo, raising=False)


def _invoca(registro, **kw):
    registro.update(kw)
    return asyncio.run(probe_facet("thot", _config(), SOURCE_CANARY_PERIODIC))


# --- puros: que registre, y que no mienta -------------------------------


def test_la_sonda_registra_su_uso_como_canario(sonda_falsa, costo_fijo, monkeypatch):
    llamadas = []

    async def record_espia(*args, **kwargs):
        llamadas.append((args, kwargs))

    monkeypatch.setattr(facet_canary, "record_usage", record_espia)
    out = _invoca(sonda_falsa, usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7))

    assert out is None
    ((args, kwargs),) = llamadas
    assert args == (None, None, "thot", "deepseek", "deepseek-v4-flash", 5, 7,
                    facet_canary.REQUEST_TYPE_CANARIO), \
        "la sonda paga una llamada real: tiene que dejar la misma fila de uso que el chat, como canario"
    assert kwargs["cost_usd_override"] == 0.25, "el precio ya calculado viaja a record_usage"
    uuid.UUID(kwargs["spool_id"])  # un uuid valido: la identidad idempotente de la fila


def test_cada_sonda_lleva_su_propio_spool_id(sonda_falsa, costo_fijo, monkeypatch):
    ids = []

    async def record_espia(*args, **kwargs):
        ids.append(kwargs["spool_id"])

    monkeypatch.setattr(facet_canary, "record_usage", record_espia)
    _invoca(sonda_falsa, usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7))
    _invoca(sonda_falsa, usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7))
    assert len(ids) == 2 and ids[0] != ids[1], "dos sondas, dos filas, dos spool_id"


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
        sonda_falsa, costo_fijo, monkeypatch):
    """La salud la decide _invoke_facet; el costo es OTRO registro. Si
    record_usage rompiera su promesa de no propagar, la sonda ya invocada y
    sana NO puede volverse probe_error -- seria una fila de salud falsa por
    un problema de contabilidad."""
    async def record_roto(*args, **kwargs):
        raise OSError("promesa rota: record_usage no debe lanzar")

    monkeypatch.setattr(facet_canary, "record_usage", record_roto)
    out = _invoca(sonda_falsa, usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7))

    assert out is None, "el registro de uso no es parte del veredicto de salud de la sonda"


def test_un_bug_nuestro_en_el_registro_no_se_traga(sonda_falsa, costo_fijo, monkeypatch):
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
    uuid.UUID(archivos[0].stem)  # la fila encolada lleva un spool_id (uuid) de la sonda
    assert fila["spool_id"] == archivos[0].stem
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


def test_la_cancelacion_de_la_tarea_de_registro_nunca_se_traga(
        sonda_falsa, base_colgada, monkeypatch):
    """El registro corre en su propia tarea blindada: cancelar a quien espera
    no la toca, pero si SE CANCELA ella misma (cierre del servicio) el
    CancelledError tiene que salir, no convertirse en un retorno normal."""
    monkeypatch.setattr(facet_canary, "CANARY_USAGE_TIMEOUT_SECONDS", 30)
    sonda_falsa["usage"] = UsageInfo("deepseek", "deepseek-v4-flash", 5, 7)

    async def escenario():
        tarea = asyncio.ensure_future(
            probe_facet("thot", _config(), SOURCE_CANARY_PERIODIC))
        await asyncio.sleep(0.2)
        en_vuelo = list(facet_canary._registros_en_vuelo)
        assert len(en_vuelo) == 1, "el registro tiene que estar en vuelo"
        en_vuelo[0].cancel()
        await tarea

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(escenario())


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


# --- ronda 3 de la auditoria: el borde del tope, el barrido, el COMMIT lento --


def _archivos_de_cola(directorio):
    return [p for p in directorio.iterdir() if p.is_file() and p.suffix == ".json"]


def test_X1_la_sonda_al_borde_del_tope_de_salud_no_pierde_su_registro(
        sonda_falsa, base_colgada, monkeypatch):
    """MAJOR-1 de la ronda 3: el registro corria DENTRO del tope de salud. Con
    el tope de faceta en 1,0 s, la sonda tardando 0,7 s y el registro colgado
    (tope propio 0,5 s), el tope de faceta cortaba el registro a mitad: la
    faceta sana quedaba probe_error y la fila se perdia. Ahora el registro
    corre despues de cerrar ese tope."""
    from api.admin import usage as usage_mod

    escritas = []

    async def sin_fila_de_salud(*args, **kwargs):
        escritas.append(args)

    monkeypatch.setattr(facet_canary, "record_facet_health", sin_fila_de_salud)
    monkeypatch.setattr(facet_canary, "CANARY_FACET_TIMEOUT_SECONDS", 1.0)
    sonda_falsa.update(usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7), demora=0.7)

    async def barrido():
        try:
            return await asyncio.wait_for(facet_canary._sondear_con_tope(
                "thot", _config(), SOURCE_CANARY_PERIODIC), 10)
        except TimeoutError:
            return SIN_RESPUESTA

    out = asyncio.run(barrido())

    assert out is None, "faceta sana al borde del tope: ok, no probe_error"
    assert escritas == [], "ninguna fila de salud de error"
    assert len(_archivos_de_cola(base_colgada)) == 1, "la fila pagada queda en la cola durable"
    assert usage_mod._registros_perdidos == 0


def test_X3_el_tope_del_barrido_tampoco_cancela_el_registro(
        sonda_falsa, base_colgada, monkeypatch):
    """El registro va blindado (asyncio.shield): aunque el barrido entero venza
    su tope mientras el registro esta en vuelo, el registro termina por su
    cuenta y encola la fila."""
    from api.admin import usage as usage_mod

    async def una_faceta(config):
        return ["thot"]

    monkeypatch.setattr(facet_canary, "canary_facets", una_faceta)
    monkeypatch.setattr(facet_canary, "_load_config", _config)
    monkeypatch.setattr(facet_canary, "CANARY_SWEEP_TIMEOUT_SECONDS", 1.0)
    sonda_falsa.update(usage=UsageInfo("deepseek", "deepseek-v4-flash", 5, 7), demora=0.7)

    async def escenario():
        with pytest.raises(TimeoutError):
            await facet_canary.probe_all()
        await asyncio.sleep(0.8)  # el registro (tope 0,5 s) sigue vivo tras el corte del barrido

    asyncio.run(escenario())

    assert len(_archivos_de_cola(base_colgada)) == 1, \
        "el tope del barrido cancelo el registro: la fila pagada se perdio"
    assert usage_mod._registros_perdidos == 0


def test_si_el_precio_ya_se_calculo_antes_del_cuelgue_la_fila_encolada_lo_lleva(
        sonda_falsa, base_colgada, monkeypatch):
    import json
    from decimal import Decimal
    from api.admin import usage as usage_mod

    async def precio(provider_id, model):
        return Decimal("0.14"), Decimal("0.28")

    monkeypatch.setattr(usage_mod, "_lookup_model_price", precio)
    out = _invoca_con_red(sonda_falsa, usage=UsageInfo("deepseek", "deepseek-v4-flash", 1000, 500))

    assert out is None
    (archivo,) = _archivos_de_cola(base_colgada)
    fila = json.loads(archivo.read_text())
    assert fila["cost_usd"] == pytest.approx((1000 * 0.14 + 500 * 0.28) / 1_000_000), \
        "el precio ya estaba calculado: la fila encolada no pierde el costo (no NULL)"


def test_el_tope_del_registro_se_lee_del_entorno_con_default_5():
    import inspect
    fuente = inspect.getsource(facet_canary)
    assert 'os.getenv("CANARY_USAGE_TIMEOUT_SECONDS", "5")' in fuente
    import os
    if "CANARY_USAGE_TIMEOUT_SECONDS" not in os.environ:
        assert facet_canary.CANARY_USAGE_TIMEOUT_SECONDS == 5.0


class _ConexionConCommitLento:
    """Confirma de verdad y DESPUES tarda: el escenario en que el tope corta
    entre el COMMIT y el retorno de record_usage (la fila ya esta en la tabla)."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *a, **k):
        return self._conn.cursor(*a, **k)

    async def commit(self):
        await self._conn.commit()
        await asyncio.sleep(1)


class _AdquisicionLenta:
    def __init__(self, pool):
        self._cm = pool.acquire()

    async def __aenter__(self):
        return _ConexionConCommitLento(await self._cm.__aenter__())

    async def __aexit__(self, *exc):
        return await self._cm.__aexit__(*exc)


class _PoolConCommitLento:
    def __init__(self, pool):
        self._pool = pool

    def acquire(self):
        return _AdquisicionLenta(self._pool)


def test_X2_un_commit_lento_no_duplica_la_fila_tras_el_drenaje(
        client, sonda_falsa, monkeypatch, tmp_path):
    """MAJOR-2 de la ronda 3: el tope corta tras el COMMIT; la fila ya esta en
    la tabla Y se encola. Sin spool_id el drenaje la insertaba otra vez (2
    filas). Con el mismo spool_id en las dos, el INSERT IGNORE contra el UNIQUE
    la deja en exactamente 1."""
    from api.admin import usage as usage_mod
    from uso import cola, reintento

    monkeypatch.setenv(cola.VARIABLE_DIRECTORIO, str(tmp_path))
    usage_mod.reset_registros_perdidos()
    cola.reset_estado()
    monkeypatch.setattr(facet_canary, "CANARY_USAGE_TIMEOUT_SECONDS", 0.5)
    sonda_falsa["usage"] = UsageInfo("deepseek", "deepseek-v4-flash", 1000, 500)

    async def escenario():
        from db.connection import get_pool
        real = await get_pool()
        await _limpiar_filas_del_canario()
        with monkeypatch.context() as m:
            m.setattr(usage_mod, "get_pool", _devuelve(_PoolConCommitLento(real)))
            out = await probe_facet("thot", _config(), SOURCE_CANARY_PERIODIC)
        tras_el_corte = len(await _filas_del_canario())
        en_cola = len(_archivos_de_cola(tmp_path))
        resumen = await reintento.drenar(100)
        tras_el_drenaje = len(await _filas_del_canario())
        pendientes = len(_archivos_de_cola(tmp_path))
        return out, tras_el_corte, en_cola, resumen, tras_el_drenaje, pendientes

    try:
        out, tras_el_corte, en_cola, resumen, tras_el_drenaje, pendientes = \
            client.portal.call(escenario)
    finally:
        client.portal.call(_limpiar_filas_del_canario)

    assert out is None
    assert tras_el_corte == 1, "el COMMIT ya habia confirmado la fila"
    assert en_cola == 1, "el tope corto: la fila tambien se encolo"
    assert tras_el_drenaje == 1, \
        "la fila encolada es la MISMA (spool_id): el drenaje no la cobra dos veces"
    assert resumen["duplicadas"] == 1 and pendientes == 0


def _devuelve(valor):
    async def get_pool():
        return valor

    return get_pool


async def _insertar_uso_nulo(facet, costo):
    from tests.identidades import sql
    return await sql(
        "INSERT INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, "
        "tokens_out, cost_usd, request_type) VALUES (NULL, NULL, %s, 'm-nulo', "
        "1, 1, %s, NULL)", (facet, costo))


def test_A1_una_fila_con_request_type_NULL_sigue_contando_como_peticion(client):
    """`<=>` es null-safe: un `<>` perderia las filas con request_type NULL
    (la columna admite NULL) de «Peticiones hoy» y del grafico."""
    from tests.identidades import token_de

    faceta = f"nul-{uuid.uuid4().hex[:10]}"
    cab = {"Authorization": f"Bearer {token_de(client, 'canario-nulo', 'superadmin', '1')}"}
    antes = client.get("/api/admin/dashboard", headers=cab).json()["stats"]["messages_today"]
    ids = [client.portal.call(_insertar_uso_nulo, faceta, 0.1)]
    try:
        despues = client.get("/api/admin/dashboard", headers=cab).json()["stats"]["messages_today"]
        uso = client.get("/api/admin/usage?period=day", headers=cab).json()
    finally:
        client.portal.call(_borrar_uso, ids)

    assert despues - antes == 1, "la fila con request_type NULL cuenta como peticion"
    assert sum(uso["chart_data"]["datasets"].get(faceta, [])) == 1, \
        "y entra en el grafico por faceta"
