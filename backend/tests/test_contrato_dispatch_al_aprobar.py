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
_FILAS = {
    SIN_PARAM: (None, 4096),
    SIN_TOPE: ("max_tokens", None),
    COMPLETO: ("max_tokens", 4096),
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
    for model_id, (param, tope) in _FILAS.items():
        await _q(
            "INSERT INTO model (provider_id, model_id, max_tokens_param, max_output_tokens, "
            "source, source_checked_at) VALUES ('deepseek', %s, %s, %s, 'manual', NOW()) "
            "ON DUPLICATE KEY UPDATE max_tokens_param=VALUES(max_tokens_param), "
            "max_output_tokens=VALUES(max_output_tokens)",
            (model_id, param, tope), commit=True,
        )
        rows = await _q("SELECT id FROM model WHERE provider_id='deepseek' AND model_id=%s", (model_id,))
        ids[model_id] = rows[0][0]
    return ids


async def _borrar_filas(ids):
    for ref in ids.values():
        await _q("DELETE FROM model_binding_proposal WHERE proposed_model_ref=%s OR current_model_ref=%s",
                 (ref, ref), commit=True)
        await _q("DELETE FROM model WHERE id=%s", (ref,), commit=True)


async def _binding(facet_key):
    rows = await _q(
        "SELECT provider_id, model_ref, model_id FROM facet_binding "
        "WHERE facet_key=%s AND role='primary'", (facet_key,),
    )
    return rows[0]


async def _restaurar(facet_key, binding):
    provider_id, model_ref, model_id = binding
    await _q(
        "UPDATE facet_binding SET provider_id=%s, model_ref=%s, model_id=%s "
        "WHERE facet_key=%s AND role='primary'",
        (provider_id, model_ref, model_id, facet_key), commit=True,
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


def _put(client, facet_key, model_id):
    ids = client.portal.call(_crear_filas)
    antes = client.portal.call(_binding, facet_key)
    try:
        resp = client.put(
            f"/api/admin/facet-bindings/{facet_key}",
            json={"provider_id": "deepseek", "model_ref": ids[model_id]},
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
    resp, estado, antes, despues, ref = _aprobar(client, "jax_local", SIN_PARAM)
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
