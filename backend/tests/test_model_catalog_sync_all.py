"""Bloque D -- orquestacion completa del catalogo (2026-09-27).

Hallazgo real que origina esta ronda: desde el 17-sep los servicios corren
como `jaxsvc`, que no tiene `~/.claude/.credentials.json` -- el sync de
anthropic se saltaba en cada corrida de POST /admin/models/sync
(api/admin/models.py) y la respuesta seguia diciendo `ok: true` porque solo
`error` bajaba `ok`, nunca `skipped`. Opus 5.5 nunca entro al catalogo y
nadie se enteró.

`model_catalog.sync_all()` es la logica extraida (antes vivia inline en el
endpoint) para que el endpoint Y el ejecutor programado
(catalogo_modelos_ejecutor.py) compartan la MISMA fuente de "que significa
que el catalogo este sano". Estos tests fakean `sync_provider_models` y
`enrich_from_models_dev` -- la integracion real de cada rama ya la cubre
test_model_catalog_sync.py -- y ejercitan la AGREGACION: como un resultado
por proveedor se convierte en `ok`/`code`/las listas de la respuesta.

Usa `client` (DB real vía el portal de la sesión) porque `sync_all()` hace
una consulta real a `facet_binding`/`model` para las facetas en riesgo.
"""
import model_catalog


def _resultados_todo_bien():
    return {p: {"provider_id": p, "fetched": 1, "nuevos": []} for p in model_catalog.SYNCABLE_PROVIDERS}


async def _enrich_ok():
    return {"enriched": 0}


def _fake_sync_provider_models(monkeypatch, resultados):
    async def _fake(provider_id):
        return resultados[provider_id]
    monkeypatch.setattr(model_catalog, "sync_provider_models", _fake)


def _sin_enrich_real(monkeypatch):
    monkeypatch.setattr(model_catalog, "enrich_from_models_dev", _enrich_ok)


def test_sync_all_ok_true_cuando_todo_sincroniza_sin_saltos(client, monkeypatch):
    _fake_sync_provider_models(monkeypatch, _resultados_todo_bien())
    _sin_enrich_real(monkeypatch)

    result = client.portal.call(model_catalog.sync_all)

    assert result["providers_fallidos"] == []
    assert result["providers_saltados"] == []
    assert result["enrich_fallido"] is False
    # ok tambien depende de que no haya facetas en riesgo HOY en la base de
    # esta sesión -- si alguna vez lo hay, el propio valor de `ok` lo dice;
    # lo que este test afirma sin depender de eso es que NINGUNA de las
    # otras tres causas de 'no ok' esta presente.
    if result["ok"]:
        assert "code" not in result
    else:
        assert result["facetas_en_riesgo"]  # única causa posible dejada en pie


def test_sync_all_un_provider_saltado_baja_ok_y_se_lista(client, monkeypatch):
    """El hallazgo real: un 'skipped' (anthropic sin credencial de jaxsvc,
    ollama caído, lo que sea) YA NO puede seguir contando como éxito."""
    resultados = _resultados_todo_bien()
    resultados["anthropic"] = {
        "provider_id": "anthropic", "fetched": 0,
        "skipped": "oauth local no disponible: credentials file unreadable",
    }
    _fake_sync_provider_models(monkeypatch, resultados)
    _sin_enrich_real(monkeypatch)

    result = client.portal.call(model_catalog.sync_all)

    assert result["ok"] is False
    assert result["providers_saltados"] == ["anthropic"]
    assert result["providers_fallidos"] == []
    assert result["code"] == "sync_con_errores"


def test_sync_all_un_provider_fallido_baja_ok_y_se_lista(client, monkeypatch):
    async def _fake(provider_id):
        if provider_id == "openai":
            raise RuntimeError("boom")
        return {"provider_id": provider_id, "fetched": 0, "nuevos": []}
    monkeypatch.setattr(model_catalog, "sync_provider_models", _fake)
    _sin_enrich_real(monkeypatch)

    result = client.portal.call(model_catalog.sync_all)

    assert result["ok"] is False
    assert result["providers_fallidos"] == ["openai"]
    assert result["providers_saltados"] == []


def test_sync_all_enrich_fallido_baja_ok(client, monkeypatch):
    _fake_sync_provider_models(monkeypatch, _resultados_todo_bien())

    async def _enrich_falla():
        raise RuntimeError("models.dev caído")
    monkeypatch.setattr(model_catalog, "enrich_from_models_dev", _enrich_falla)

    result = client.portal.call(model_catalog.sync_all)

    assert result["ok"] is False
    assert result["enrich_fallido"] is True
    assert result["code"] == "sync_con_errores"


def test_sync_all_agrega_nuevos_por_proveedor_solo_los_no_vacios(client, monkeypatch):
    resultados = _resultados_todo_bien()
    resultados["moonshot"] = {"provider_id": "moonshot", "fetched": 2, "nuevos": ["kimi-nuevo"]}
    _fake_sync_provider_models(monkeypatch, resultados)
    _sin_enrich_real(monkeypatch)

    result = client.portal.call(model_catalog.sync_all)

    assert result["nuevos"] == {"moonshot": ["kimi-nuevo"]}


# --------------------------------------------------------------------------
# Punto 5 (tercera auditoría adversarial, 2026-09-27): candado contra syncs
# concurrentes -- `GET_LOCK`/`RELEASE_LOCK` de MariaDB, sostenido en UNA
# conexión dedicada durante todo `sync_all()`.
# --------------------------------------------------------------------------

async def _tomar_candado_desde_otra_conexion():
    """Simula OTRO proceso con el candado tomado: una conexión PROPIA del
    pool (no la que usa `sync_all()`), que pide el MISMO `GET_LOCK` y no lo
    suelta hasta que el test llame a `_soltar_candado`."""
    from db.connection import get_pool
    pool = await get_pool()
    conn = await pool.acquire()
    cur = await conn.cursor()
    await cur.execute("SELECT GET_LOCK(%s, 0)", (model_catalog._NOMBRE_CANDADO_SYNC,))
    (obtenido,) = await cur.fetchone()
    await cur.close()
    assert obtenido == 1, "no se pudo tomar el candado desde la conexión de control del test"
    return pool, conn


async def _soltar_candado(pool, conn):
    cur = await conn.cursor()
    await cur.execute("SELECT RELEASE_LOCK(%s)", (model_catalog._NOMBRE_CANDADO_SYNC,))
    await cur.close()
    await pool.release(conn)


def test_sync_all_candado_ocupado_no_toca_nada_y_devuelve_sync_en_curso(client, monkeypatch):
    llamado = []

    async def _no_deberia_llamarse(provider_id):
        llamado.append(provider_id)
        return {"provider_id": provider_id, "fetched": 1, "nuevos": []}
    monkeypatch.setattr(model_catalog, "sync_provider_models", _no_deberia_llamarse)

    async def _enrich_no_deberia_llamarse():
        raise AssertionError("enrich_from_models_dev no debería correr con el candado ocupado")
    monkeypatch.setattr(model_catalog, "enrich_from_models_dev", _enrich_no_deberia_llamarse)

    pool, conn = client.portal.call(_tomar_candado_desde_otra_conexion)
    try:
        result = client.portal.call(model_catalog.sync_all)
    finally:
        client.portal.call(_soltar_candado, pool, conn)

    assert result == {
        "ok": False, "code": "sync_en_curso",
        "providers": [], "enrich": {}, "providers_fallidos": [],
        "providers_saltados": [], "enrich_fallido": False,
        "nuevos": {}, "facetas_en_riesgo": [],
    }
    assert llamado == []  # no se tocó ni un proveedor


def test_sync_all_libera_el_candado_al_terminar(client, monkeypatch):
    """Control: sin nadie más sosteniendo el candado, DOS `sync_all()`
    consecutivos tienen que poder correr los dos -- si el primero no
    soltara el candado en su `finally`, el segundo se vería "en curso" para
    siempre."""
    _fake_sync_provider_models(monkeypatch, _resultados_todo_bien())
    _sin_enrich_real(monkeypatch)

    primero = client.portal.call(model_catalog.sync_all)
    segundo = client.portal.call(model_catalog.sync_all)

    assert primero.get("code") != "sync_en_curso"
    assert segundo.get("code") != "sync_en_curso"
