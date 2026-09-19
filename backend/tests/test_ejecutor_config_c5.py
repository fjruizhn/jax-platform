# backend/tests/test_ejecutor_config_c5.py
"""Configuración de C5 en axioma_config: se siembra sin pisar lo que el admin cambió.
La compuerta de datos de clientes nace CERRADA (DECISIÓN reservada a Fernando)."""
from db.migrations import _ejecutor_config_c5_v1
from tests.identidades import sql

CLAVES = {
    "ejecutor.cerebro_faceta": "ejecutor", "ejecutor.auditor_faceta": "thot",
    "ejecutor.auditor_faceta_local": "auditor_local", "ejecutor.c5_lote_max": "20",
    "ejecutor.c5_intervalo_s": "15", "ejecutor.c5_max_tokens": "4000",
    "ejecutor.c5_auditor_admite_datos_de_clientes": "false",
}
_PLACEHOLDERS = ", ".join(["%s"] * len(CLAVES))


async def _correr():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _ejecutor_config_c5_v1(cur)
        await conn.commit()


def _filas(client):
    return dict(client.portal.call(sql, f"SELECT config_key, config_value FROM axioma_config "
                                        f"WHERE config_key IN ({_PLACEHOLDERS})", tuple(CLAVES), True))


def test_siembra_y_no_pisa(client):
    client.portal.call(sql, f"DELETE FROM axioma_config WHERE config_key IN ({_PLACEHOLDERS})", tuple(CLAVES))
    client.portal.call(_correr)
    assert _filas(client) == CLAVES
    client.portal.call(sql, "UPDATE axioma_config SET config_value = 'kimi' WHERE config_key = 'ejecutor.auditor_faceta'")
    client.portal.call(_correr)
    assert _filas(client)["ejecutor.auditor_faceta"] == "kimi"
    client.portal.call(sql, "UPDATE axioma_config SET config_value = 'thot' WHERE config_key = 'ejecutor.auditor_faceta'")


def test_el_auditor_local_no_esta_hardcodeado_a_una_sola_clave():
    """Spec 2026-09-18-auditor-local-opcion.md §4: la clave nueva es la faceta del
    auditor local -- distinta de la de nube, las dos leídas de axioma_config."""
    from db.migrations import _EJECUTOR_CONFIG_C5
    valores = dict(_EJECUTOR_CONFIG_C5)
    assert valores["ejecutor.auditor_faceta_local"] == "auditor_local"
    assert valores["ejecutor.auditor_faceta_local"] != valores["ejecutor.auditor_faceta"]


def test_la_compuerta_de_datos_de_clientes_nace_cerrada():
    from db.migrations import _EJECUTOR_CONFIG_C5
    assert dict(_EJECUTOR_CONFIG_C5)["ejecutor.c5_auditor_admite_datos_de_clientes"] == "false"
