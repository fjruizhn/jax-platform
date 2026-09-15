"""PR-J (2026-09-14) — los dos escritores de facet_binding verifican el
contrato de dispatch ANTES de escribir.

Incidente: el 2026-09-12 19:40 se aprobó la propuesta #11 (drift
deepseek-v4-flash -> deepseek-flash) y jekyll quedó apuntando a una fila de
`model` sin max_tokens_param ni max_output_tokens. El dispatch
http_openai_compat los exige (fail-closed, correcto) y la faceta se cayó recién
en el primer uso. Estos tests fijan que ni POST /proposals/{id}/approve ni PUT
/facet-bindings/{key} aceptan un modelo que el dispatch de ESA faceta
rechazaría: 409 con código, la propuesta sigue 'pending' y el binding NO
cambia (leído de la DB, no de la respuesta).

Todo lo que escriben va contra jax_memory_test (conftest pisa JAX_DB_NAME) y
se limpia en un finally.
"""
from auth.jwt import create_access_token

USER_ID = "1"  # jax_users.user_id real (FK de approved_by/decided_by)
TENANT_ID = "test-contrato-dispatch-tenant"

# Filas de prueba del catálogo, una por estado del contrato.
SIN_PARAM = "test-contrato-sin-max-tokens-param"
SIN_TOPE = "test-contrato-sin-max-output-tokens"
COMPLETO = "test-contrato-completo"
# Ronda 1: el binding tiene que quedar con el proveedor del modelo. Para una
# faceta ollama (jax_local) la fila sin contrato tiene que ser de ollama; la
# de openai es un modelo completo de OTRO proveedor que el de jekyll.
OLLAMA_SIN_PARAM = "test-contrato-ollama-sin-max-tokens-param"
OPENAI_COMPLETO = "test-contrato-openai-completo"
_FILAS = {
    SIN_PARAM: ("deepseek", None, 4096),
    SIN_TOPE: ("deepseek", "max_tokens", None),
    COMPLETO: ("deepseek", "max_tokens", 4096),
    OLLAMA_SIN_PARAM: ("ollama", None, None),
    OPENAI_COMPLETO: ("openai", "max_completion_tokens", 4096),
}


def _headers():
    return {"Authorization": f"Bearer {create_access_token(USER_ID, TENANT_ID, 'superadmin')}"}


async def _q(sql, params=(), commit=False):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        if commit:
            await conn.commit()
    return rows


async def _crear_filas():
    ids = {}
    for model_id, (provider_id, param, tope) in _FILAS.items():
        await _q(
            "INSERT INTO model (provider_id, model_id, max_tokens_param, max_output_tokens, "
            "source, source_checked_at) VALUES (%s, %s, %s, %s, 'manual', NOW()) "
            "ON DUPLICATE KEY UPDATE max_tokens_param=VALUES(max_tokens_param), "
            "max_output_tokens=VALUES(max_output_tokens)",
            (provider_id, model_id, param, tope), commit=True,
        )
        rows = await _q("SELECT id FROM model WHERE provider_id=%s AND model_id=%s", (provider_id, model_id))
        ids[model_id] = rows[0][0]
    return ids


async def _borrar_filas(ids):
    for ref in ids.values():
        await _q("DELETE FROM model_binding_proposal WHERE proposed_model_ref=%s OR current_model_ref=%s",
                 (ref, ref), commit=True)
        await _q("DELETE FROM model WHERE id=%s", (ref,), commit=True)


async def _binding(facet_key):
    """La fila completa que los endpoints escriben, procedencia incluida: un
    409 no puede tocar ni approved_by/approved_at."""
    rows = await _q(
        "SELECT provider_id, model_ref, model_id, approved_by, approved_at FROM facet_binding "
        "WHERE facet_key=%s AND role='primary'", (facet_key,),
    )
    return rows[0]


async def _restaurar(facet_key, binding):
    provider_id, model_ref, model_id, approved_by, approved_at = binding
    await _q(
        "UPDATE facet_binding SET provider_id=%s, model_ref=%s, model_id=%s, "
        "approved_by=%s, approved_at=%s "
        "WHERE facet_key=%s AND role='primary'",
        (provider_id, model_ref, model_id, approved_by, approved_at, facet_key), commit=True,
    )


async def _propuesta(facet_key, proposed_ref):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT model_ref FROM facet_binding WHERE facet_key=%s AND role='primary'", (facet_key,))
            (current,) = await cur.fetchone()
            await cur.execute(
                "INSERT INTO model_binding_proposal "
                "(facet_key, current_model_ref, proposed_model_ref, reason, detail) "
                "VALUES (%s, %s, %s, 'drift_detected', 'test PR-J')",
                (facet_key, current, proposed_ref),
            )
            pid = cur.lastrowid
        await conn.commit()
    return pid


async def _estado_propuesta(pid):
    rows = await _q("SELECT status, decided_by FROM model_binding_proposal WHERE id=%s", (pid,))
    return rows[0]


def _aprobar(client, facet_key, model_id):
    """Aprueba una propuesta facet_key -> model_id y devuelve
    (respuesta, propuesta_id, binding_antes, binding_despues). Restaura el
    binding y borra las filas de prueba pase lo que pase."""
    ids = client.portal.call(_crear_filas)
    antes = client.portal.call(_binding, facet_key)
    try:
        pid = client.portal.call(_propuesta, facet_key, ids[model_id])
        resp = client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())
        despues = client.portal.call(_binding, facet_key)
        estado = client.portal.call(_estado_propuesta, pid)
        return resp, estado, antes, despues, ids[model_id]
    finally:
        client.portal.call(_restaurar, facet_key, antes)
        client.portal.call(_borrar_filas, ids)


def _put(client, facet_key, model_id, provider_id=None):
    """provider_id: el del request; por defecto, el del propio modelo."""
    ids = client.portal.call(_crear_filas)
    antes = client.portal.call(_binding, facet_key)
    try:
        resp = client.put(
            f"/api/admin/facet-bindings/{facet_key}",
            json={"provider_id": provider_id or _FILAS[model_id][0], "model_ref": ids[model_id]},
            headers=_headers(),
        )
        despues = client.portal.call(_binding, facet_key)
        return resp, antes, despues, ids[model_id]
    finally:
        client.portal.call(_restaurar, facet_key, antes)
        client.portal.call(_borrar_filas, ids)


def _assert_409_de_contrato(resp, campos):
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "modelo_sin_contrato_de_dispatch"
    assert detail["campos"] == campos
    for campo in campos:
        assert campo in detail["message"]  # el mensaje dice qué falta


# ---------------------------------------------------------------- approve ---

def test_approve_hacia_modelo_sin_max_tokens_param_es_409_y_no_toca_nada(client):
    resp, estado, antes, despues, _ = _aprobar(client, "jekyll", SIN_PARAM)
    _assert_409_de_contrato(resp, ["max_tokens_param"])
    assert estado == ("pending", None), "la propuesta rechazada por contrato no se marca decidida"
    assert despues == antes, "el binding cambió aunque la aprobación fue rechazada"


def test_approve_hacia_modelo_sin_max_output_tokens_es_409_y_no_toca_nada(client):
    resp, estado, antes, despues, _ = _aprobar(client, "jekyll", SIN_TOPE)
    _assert_409_de_contrato(resp, ["max_output_tokens"])
    assert estado == ("pending", None)
    assert despues == antes


def test_approve_con_contrato_completo_sigue_como_hoy(client, monkeypatch):
    encoladas = []
    import jax_engine.background as background
    monkeypatch.setattr(background, "add_safe_task", lambda bt, fn, *a: encoladas.append((fn.__name__, a)))
    resp, estado, antes, despues, ref = _aprobar(client, "jekyll", COMPLETO)
    assert resp.status_code == 200, resp.text
    assert estado[0] == "approved"
    assert despues[1] == ref
    assert encoladas == [("probe_after_rebind", ("jekyll",))]


def test_approve_de_faceta_que_no_lee_el_contrato_no_se_bloquea(client):
    """jax_local es ollama: max_tokens_param no significa nada para su
    dispatch, bloquear sería inventar un requisito."""
    resp, estado, antes, despues, ref = _aprobar(client, "jax_local", OLLAMA_SIN_PARAM)
    assert resp.status_code == 200, resp.text
    assert estado[0] == "approved"
    assert despues[1] == ref


# -------------------------------------------------------------------- PUT ---

def test_put_hacia_modelo_sin_max_tokens_param_es_409_y_no_toca_nada(client):
    resp, antes, despues, _ = _put(client, "jekyll", SIN_PARAM)
    _assert_409_de_contrato(resp, ["max_tokens_param"])
    assert despues == antes


def test_put_hacia_modelo_sin_max_output_tokens_es_409_y_no_toca_nada(client):
    resp, antes, despues, _ = _put(client, "jekyll", SIN_TOPE)
    _assert_409_de_contrato(resp, ["max_output_tokens"])
    assert despues == antes


def test_put_con_contrato_completo_sigue_como_hoy(client, monkeypatch):
    encoladas = []
    import jax_engine.background as background
    monkeypatch.setattr(background, "add_safe_task", lambda bt, fn, *a: encoladas.append((fn.__name__, a)))
    resp, antes, despues, ref = _put(client, "jekyll", COMPLETO)
    assert resp.status_code == 200, resp.text
    assert despues[:2] == ("deepseek", ref)
    assert encoladas == [("probe_after_rebind", ("jekyll",))]


def test_put_de_faceta_subprocess_no_se_bloquea(client):
    resp, antes, despues, ref = _put(client, "hyde", SIN_PARAM)
    assert resp.status_code == 200, resp.text
    assert despues[1] == ref


def test_put_sin_ninguno_de_los_dos_nombra_los_dos_campos():
    """Quien aprueba ve de una vez todo lo que falta sembrar."""
    from contrato_dispatch import faltantes_del_contrato
    faltan = faltantes_del_contrato("http_openai_compat", "deepseek-flash", None, None)
    assert [campo for campo, _ in faltan] == ["max_tokens_param", "max_output_tokens"]
    assert faltantes_del_contrato("http_gemini", "x", None, None) == []
    assert faltantes_del_contrato("http_openai_compat", "x", "max_tokens", 393216) == []


def test_chat_usa_los_mismos_validadores_que_los_admins():
    """Una sola regla: si alguien vuelve a definir los validadores en chat.py,
    el admin y el dispatch pueden divergir."""
    import api.chat as chat
    import contrato_dispatch
    assert chat._max_tokens_field is contrato_dispatch._max_tokens_field
    assert chat._max_output_tokens_value is contrato_dispatch._max_output_tokens_value
    assert chat.ModelDispatchConfigError is contrato_dispatch.ModelDispatchConfigError


# ------------------------------------------------ Ronda 1: proveedor -------
# Medido contra los lectores (ver el docstring de
# contrato_dispatch.detalle_si_rompe_el_contrato): los resolvers sacan URL y
# credencial de facet_binding.provider_id y el modelo de model_ref; approve
# cambia model_ref sin tocar provider_id.

def _assert_409_de_proveedor(resp, provider_binding, provider_modelo):
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "modelo_de_otro_proveedor"
    assert detail["campos"] == ["provider_id"]
    assert (detail["provider_binding"], detail["provider_modelo"]) == (provider_binding, provider_modelo)


def test_approve_hacia_modelo_de_otro_proveedor_es_409_y_no_toca_nada(client, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        resp, estado, antes, despues, _ = _aprobar(client, "jekyll", OPENAI_COMPLETO)
    _assert_409_de_proveedor(resp, antes[0], "openai")
    # El WARNING nombra el motivo real, no "sin contrato de dispatch".
    avisos = [r.getMessage() for r in caplog.records if "escritura de facet_binding rechazada" in r.getMessage()]
    assert avisos and "modelo de otro proveedor" in avisos[0], avisos
    assert "sin contrato de dispatch" not in avisos[0]
    assert estado == ("pending", None)
    assert despues == antes


def test_put_con_provider_distinto_al_del_modelo_es_409_y_no_toca_nada(client):
    resp, antes, despues, _ = _put(client, "jekyll", COMPLETO, provider_id="openai")
    _assert_409_de_proveedor(resp, "openai", "deepseek")
    assert despues == antes


# ------------------------------------------------ Ronda 1: logs ------------

def test_el_guard_loguea_warning_y_no_dispatch_abortado(client, caplog):
    """Rechazar una aprobación no aborta ningún dispatch: el log no puede
    decir que sí."""
    import logging
    with caplog.at_level(logging.DEBUG):
        resp, *_ = _aprobar(client, "jekyll", SIN_PARAM)
    assert resp.status_code == 409, resp.text
    mensajes = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert not [m for _, m in mensajes if "dispatch abortado" in m], mensajes
    avisos = [m for nivel, m in mensajes if nivel == logging.WARNING and "escritura de facet_binding rechazada" in m]
    assert avisos and "jekyll" in avisos[0] and SIN_PARAM in avisos[0]


# Nombre único: identifica la fila config_error que _invoke_facet escribe en
# facet_health_event (jax_memory_test) para borrarla al terminar y no dejar a
# jekyll "caída" para otros tests ni para quien mire la base de test.
_MODELO_SIN_SEMBRAR = "modelo-sin-sembrar-prj-ronda2"


async def _dispatch_real_sin_contrato(chat):
    """El camino de dispatch de verdad (_invoke_facet -> _call_openai_compat
    con los validadores reales); _invoke_facet_dispatch viene reemplazado por
    monkeypatch para evitar solo resolver la faceta y el gate de las_manos.
    Devuelve la excepción en vez de dejarla escapar del portal (nada de
    pytest.raises dentro de client.portal.call)."""
    try:
        await chat._invoke_facet("jekyll", {}, "test-user", "hola")
    except chat.ModelDispatchConfigError as e:
        return e
    return None


async def _eventos_de_la_prueba(borrar=False):
    patron = f"%{_MODELO_SIN_SEMBRAR}%"
    if borrar:
        await _q("DELETE FROM facet_health_event WHERE facet='jekyll' AND detail LIKE %s",
                 (patron,), commit=True)
    rows = await _q("SELECT outcome FROM facet_health_event WHERE facet='jekyll' AND detail LIKE %s",
                    (patron,))
    return [outcome for (outcome,) in rows]


def test_el_dispatch_real_sigue_logueando_dispatch_abortado_con_el_update(client, caplog, monkeypatch):
    import logging
    import api.chat as chat

    async def dispatch(facet, config, user_id, message, semantic_context, grounding=None):
        await chat._call_openai_compat(
            "https://api.example.com/v1", "sk-fake", _MODELO_SIN_SEMBRAR,
            "system", [], "hola", None, 4096,
        )
    monkeypatch.setattr(chat, "_invoke_facet_dispatch", dispatch)
    try:
        with caplog.at_level(logging.ERROR, logger="api.chat"):
            error = client.portal.call(_dispatch_real_sin_contrato, chat)
        assert isinstance(error, chat.ModelDispatchConfigError), "el dispatch dejó de fallar cerrado"
        errores = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        logueado = [m for m in errores if "dispatch abortado" in m]
        assert logueado, errores
        assert _MODELO_SIN_SEMBRAR in logueado[0]
        assert "UPDATE model SET max_tokens_param" in logueado[0]
        # Y el envoltorio registró el config_error (lo que se limpia abajo).
        assert client.portal.call(_eventos_de_la_prueba) == ["config_error"]
    finally:
        assert client.portal.call(_eventos_de_la_prueba, True) == []
