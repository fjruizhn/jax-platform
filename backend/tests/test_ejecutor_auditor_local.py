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
test_ejecutor_misiones.py para el peor caso (auditor de nube + compuerta cerrada sigue
rechazando)."""
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


def test_un_cambio_de_puerto_actualiza_el_base_url_en_el_proximo_arranque(client, monkeypatch):
    """Hallazgo BAJO de la revisión (2026-09-18): con INSERT IGNORE, cambiar
    JAX_OLLAMA_CPU_URL después del primer arranque no tenía ningún efecto -- el puerto
    quedaba congelado para siempre, en silencio."""
    monkeypatch.setenv("JAX_OLLAMA_CPU_URL", "http://127.0.0.1:11435")
    _limpiar(client)
    try:
        client.portal.call(_correr, _seed_ollama_cpu_provider)
        monkeypatch.setenv("JAX_OLLAMA_CPU_URL", "http://127.0.0.1:19999")
        client.portal.call(_correr, _seed_ollama_cpu_provider)
        fila = client.portal.call(sql, "SELECT base_url FROM provider WHERE id = %s",
                                  (_PROVIDER_ID_OLLAMA_CPU,), True)
        assert fila[0][0] == "http://127.0.0.1:19999/v1"
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


def test_el_binding_resuelve_model_ref_sin_depender_de_backfill_and_models(client, monkeypatch):
    """Hallazgo MEDIO de la revisión (2026-09-18): las dos semillas estaban acopladas por
    ORDEN (_seed_models_and_backfill, más abajo en run_migrations, era quien completaba
    model_ref). En producción quedó a medias -- model_ref NULL, sin fila en `model` --
    porque se corrieron sueltas, sin ese paso. Acá se llama SÓLO a las dos funciones de
    este módulo, nunca a _seed_models_and_backfill: el binding tiene que resolver igual."""
    monkeypatch.setenv("JAX_OLLAMA_CPU_URL", "http://127.0.0.1:11435")
    _limpiar(client)
    try:
        client.portal.call(_correr, _seed_ollama_cpu_provider)
        client.portal.call(_correr, _seed_auditor_local_facet)
        fila = client.portal.call(
            sql, "SELECT b.model_ref, m.provider_id, m.model_id FROM facet_binding b "
                "JOIN model m ON m.id = b.model_ref WHERE b.facet_key = 'auditor_local'", (), True)
        assert fila and fila[0][0] is not None
        assert fila[0][1:] == (_PROVIDER_ID_OLLAMA_CPU, _MODELO_AUDITOR_LOCAL)
    finally:
        _limpiar(client)


def test_repara_un_binding_que_quedo_con_model_ref_nulo(client, monkeypatch):
    """Reproduce el estado real que quedó en producción (2026-09-18) y comprueba que
    correr la semilla de nuevo lo repara -- no sólo evita el caso nuevo, arregla el
    viejo."""
    monkeypatch.setenv("JAX_OLLAMA_CPU_URL", "http://127.0.0.1:11435")
    _limpiar(client)
    try:
        client.portal.call(_correr, _seed_ollama_cpu_provider)
        # Simula la corrida vieja, desacoplada: binding SIN model_ref, SIN fila en `model`.
        client.portal.call(
            sql, "INSERT INTO facet_binding (facet_key, provider_id, model_id, role) "
                "VALUES ('auditor_local', %s, %s, 'primary')", (_PROVIDER_ID_OLLAMA_CPU, _MODELO_AUDITOR_LOCAL))
        roto = client.portal.call(
            sql, "SELECT model_ref FROM facet_binding WHERE facet_key = 'auditor_local'", (), True)
        assert roto[0][0] is None, "la simulación tiene que arrancar rota (model_ref NULL)"

        client.portal.call(_correr, _seed_auditor_local_facet)

        reparado = client.portal.call(
            sql, "SELECT b.model_ref, m.provider_id FROM facet_binding b JOIN model m ON m.id = b.model_ref "
                "WHERE b.facet_key = 'auditor_local'", (), True)
        assert reparado and reparado[0][0] is not None and reparado[0][1] == _PROVIDER_ID_OLLAMA_CPU
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
    """Sin hardcoding de segunda mano: el LITERAL sólo aparece donde `_MODELO_AUDITOR_LOCAL`
    lo declara -- ningún otro archivo repite el nombre del modelo a mano.

    (Hallazgo BAJO de la revisión, 2026-09-18: la versión anterior sólo comparaba la
    constante consigo misma -- `assert _MODELO_AUDITOR_LOCAL == "qwen3:14b"` es
    tautológico, nunca puede fallar por un hardcodeo real en otro archivo. Esta versión
    grepea el árbol de verdad.)"""
    import re
    from pathlib import Path

    repo_backend = Path(__file__).resolve().parents[1]
    este_archivo = Path(__file__).resolve()
    patron = re.compile(re.escape(f'"{_MODELO_AUDITOR_LOCAL}"'))
    hallados = []
    for path in repo_backend.rglob("*.py"):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        # Se excluye a sí mismo: el docstring de este test cita el literal en prosa (para
        # explicar el hallazgo), no como un segundo hardcodeo real.
        if path.resolve() == este_archivo:
            continue
        if patron.search(path.read_text(encoding="utf-8")):
            hallados.append(path.relative_to(repo_backend).as_posix())
    assert hallados == ["db/migrations.py"], (
        f"el literal {_MODELO_AUDITOR_LOCAL!r} aparece hardcodeado en {hallados}, no sólo en "
        "db/migrations.py -- usá la constante _MODELO_AUDITOR_LOCAL en vez de repetir el "
        "nombre del modelo")


def test_run_migrations_real_siembra_la_faceta_y_la_clave_de_config(client):
    """El wiring real (run_migrations, no la función suelta): la fixture `client`
    (session-scoped) ya la corrió al levantar la app.

    **La clave existe; su VALOR no se afirma acá.** En una base nueva la semilla
    pone `el_juez`; en una que ya venía usando `auditor_local`, el `INSERT
    IGNORE` respeta lo que hay -- las dos cosas son correctas y dependen de la
    base, no del código. El valor de la SEMILLA lo ata
    `test_ejecutor_config_c5.py`, y la relación config↔faceta la ata
    `test_la_faceta_que_la_config_nombra_nunca_esta_retirada`. Afirmarlo también
    acá era atar el ambiente: pasaba local (base vieja) y fallaba en CI (base
    nueva), que es exactamente lo que se busca no tener.
    """
    fila = client.portal.call(sql, "SELECT config_value FROM axioma_config WHERE config_key = "
                                   "'ejecutor.auditor_faceta_local'", (), True)
    assert fila and fila[0][0], "run_migrations no sembró la clave de config"
    existe = client.portal.call(sql, "SELECT 1 FROM facet WHERE `key` = 'auditor_local'", (), True)
    assert existe != (), "la faceta vieja no está: tiene que quedar, retirada"


# --- retiro de la faceta vieja y semilla al día (2026-09-20) ----------------------------------

def _config_auditor_local(client):
    return client.portal.call(
        sql, "SELECT config_value FROM axioma_config "
             "WHERE config_key = 'ejecutor.auditor_faceta_local'", (), True)[0][0]


def _estado_facet(client, clave):
    fila = client.portal.call(sql, "SELECT status FROM facet WHERE `key` = %s", (clave,), True)
    return fila[0][0] if fila else None


def test_la_faceta_vieja_se_retira_SOLO_si_la_config_ya_no_la_nombra(client):
    """Las dos ramas de `_retirar_auditor_local_facet`, no la que toque el ambiente.

    `auditor_local` fue el primer auditor de C5 (`qwen3:14b` sobre CPU) y la
    reemplazó `el_juez`. Se retira con `status='disabled'` y no con DELETE:
    `jacobs/store.py` arma con `facet.status='active'` el conjunto de facetas que
    el planner ACEPTA como destino, y un DELETE perdería contra su propia semilla
    en la primera base nueva.

    Pero NO se retira si la config todavía la nombra: la semilla nueva es
    `INSERT IGNORE` y no pisa lo que ya existe, así que una base que sigue
    usándola quedaría apuntando a una faceta `disabled` -- y la consulta de
    elegibilidad no mira `status`. La primera versión de esta migración tenía
    ese defecto; lo encontró el test de abajo.
    """
    previo = _config_auditor_local(client)
    previo_estado = _estado_facet(client, "auditor_local")
    try:
        # Rama 1: la config la nombra -> se queda activa.
        client.portal.call(sql, "UPDATE axioma_config SET config_value = 'auditor_local' "
                                "WHERE config_key = 'ejecutor.auditor_faceta_local'")
        client.portal.call(sql, "UPDATE facet SET status = 'active' WHERE `key` = 'auditor_local'")
        client.portal.call(_correr_retiro)
        assert _estado_facet(client, "auditor_local") == "active", (
            "se retiró la faceta que la config todavía nombra")

        # Rama 2: la config nombra otra -> se retira.
        client.portal.call(sql, "UPDATE axioma_config SET config_value = 'el_juez' "
                                "WHERE config_key = 'ejecutor.auditor_faceta_local'")
        client.portal.call(_correr_retiro)
        assert _estado_facet(client, "auditor_local") == "disabled"

        # Y no se borró.
        existe = client.portal.call(
            sql, "SELECT 1 FROM facet WHERE `key` = 'auditor_local'", (), True)
        assert existe, "la faceta se BORRÓ: tiene que quedar, retirada"
    finally:
        client.portal.call(sql, "UPDATE axioma_config SET config_value = %s "
                                "WHERE config_key = 'ejecutor.auditor_faceta_local'", (previo,))
        client.portal.call(sql, "UPDATE facet SET status = %s WHERE `key` = 'auditor_local'",
                           (previo_estado,))


async def _correr_retiro():
    from db.connection import get_pool
    from db.migrations import _retirar_auditor_local_facet
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _retirar_auditor_local_facet(cur)


def test_la_faceta_que_la_config_nombra_nunca_esta_retirada(client):
    """El invariante que casi rompo: retirar una faceta y dejar la config
    apuntándole.

    `ejecutor.auditor_faceta_local` es lo que el Ejecutor usa para auditar
    máquinas con datos de clientes. Si esa faceta queda `disabled`, el sistema
    apunta a algo retirado -- y la consulta de elegibilidad NO mira `status`,
    así que la puerta seguiría abriéndose contra una faceta que el planner ya
    no acepta. Se comprueba la relación, no un nombre: sirve igual el día que
    la config apunte a otra cosa.
    """
    faceta = client.portal.call(
        sql, "SELECT config_value FROM axioma_config "
             "WHERE config_key = 'ejecutor.auditor_faceta_local'", (), True)[0][0]
    estado = client.portal.call(
        sql, "SELECT status FROM facet WHERE `key` = %s", (faceta,), True)
    assert estado, f"la config apunta a la faceta {faceta!r}, que NO existe"
    assert estado[0][0] == "active", (
        f"la config apunta a {faceta!r} y está en {estado[0][0]!r}: el auditor local "
        "de las máquinas con datos de clientes es una faceta retirada")
