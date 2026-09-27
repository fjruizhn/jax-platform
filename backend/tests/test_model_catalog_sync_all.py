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
    async def _fake(provider_id, forzar=False):
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
    async def _fake(provider_id, forzar=False):
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
