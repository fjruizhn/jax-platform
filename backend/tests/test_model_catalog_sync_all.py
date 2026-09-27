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
import uuid

import pytest

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


# --------------------------------------------------------------------------
# MINOR-1 (cuarta auditoría adversarial, 2026-09-28): verificar el candado
# de verdad (IS_FREE_LOCK desde OTRA conexión) tras un sync normal y tras
# uno que revienta con una excepción.
# --------------------------------------------------------------------------

async def _is_free_lock():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT IS_FREE_LOCK(%s)", (model_catalog._NOMBRE_CANDADO_SYNC,))
            (libre,) = await cur.fetchone()
            return libre


def test_sync_all_deja_el_candado_libre_verificado_desde_otra_conexion(client, monkeypatch):
    _fake_sync_provider_models(monkeypatch, _resultados_todo_bien())
    _sin_enrich_real(monkeypatch)

    client.portal.call(model_catalog.sync_all)

    assert client.portal.call(_is_free_lock) == 1


def test_sync_all_libera_el_candado_incluso_si_algo_revienta_dentro_del_try(client, monkeypatch):
    """Una excepción DENTRO del try (después de tomar el candado, en algo
    que `sync_all()` NO envuelve en su propio try/except por proveedor --
    acá `_facetas_en_riesgo`) tiene que seguir liberando el candado en el
    `finally`. Control de que el `finally` corre pase lo que pase, no sólo
    en el camino feliz."""
    _fake_sync_provider_models(monkeypatch, _resultados_todo_bien())
    _sin_enrich_real(monkeypatch)

    async def _facetas_en_riesgo_revienta(cur):
        raise RuntimeError("boom -- no debería dejar el candado trabado")
    monkeypatch.setattr(model_catalog, "_facetas_en_riesgo", _facetas_en_riesgo_revienta)

    with pytest.raises(RuntimeError, match="boom"):
        client.portal.call(model_catalog.sync_all)

    assert client.portal.call(_is_free_lock) == 1


# --------------------------------------------------------------------------
# MAJOR-2(a)/(b) (cuarta auditoría adversarial, 2026-09-28): la
# INTERPRETACIÓN de GET_LOCK/RELEASE_LOCK es pura -- se prueba directo, sin
# tener que forzar un error real de MariaDB (imposible de reproducir
# determinísticamente en un test).
# --------------------------------------------------------------------------

def test_interpretar_get_lock_null_es_error():
    assert model_catalog._interpretar_get_lock(None) == "error"


def test_interpretar_get_lock_cero_es_ocupado():
    assert model_catalog._interpretar_get_lock(0) == "ocupado"


def test_interpretar_get_lock_uno_es_obtenido():
    assert model_catalog._interpretar_get_lock(1) == "obtenido"


class _ConexionFalsa:
    def __init__(self):
        self.cerrada = False

    def close(self):
        self.cerrada = True


def test_release_lock_que_no_confirma_cierra_la_conexion():
    conn = _ConexionFalsa()
    model_catalog._cerrar_conexion_si_release_lock_no_confirma(0, conn)
    assert conn.cerrada is True


def test_release_lock_nulo_cierra_la_conexion():
    conn = _ConexionFalsa()
    model_catalog._cerrar_conexion_si_release_lock_no_confirma(None, conn)
    assert conn.cerrada is True


def test_release_lock_confirmado_no_cierra_la_conexion():
    conn = _ConexionFalsa()
    model_catalog._cerrar_conexion_si_release_lock_no_confirma(1, conn)
    assert conn.cerrada is False


# --------------------------------------------------------------------------
# MINOR-2/MINOR-3 (cuarta auditoría adversarial, 2026-09-28): `marca_nuevos`
# -- la consulta real corre DENTRO de sync_all(), con el candado tomado.
# --------------------------------------------------------------------------

async def _sembrar_modelo_con_created_at(provider_id, model_id, created_at):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO model (provider_id, model_id, status, source, source_checked_at, created_at) "
                "VALUES (%s, %s, 'available', 'manual', NOW(), %s)",
                (provider_id, model_id, created_at),
            )
        await conn.commit()


async def _borrar_modelo(provider_id, model_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM model WHERE provider_id=%s AND model_id=%s", (provider_id, model_id))
        await conn.commit()


async def _ahora_como_str():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT NOW()")
            (ahora,) = await cur.fetchone()
            return str(ahora)


async def _hace_un_minuto_como_str():
    """Un instante claramente ANTES de "ahora" (no `NOW()` a secas): el
    modelo sintético se siembra con `created_at=NOW()` en el MISMO
    call -- si la marca fuera `NOW()` capturado un instante antes, ambas
    consultas podrían caer en el MISMO segundo (la resolución de
    `created_at` es de segundos, no de microsegundos) y la comparación
    estricta `created_at > marca` daría falso por un empate, no por un
    error real."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT NOW() - INTERVAL 1 MINUTE")
            (hace_un_minuto,) = await cur.fetchone()
            return str(hace_un_minuto)


def test_sync_all_sin_marca_nuevos_no_calcula_nada(client, monkeypatch):
    _fake_sync_provider_models(monkeypatch, _resultados_todo_bien())
    _sin_enrich_real(monkeypatch)

    result = client.portal.call(model_catalog.sync_all)

    assert "nuevos_desde_marca" not in result
    assert "marca_corte" not in result
    assert "marca_retrocedio" not in result


def test_sync_all_con_marca_nuevos_incluye_lo_creado_despues(client, monkeypatch):
    provider_id = "zhipu"
    model_id = f"test-marca-sync-all-{uuid.uuid4().hex[:8]}"
    hace_un_minuto = client.portal.call(_hace_un_minuto_como_str)
    _fake_sync_provider_models(monkeypatch, _resultados_todo_bien())
    _sin_enrich_real(monkeypatch)
    client.portal.call(_sembrar_modelo_con_created_at, provider_id, model_id, client.portal.call(_ahora_como_str))
    try:
        result = client.portal.call(model_catalog.sync_all, hace_un_minuto)
        assert result["marca_retrocedio"] is False
        assert model_id in result.get("nuevos_desde_marca", {}).get(provider_id, [])
        assert isinstance(result["marca_corte"], str) and result["marca_corte"]
    finally:
        client.portal.call(_borrar_modelo, provider_id, model_id)


def test_sync_all_marca_en_el_futuro_avisa_retroceso_y_no_pierde(client, monkeypatch):
    """MINOR-3: si `marca_nuevos` queda DESPUÉS de `NOW()` (reloj o zona
    horaria movidos hacia atrás), `sync_all()` marca `marca_retrocedio` en
    vez de devolver silenciosamente una ventana vacía/invertida."""
    _fake_sync_provider_models(monkeypatch, _resultados_todo_bien())
    _sin_enrich_real(monkeypatch)

    marca_del_futuro = "2099-01-01 00:00:00"
    result = client.portal.call(model_catalog.sync_all, marca_del_futuro)

    assert result["marca_retrocedio"] is True
    assert result["nuevos_desde_marca"] == {}
    assert isinstance(result["marca_corte"], str) and result["marca_corte"]
