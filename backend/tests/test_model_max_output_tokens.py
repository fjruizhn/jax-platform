"""model.max_output_tokens — la columna nace CON lector y CON test.

Segunda mitad del incidente de thot (2026-08-27). Arreglado el NOMBRE del
parametro de limite de salida (model.max_tokens_param,
tests/test_model_max_tokens_param.py), la misma API contesto por el VALOR:

    HTTP 400: "max_tokens is too large: 131072. This model supports at most
    128000 completion tokens, whereas you provided 131072."

_call_openai_compat mandaba 131072 fijo (constante _MAX_OUTPUT_TOKENS), que era
universal mientras todos los modelos del camino openai-compat lo aceptaran.
Dejo de serlo: el tope de completion es una propiedad estable POR MODELO, del
mismo eje que supports_tool_use / context_window, y vive en la misma fila.

NO se deriva de context_window — verificado contra la DB en vivo, no supuesto:
gpt-5.6-terra tiene context_window=1050000 (la ventana TOTAL, entrada+salida)
contra un tope de completion de 128000. Son dos hechos distintos.

Un modelo sin valor FALLA RUIDOSO en vez de asumir uno (decision del dueño,
textual: "prefiero que un modelo sin valor falle ruidoso a que asuma"): un
default de 131072 reproduciria este mismo incidente contra el proximo modelo
con tope mas bajo, y uno "conservador" truncaria respuestas de modelos de
razonamiento en silencio.

Los tests que tocan DB pasan por `client.portal.call(...)` a proposito: el
pool de aiomysql de la app vive en el loop del portal de esa sesion, y
tocarlo desde otro loop da el RuntimeError de "attached to a different loop"
que ya afecta a otras suites de este repo.
"""
import http_client
import pytest

from api.chat import (
    ModelDispatchConfigError,
    _call_openai_compat,
    _max_output_tokens_value,
)
from db.migrations import (
    _MODEL_MAX_OUTPUT_TOKENS_SEED,
    _seed_model_max_output_tokens,
)


# --------------------------------------------------------------------------
# Lector: el valor del limite sale del catalogo, no de una constante
# --------------------------------------------------------------------------

def test_model_with_a_lower_cap_gets_its_own_value():
    """El caso de thot/gpt-5.6-terra: 128000, no los 131072 que el codigo
    mandaba fijo. Es el valor que la propia API nombro en el HTTP 400."""
    assert _max_output_tokens_value("gpt-5.6-terra", 128000) == 128000


def test_model_that_accepts_the_old_ceiling_still_gets_it():
    """El caso de jekyll/deepseek-v4-flash y ada/glm-5.3: 131072 sigue siendo
    su tope. Es lo que hace que 'bajar la constante a 128000' no sea un fix —
    arregla la instancia y le recorta la salida a la clase."""
    assert _max_output_tokens_value("deepseek-v4-flash", 131072) == 131072


def test_null_fails_loud_naming_the_model_and_the_remedy():
    """Requisito explicito: NULL no asume, falla — y el error tiene que
    servirle a un operador para saber exactamente que fila sembrar."""
    with pytest.raises(ModelDispatchConfigError) as exc:
        _max_output_tokens_value("modelo-recien-aprobado", None)
    msg = str(exc.value)
    assert "modelo-recien-aprobado" in msg, "el error no nombra el modelo"
    assert "max_output_tokens" in msg
    assert "UPDATE model SET max_output_tokens" in msg, "el error no dice como arreglarlo"
    assert "context_window" in msg, (
        "el error no advierte contra el atajo equivocado (derivarlo de la "
        "ventana total), que es justo el que un operador apurado tomaria"
    )


def test_null_carries_the_update_in_the_exception_and_the_validator_does_not_log(caplog):
    """El 502 que ve el usuario trunca el mensaje; el operador lee el log. El
    UPDATE viaja completo en la excepcion, y el ERROR "dispatch abortado" lo
    escribe el camino de dispatch (api/chat.py::_invoke_facet, ver
    test_contrato_dispatch_al_aprobar.py), NO el validador: desde 2026-09-14
    lo usan tambien los admins, donde no se aborta ningun dispatch."""
    import logging

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ModelDispatchConfigError) as exc:
            _max_output_tokens_value("modelo-sin-sembrar", None)
    assert "modelo-sin-sembrar" in str(exc.value)
    assert "UPDATE model SET max_output_tokens" in str(exc.value)
    assert not [r for r in caplog.records if "dispatch abortado" in r.getMessage()]


@pytest.mark.parametrize("valor_imposible", [0, -1, "131072", 131072.0, True])
def test_impossible_values_are_rejected_instead_of_going_on_the_wire(valor_imposible):
    """Defensa en profundidad contra un valor que no pudo venir de un seed
    (una migracion a mano, un 0 heredado de un backfill): un limite invalido
    hace que la API devuelva vacio o un 400, con un modo de falla que se
    confunde con un error real del proveedor."""
    with pytest.raises(ModelDispatchConfigError) as exc:
        _max_output_tokens_value("modelo-x", valor_imposible)
    assert "max_output_tokens" in str(exc.value)


# --------------------------------------------------------------------------
# Dispatch: el valor elegido es el que de verdad viaja en el body
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        pass


class _BodyCapturingClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def _dispatch(client, max_tokens_param, max_output_tokens, model="modelo-x"):
    fake = _BodyCapturingClient(_FakeResponse({"choices": [{"message": {"content": "ok"}}]}))
    original = http_client._client
    http_client._client = fake
    try:
        client.portal.call(
            _call_openai_compat, "https://api.example.com/v1", "sk-fake", model,
            "system", [], "hola", max_tokens_param, max_output_tokens, None,
        )
    finally:
        http_client._client = original
    return fake


def test_dispatch_sends_the_models_own_cap_not_the_old_constant(client):
    """Reproduccion directa del incidente: thot manda 128000 bajo el nombre
    nuevo. Si viajara 131072, es el HTTP 400 otra vez."""
    fake = _dispatch(client, "max_completion_tokens", 128000, model="gpt-5.6-terra")
    body = fake.calls[0][1]["json"]
    assert body["max_completion_tokens"] == 128000
    assert 131072 not in body.values(), (
        "viajo el valor viejo hardcodeado: es exactamente el HTTP 400 que "
        "tumbo a thot ('max_tokens is too large: 131072')"
    )


def test_dispatch_is_behavior_identical_for_the_models_that_worked(client):
    """jekyll/ada mandan 131072 bajo el nombre viejo — byte por byte el mismo
    body que antes de esta columna. Cambio de comportamiento CERO para los que
    no estaban rotos."""
    fake = _dispatch(client, "max_tokens", 131072, model="deepseek-v4-flash")
    body = fake.calls[0][1]["json"]
    assert body["max_tokens"] == 131072
    assert "max_completion_tokens" not in body


def test_dispatch_with_null_never_reaches_the_provider(client):
    """La falla ruidosa corta ANTES de la llamada saliente: un modelo sin
    sembrar no gasta una llamada (ni tokens) para descubrir lo que el catalogo
    deberia declarar."""
    fake = _BodyCapturingClient(_FakeResponse({"choices": [{"message": {"content": "ok"}}]}))
    original = http_client._client
    http_client._client = fake
    try:
        with pytest.raises(ModelDispatchConfigError):
            client.portal.call(
                _call_openai_compat, "https://api.example.com/v1", "sk-fake",
                "modelo-sin-sembrar", "system", [], "hola", "max_tokens", None, None,
            )
    finally:
        http_client._client = original
    assert fake.calls == [], "no debe haber ni una llamada al proveedor"


# --------------------------------------------------------------------------
# Migracion + seed: la columna existe y esta poblada tras run_migrations()
# --------------------------------------------------------------------------

async def _fetch(sql, params=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()


async def _exec(sql, params=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)


async def _run_seed():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _seed_model_max_output_tokens(cur)
        await conn.commit()


def test_column_exists_after_run_migrations(client):
    """El `client` fixture entra TestClient como context manager, o sea que el
    lifespan de la app ya corrio run_migrations() sobre jax_memory_test."""
    rows = client.portal.call(
        _fetch,
        "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'model' "
        "AND COLUMN_NAME = 'max_output_tokens'",
    )
    assert rows, "run_migrations() no creo model.max_output_tokens"
    data_type, is_nullable, default = rows[0]
    assert data_type == "int"
    assert is_nullable == "YES"
    # MariaDB reporta "sin DEFAULT" en una columna nullable como el literal
    # 'NULL' (string), no como NULL — mismo valor que context_window/digest.
    assert default in (None, "NULL"), (
        f"COLUMN_DEFAULT={default!r}: un DEFAULT convierte 'no declarado' en "
        f"una suposicion silenciosa — justo lo que esta columna existe para impedir"
    )


def test_seed_populates_every_verified_model_present_in_the_catalog(client):
    """Se afirma sobre las filas que existen en esta DB: jax_memory_test no
    tiene todo el catalogo de produccion, pero ninguna fila sembrable puede
    quedar NULL.

    Arranca con esas filas en NULL (PR-L ronda 2, 2026-09-14): la semilla
    solo llena NULL a propósito (no pisa un valor manual), así que afirmar
    sobre el valor que la fila TENGA hacía depender el test de lo que otro
    escribió antes -- la prueba de carga de PR-L dejó deepseek-v4-flash en
    393216 y este test falló sin que la semilla cambiara. Así se prueba la
    semilla de verdad, y la fila queda en su valor sembrado."""
    for provider_id, model_id, _expected in _MODEL_MAX_OUTPUT_TOKENS_SEED:
        client.portal.call(
            _commit, "UPDATE model SET max_output_tokens = NULL WHERE provider_id = %s AND model_id = %s",
            (provider_id, model_id))
    client.portal.call(_run_seed)
    for provider_id, model_id, expected in _MODEL_MAX_OUTPUT_TOKENS_SEED:
        rows = client.portal.call(
            _fetch,
            "SELECT max_output_tokens FROM model WHERE provider_id = %s AND model_id = %s",
            (provider_id, model_id),
        )
        if not rows:
            continue  # esa fila del catalogo no existe en esta DB
        assert rows[0][0] == expected, f"{provider_id}/{model_id}"


def test_seed_covers_thot_with_its_lower_cap_and_leaves_the_rest_at_131072(client):
    """Guard contra las dos regresiones mas probables de este cambio: sembrar
    solo el de thot deja a jekyll y ada en NULL (o sea, tumbados por el fallo
    ruidoso), y sembrar 128000 parejo les recorta la salida sin motivo."""
    seeded = {(p, m): v for p, m, v in _MODEL_MAX_OUTPUT_TOKENS_SEED}
    assert seeded[("openai", "gpt-5.6-terra")] == 128000
    assert seeded[("deepseek", "deepseek-v4-flash")] == 131072
    assert seeded[("zhipu", "glm-5.3")] == 131072
    assert seeded[("moonshot", "kimi-k3")] == 131072


def test_seed_is_idempotent_and_does_not_overwrite_a_manual_value(client):
    """Mismo guard que _seed_model_max_tokens_param: WHERE ... IS NULL."""
    row = client.portal.call(
        _fetch,
        "SELECT id, max_output_tokens FROM model "
        "WHERE provider_id='deepseek' AND model_id='deepseek-v4-flash'",
    )
    assert row, "deepseek-v4-flash deberia existir en jax_memory_test"
    model_row_id, previous = row[0]
    client.portal.call(
        _exec, "UPDATE model SET max_output_tokens=64000 WHERE id=%s", (model_row_id,)
    )
    try:
        client.portal.call(_run_seed)
        after = client.portal.call(
            _fetch, "SELECT max_output_tokens FROM model WHERE id=%s", (model_row_id,)
        )
        assert after[0][0] == 64000, "el seed piso un valor puesto a mano"
    finally:
        client.portal.call(
            _exec, "UPDATE model SET max_output_tokens=%s WHERE id=%s",
            (previous, model_row_id),
        )
        client.portal.call(_run_seed)


def test_seed_leaves_unverified_models_null(client):
    """No se adivina, ni por proveedor ni por context_window: un modelo fuera
    de la lista verificada queda NULL y falla ruidoso cuando alguien lo bindee.
    gpt-5.5 (el modelo ANTERIOR de thot, todavia en el catalogo) es el caso
    real."""
    rows = client.portal.call(
        _fetch,
        "SELECT max_output_tokens FROM model WHERE provider_id='openai' AND model_id='gpt-5.5'",
    )
    if rows:
        assert rows[0][0] is None, (
            "gpt-5.5 no esta en la lista verificada: sembrarlo por parecido de "
            "proveedor es exactamente la suposicion que esta columna elimina"
        )


def test_the_cap_is_not_derivable_from_context_window(client):
    """El dato clave que justifica una columna nueva en vez de una formula.
    Si algun dia alguien propone 'derivarlo de context_window', este test dice
    por que no: para el mismo modelo, los dos numeros no coinciden ni por
    fraccion fija."""
    rows = client.portal.call(
        _fetch,
        "SELECT context_window, max_output_tokens FROM model "
        "WHERE provider_id='openai' AND model_id='gpt-5.6-terra'",
    )
    if not rows:
        pytest.skip("gpt-5.6-terra no esta en esta DB")
    client.portal.call(_run_seed)
    context_window, cap = client.portal.call(
        _fetch,
        "SELECT context_window, max_output_tokens FROM model "
        "WHERE provider_id='openai' AND model_id='gpt-5.6-terra'",
    )[0]
    assert cap == 128000
    if context_window is not None:
        assert context_window != cap, (
            "si fueran iguales, mandar context_window habria funcionado y esta "
            "columna sobraria — no es el caso en produccion (1050000 vs 128000)"
        )


# --------------------------------------------------------------------------
# Plomeria: el dato llega del catalogo al despachador
# --------------------------------------------------------------------------

def test_resolve_facet_carries_max_output_tokens_from_the_model_row(client):
    """El JOIN de facet_resolver._query_facet ya traia model.model_id y
    max_tokens_param; ahora tambien max_output_tokens — una sola fuente de
    verdad, la misma fila."""
    import facet_resolver

    # Centinela propio sobre la fila que jekyll tiene HOY (PR-L ronda 2): el
    # test prueba la plomería (el valor de la fila llega al ResolvedFacet), no
    # qué valor dejó en esa fila otro test o la prueba de carga.
    (ref, antes), = client.portal.call(
        _fetch,
        "SELECT m.id, m.max_output_tokens FROM facet_binding b JOIN model m ON m.id = b.model_ref "
        "WHERE b.facet_key = 'jekyll' AND b.role = 'primary'",
    )
    centinela = 4321
    client.portal.call(_commit, "UPDATE model SET max_output_tokens = %s WHERE id = %s", (centinela, ref))
    try:
        facet_resolver._cache.pop("jekyll", None)
        resolved = client.portal.call(facet_resolver.resolve_facet, "jekyll")
        assert resolved.transport == "http_openai_compat"
        assert resolved.max_output_tokens == centinela, (
            f"jekyll resolvio a {resolved.model!r} con "
            f"max_output_tokens={resolved.max_output_tokens!r}"
        )
    finally:
        client.portal.call(_commit, "UPDATE model SET max_output_tokens = %s WHERE id = %s", (antes, ref))
        facet_resolver._cache.pop("jekyll", None)


# --------------------------------------------------------------------------
# PR-J (2026-09-14): deepseek-flash / deepseek-v4-pro, los modelos vigentes de
# DeepSeek tras retirar deepseek-v4-flash (jekyll caida desde 2026-09-12).
# --------------------------------------------------------------------------

_DEEPSEEK_VIGENTES = ("deepseek-flash", "deepseek-v4-pro")


async def _commit(sql, params=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
        await conn.commit()


def test_seed_declara_393216_para_los_deepseek_vigentes():
    """Doc oficial (api-docs.deepseek.com, 2026-09-14): max_tokens va de 1 a
    393216 (384K). Decision de Fernando: el maximo documentado."""
    seeded = {(p, m): v for p, m, v in _MODEL_MAX_OUTPUT_TOKENS_SEED}
    for model_id in _DEEPSEEK_VIGENTES:
        assert seeded[("deepseek", model_id)] == 393216, model_id


def test_seed_siembra_los_deepseek_vigentes_en_la_db_sin_pisar_valores(client):
    insertadas = []
    for model_id in _DEEPSEEK_VIGENTES:
        if not client.portal.call(
            _fetch, "SELECT id FROM model WHERE provider_id='deepseek' AND model_id=%s", (model_id,),
        ):
            client.portal.call(
                _commit,
                "INSERT INTO model (provider_id, model_id, source, source_checked_at) "
                "VALUES ('deepseek', %s, 'provider_api', NOW())", (model_id,),
            )
            insertadas.append(model_id)
    try:
        client.portal.call(
            _commit,
            "UPDATE model SET max_output_tokens=NULL WHERE provider_id='deepseek' AND model_id='deepseek-flash'",
        )
        client.portal.call(
            _commit,
            "UPDATE model SET max_output_tokens=8000 WHERE provider_id='deepseek' AND model_id='deepseek-v4-pro'",
        )
        client.portal.call(_run_seed)
        (flash,), = client.portal.call(
            _fetch, "SELECT max_output_tokens FROM model WHERE provider_id='deepseek' AND model_id='deepseek-flash'")
        (pro,), = client.portal.call(
            _fetch, "SELECT max_output_tokens FROM model WHERE provider_id='deepseek' AND model_id='deepseek-v4-pro'")
        assert flash == 393216
        assert pro == 8000, "el seed piso un valor puesto a mano"
    finally:
        for model_id in _DEEPSEEK_VIGENTES:
            if model_id in insertadas:
                client.portal.call(
                    _commit, "DELETE FROM model WHERE provider_id='deepseek' AND model_id=%s", (model_id,))
            else:
                client.portal.call(
                    _commit, "UPDATE model SET max_output_tokens=NULL "
                    "WHERE provider_id='deepseek' AND model_id=%s", (model_id,))
        client.portal.call(_run_seed)


# --------------------------------------------------------------------------
# PR-L ronda 3 (2026-09-14, decisión de Fernando ~20:50): el modelo del
# binding de jax_local en producción, ollama/qwen3.6:35b-a3b-q4_K_M (hoy
# NULL/NULL). Con PR-K todo camino Ollama exige max_output_tokens: sin esta
# siembra jax_local dejaría de despachar.
# --------------------------------------------------------------------------

_QWEN_JAX_LOCAL = ("ollama", "qwen3.6:35b-a3b-q4_K_M")


def test_seed_declara_262144_para_el_qwen_de_jax_local():
    """Fuente: `ollama show` (qwen35moe 36.0B, context length 262144) y la
    doc oficial de Ollama (num_predict default -1 = generación sin tope).
    Decisión de Fernando: el contexto como tope."""
    seeded = {(p, m): v for p, m, v in _MODEL_MAX_OUTPUT_TOKENS_SEED}
    assert seeded[_QWEN_JAX_LOCAL] == 262144


def test_el_qwen_de_jax_local_no_siembra_max_tokens_param():
    """Medido en contrato_dispatch.py: ollama no está en
    TRANSPORTS_CON_CONTRATO_DE_DISPATCH; el NOMBRE del parámetro solo lo lee
    _call_openai_compat. Sembrarlo para ollama sería inventar un dato."""
    from contrato_dispatch import TRANSPORTS_CON_CONTRATO_DE_DISPATCH
    from db.migrations import _MODEL_MAX_TOKENS_PARAM_SEED
    assert "ollama" not in TRANSPORTS_CON_CONTRATO_DE_DISPATCH
    assert _QWEN_JAX_LOCAL not in {(p, m) for p, m, _v in _MODEL_MAX_TOKENS_PARAM_SEED}


def test_seed_siembra_el_qwen_de_jax_local_en_la_db_sin_pisar_valores(client):
    provider_id, model_id = _QWEN_JAX_LOCAL
    existia = bool(client.portal.call(
        _fetch, "SELECT id FROM model WHERE provider_id=%s AND model_id=%s", (provider_id, model_id)))
    antes = None
    if existia:
        (antes,), = client.portal.call(
            _fetch, "SELECT max_output_tokens FROM model WHERE provider_id=%s AND model_id=%s",
            (provider_id, model_id))
    else:
        client.portal.call(
            _commit, "INSERT INTO model (provider_id, model_id, source, source_checked_at) "
            "VALUES (%s, %s, 'provider_api', NOW())", (provider_id, model_id))
    try:
        # NULL -> la semilla pone 262144 (WHERE IS NULL).
        client.portal.call(
            _commit, "UPDATE model SET max_output_tokens=NULL WHERE provider_id=%s AND model_id=%s",
            (provider_id, model_id))
        client.portal.call(_run_seed)
        (valor,), = client.portal.call(
            _fetch, "SELECT max_output_tokens FROM model WHERE provider_id=%s AND model_id=%s",
            (provider_id, model_id))
        assert valor == 262144

        # Un valor puesto a mano no se pisa.
        client.portal.call(
            _commit, "UPDATE model SET max_output_tokens=8000 WHERE provider_id=%s AND model_id=%s",
            (provider_id, model_id))
        client.portal.call(_run_seed)
        (valor,), = client.portal.call(
            _fetch, "SELECT max_output_tokens FROM model WHERE provider_id=%s AND model_id=%s",
            (provider_id, model_id))
        assert valor == 8000, "la semilla pisó un valor puesto a mano"
    finally:
        if existia:
            client.portal.call(
                _commit, "UPDATE model SET max_output_tokens=%s WHERE provider_id=%s AND model_id=%s",
                (antes, provider_id, model_id))
        else:
            client.portal.call(
                _commit, "DELETE FROM model WHERE provider_id=%s AND model_id=%s", (provider_id, model_id))
