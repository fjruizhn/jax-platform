# backend/tests/test_ejecutor_auditor_local.py
"""Auditor local de C5 (spec jax 2026-09-18-auditor-local-opcion.md): un segundo Ollama,
SOLO CPU, para que auditar una misión contra una máquina con datos de clientes no le mande
esos datos a un proveedor de nube.

Dos piezas de la migración, cada una con su propio guard idempotente:
- `_seed_ollama_cpu_provider`: SOLO si JAX_OLLAMA_CPU_URL está seteada (fail-soft, sin
  puerto hardcodeado). En CI, sin la variable, no siembra nada -- a propósito.
- `_seed_auditor_local_facet`: la faceta 'auditor_local' siempre; su binding SOLO si el
  proveedor ya existe (la FK lo exige).

Y la mitad de `misiones.py` (jax-platform, la vía de producto): una máquina con datos de
clientes es elegible si la compuerta está abierta O si el auditor local está bindeado a un
proveedor REALMENTE local (`provider.is_local`, nunca el nombre de la faceta) -- ver
test_ejecutor_misiones_auditor_local.py para el peor caso (auditor de nube + compuerta
cerrada sigue rechazando)."""
from db.migrations import (
    _MODELO_AUDITOR_LOCAL,
    _PROVIDER_ID_OLLAMA_CPU,
    _seed_auditor_local_facet,
    _seed_ollama_cpu_provider,
)
from tests.identidades import sql


async def _correr(fn):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await fn(cur)
        await conn.commit()


def _limpiar(client):
    # NO borra la fila de `facet`: es un catálogo permanente (la migración la siembra sin
    # condición ninguna) -- otros tests de la misma sesión la dan por viva. Sí borra `model`
    # ANTES que `provider`: _seed_models_and_backfill (otros tests de esta suite llaman
    # run_migrations() de nuevo -- ver test_fix_wave_final.py, test_capability_mode.py,
    # etc.) deriva el catálogo de TODO `facet_binding` en ese instante, no sólo de la
    # semilla original -- si corre mientras el binding de prueba está vivo, dejó una fila
    # en `model` que sobrevive a este DELETE de `facet_binding`/`provider` y bloquea el
    # próximo (FK). Visto en real 2026-09-18: 1451 al borrar `provider`.
    client.portal.call(sql, "DELETE FROM facet_binding WHERE facet_key = 'auditor_local'")
    client.portal.call(sql, "DELETE FROM model WHERE provider_id = %s", (_PROVIDER_ID_OLLAMA_CPU,))
    client.portal.call(sql, "DELETE FROM provider WHERE id = %s", (_PROVIDER_ID_OLLAMA_CPU,))


def test_sin_la_variable_de_entorno_no_siembra_el_proveedor(client, monkeypatch):
    monkeypatch.delenv("JAX_OLLAMA_CPU_URL", raising=False)
    _limpiar(client)
    try:
        client.portal.call(_correr, _seed_ollama_cpu_provider)
        fila = client.portal.call(sql, "SELECT 1 FROM provider WHERE id = %s", (_PROVIDER_ID_OLLAMA_CPU,), True)
        assert fila == (), "sin JAX_OLLAMA_CPU_URL no se puede sembrar un puerto inventado"
    finally:
        _limpiar(client)


def test_con_la_variable_siembra_un_proveedor_local_sin_credencial(client, monkeypatch):
    monkeypatch.setenv("JAX_OLLAMA_CPU_URL", "http://127.0.0.1:11435")
    _limpiar(client)
    try:
        client.portal.call(_correr, _seed_ollama_cpu_provider)
        filas = client.portal.call(
            sql, "SELECT base_url, auth_type, is_local FROM provider WHERE id = %s",
            (_PROVIDER_ID_OLLAMA_CPU,), True)
        # /v1 agregado por la migración, igual que el proveedor 'ollama' existente.
        # is_local=TRUE: nunca compite por la GPU de la Mesa. auth_type='none': ninguna
        # credencial gestionada -- Ollama ignora la cabecera igual.
        assert filas[0] == ("http://127.0.0.1:11435/v1", "none", 1)
    finally:
        _limpiar(client)


def test_la_faceta_se_siembra_siempre_el_binding_solo_si_el_proveedor_existe(client, monkeypatch):
    monkeypatch.delenv("JAX_OLLAMA_CPU_URL", raising=False)
    _limpiar(client)
    try:
        client.portal.call(_correr, _seed_auditor_local_facet)
        facet = client.portal.call(sql, "SELECT transport, auto_selectable FROM facet WHERE `key` = 'auditor_local'",
                                   (), True)
        assert facet[0] == ("ollama", 0)
        binding = client.portal.call(
            sql, "SELECT 1 FROM facet_binding WHERE facet_key = 'auditor_local'", (), True)
        assert binding == (), "sin proveedor la FK no deja bindear -- el binding no debe existir"
    finally:
        _limpiar(client)


def test_con_proveedor_el_binding_apunta_al_modelo_medido(client, monkeypatch):
    monkeypatch.setenv("JAX_OLLAMA_CPU_URL", "http://127.0.0.1:11435")
    _limpiar(client)
    try:
        client.portal.call(_correr, _seed_ollama_cpu_provider)
        client.portal.call(_correr, _seed_auditor_local_facet)
        binding = client.portal.call(
            sql, "SELECT provider_id, model_id, role FROM facet_binding WHERE facet_key = 'auditor_local'", (), True)
        assert binding[0] == (_PROVIDER_ID_OLLAMA_CPU, _MODELO_AUDITOR_LOCAL, "primary")
    finally:
        _limpiar(client)


def test_correr_dos_veces_no_duplica_ni_revienta(client, monkeypatch):
    """Idempotencia: la migración corre en cada arranque de jax-platform."""
    monkeypatch.setenv("JAX_OLLAMA_CPU_URL", "http://127.0.0.1:11435")
    _limpiar(client)
    try:
        for _ in range(2):
            client.portal.call(_correr, _seed_ollama_cpu_provider)
            client.portal.call(_correr, _seed_auditor_local_facet)
        n = client.portal.call(sql, "SELECT COUNT(*) FROM facet_binding WHERE facet_key = 'auditor_local'", (), True)
        assert n[0][0] == 1
    finally:
        _limpiar(client)


def test_el_modelo_del_auditor_local_no_esta_duplicado_en_otro_lugar():
    """Sin hardcoding de segunda mano: sólo _MODELO_AUDITOR_LOCAL declara el modelo."""
    assert _MODELO_AUDITOR_LOCAL == "qwen3:14b"


def test_run_migrations_real_siembra_la_faceta_y_la_clave_de_config(client):
    """El wiring real (run_migrations, no la función suelta): la fixture `client`
    (session-scoped) ya la corrió una vez al levantar la app -- la faceta 'auditor_local'
    y la clave de config tienen que existir aunque este test nunca haya llamado a las
    funciones de arriba directamente."""
    fila = client.portal.call(sql, "SELECT config_value FROM axioma_config WHERE config_key = "
                                   "'ejecutor.auditor_faceta_local'", (), True)
    assert fila and fila[0][0] == "auditor_local"
    existe = client.portal.call(sql, "SELECT 1 FROM facet WHERE `key` = 'auditor_local'", (), True)
    assert existe != (), "run_migrations no sembró la faceta 'auditor_local'"
