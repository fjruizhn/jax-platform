"""Umbral de confirmación de costo (spec 2026-09-17 §6.1): Decimal >= 0, 0 =
confirmar siempre, sembrado en 0.50 y editable en el panel de ajustes."""
from decimal import Decimal

import pytest

import ajustes
from db import migrations
from db.migrations import MIGRACION_AJUSTE_CONFIRMAR_USD_V1, _ajuste_confirmar_costo_v1
from tests.identidades import cabeceras, sql


def test_acepta_montos_canonicos_con_hasta_dos_decimales():
    interpretar = ajustes.DEFINICIONES[ajustes.CONFIRMAR_USD].interpretar
    assert [interpretar(t) for t in ("0", "0.5", "0.50", "12", "999999.99")] == [
        Decimal("0"), Decimal("0.5"), Decimal("0.50"), Decimal("12"), Decimal("999999.99")]


@pytest.mark.parametrize("texto", ["", "-1", "0.505", "1e3", "00.5", ".5", " 0.5", "0,50", "1000000", "٠.٥", "NaN"])
def test_rechaza_lo_que_no_es_un_monto_canonico(texto):
    with pytest.raises(ajustes.ValorInvalido):
        ajustes.DEFINICIONES[ajustes.CONFIRMAR_USD].interpretar(texto)


def test_limites_publicados_del_umbral():
    assert ajustes.CONFIRMAR_USD in ajustes.CLAVES
    assert ajustes.limites()["pipeline_confirmar_usd"] == {"min": "0", "max": "999999.99", "decimales": 2}


def test_la_validacion_real_respeta_el_maximo_publicado(monkeypatch):
    """Regresión: el máximo hoy sólo lo hacía cumplir la CANTIDAD de dígitos
    del regex (coincidencia, no una comparación). Si alguien cambia
    CONFIRMAR_USD_MAX sin tocar el regex, limites() (lo que muestra el panel)
    y la validación real divergen en silencio."""
    interpretar = ajustes.DEFINICIONES[ajustes.CONFIRMAR_USD].interpretar
    monkeypatch.setattr(ajustes, "CONFIRMAR_USD_MAX", "100.00")
    assert interpretar("100.00") == Decimal("100.00")
    with pytest.raises(ajustes.ValorInvalido):
        interpretar("100.01")


async def _migrar():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _ajuste_confirmar_costo_v1(cur)
        await conn.commit()


@pytest.fixture
def sin_marca_umbral(client, ajustes_en_db):
    marca = client.portal.call(sql, "SELECT nombre FROM axioma_migracion_de_datos WHERE nombre = %s",
                               (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,), True)
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,))
    yield ajustes_en_db
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,))
    if marca:
        client.portal.call(sql, "INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)",
                           (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,))


def test_sin_fila_la_migracion_siembra_0_50(client, sin_marca_umbral):
    sin_marca_umbral.quitar("pipeline_confirmar_usd")
    client.portal.call(_migrar)
    assert sin_marca_umbral.filas()["pipeline_confirmar_usd"] == "0.50"


def test_con_fila_guardada_la_migracion_no_la_pisa(client, sin_marca_umbral):
    sin_marca_umbral.poner(pipeline_confirmar_usd="2.00")
    client.portal.call(_migrar)
    assert sin_marca_umbral.filas()["pipeline_confirmar_usd"] == "2.00"


def test_el_put_de_admin_valida_y_guarda_el_umbral(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    encabezados = cabeceras(client, "ajuste-umbral", role="superadmin")
    malo = client.put("/api/admin/config", json=[{"key": "pipeline_confirmar_usd", "value": "abc"}], headers=encabezados)
    assert (malo.status_code, malo.json()) == (400, {"detail": {"code": "config_valor_invalido", "clave": "pipeline_confirmar_usd"}})
    bueno = client.put("/api/admin/config", json=[{"key": "pipeline_confirmar_usd", "value": "1.25"}], headers=encabezados)
    assert bueno.status_code == 200
    assert client.portal.call(ajustes.valor, ajustes.CONFIRMAR_USD) == Decimal("1.25")


# 2026-09-17, merge de master: la fila del umbral había desaparecido de
# jax_memory_test (otra suite la borró) y el marcador de la migración impedía
# volver a sembrarla: `POST /api/pipelines` respondía 503 ajuste_ilegible para
# siempre. El umbral es un ajuste REQUERIDO: se repone en cada arranque, con
# INSERT IGNORE, igual que la config de C5 (nunca pisa lo que puso el admin).
def test_el_umbral_se_repone_si_alguien_borro_la_fila(client):
    async def correr():
        await sql("DELETE FROM axioma_config WHERE config_key = %s", (ajustes.CONFIRMAR_USD,))
        await migrations.run_migrations()
        return await sql("SELECT config_value FROM axioma_config WHERE config_key = %s",
                         (ajustes.CONFIRMAR_USD,), fetch=True)

    assert client.portal.call(correr) == ((migrations.VALOR_INICIAL_CONFIRMAR_USD,),)
