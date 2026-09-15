"""PR-L (2026-09-14, Ruling 33) — declarar el contrato de dispatch de una fila
de `model` desde el admin, y rastro en la DB de las escrituras de binding
rechazadas por el guard de PR-J.

Hueco que cierra: el 409 `modelo_sin_contrato_de_dispatch` de PR-J dejaba como
único remedio un UPDATE a mano sobre `model` (contra la regla "cambiar el
modelo NUNCA es un UPDATE a mano") y no dejaba rastro. Ahora:

  - PUT /api/admin/models/{model_ref}/contrato-dispatch (solo superadmin)
    valida con LOS MISMOS validadores del dispatch (contrato_dispatch.py),
    escribe la fila por PK + una fila de auditoría (antes -> después) en la
    misma transacción, y estampa el sello de facet_resolver.
  - approve / PUT de binding rechazados por el guard dejan una fila
    'binding_rechazado' en model_catalog_audit, y GET /proposals la muestra.

Todo va contra jax_memory_test (conftest pisa JAX_DB_NAME) y se limpia en un
finally. Nada de pytest.raises dentro de client.portal.call.
"""
import os
import time

from auth.jwt import create_access_token

USER_ID = "1"  # jax_users.user_id real (FK de performed_by / decided_by)
TENANT_ID = "test-contrato-dispatch-admin-tenant"

SIN_CONTRATO = "test-prl-sin-contrato"
_PROVEEDOR = "deepseek"  # el de jekyll (http_openai_compat)


def _headers(role="superadmin"):
    return {"Authorization": f"Bearer {create_access_token(USER_ID, TENANT_ID, role)}"}


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


async def _crear_fila(model_id=SIN_CONTRATO, provider_id=_PROVEEDOR):
    await _q(
        "INSERT INTO model (provider_id, model_id, max_tokens_param, max_output_tokens, "
        "source, source_checked_at) VALUES (%s, %s, NULL, NULL, 'manual', NOW()) "
        "ON DUPLICATE KEY UPDATE max_tokens_param=NULL, max_output_tokens=NULL",
        (provider_id, model_id), commit=True,
    )
    rows = await _q("SELECT id FROM model WHERE provider_id=%s AND model_id=%s", (provider_id, model_id))
    return rows[0][0]


async def _borrar_fila(ref):
    # La auditoría primero: tiene FK a model y a model_binding_proposal.
    await _q("DELETE FROM model_catalog_audit WHERE model_ref=%s", (ref,), commit=True)
    await _q("DELETE FROM model_binding_proposal WHERE proposed_model_ref=%s OR current_model_ref=%s",
             (ref, ref), commit=True)
    await _q("DELETE FROM model WHERE id=%s", (ref,), commit=True)


async def _contrato(ref):
    rows = await _q("SELECT max_tokens_param, max_output_tokens FROM model WHERE id=%s", (ref,))
    return rows[0]


async def _auditoria(ref):
    rows = await _q(
        "SELECT action, facet_key, proposal_id, code, valor_antes, valor_despues, performed_by "
        "FROM model_catalog_audit WHERE model_ref=%s ORDER BY id", (ref,),
    )
    return rows


def _declarar(client, ref, body, role="superadmin"):
    return client.put(f"/api/admin/models/{ref}/contrato-dispatch", json=body, headers=_headers(role))


# ------------------------------------------------------------ autorización ---

def test_declarar_contrato_exige_superadmin(client, usuarios):
    """El rol sale de jax_users por user_id (auth/middleware.py), no del
    claim del token: hacen falta usuarios REALES con otro rol."""
    ref = client.portal.call(_crear_fila)
    try:
        body = {"max_tokens_param": "max_tokens", "max_output_tokens": 4096}
        sin_token = client.put(f"/api/admin/models/{ref}/contrato-dispatch", json=body)
        assert sin_token.status_code == 401, sin_token.text
        for rol in ("admin", "operator"):
            user_id, _ = usuarios(role=rol)
            token = create_access_token(str(user_id), TENANT_ID, rol)
            resp = client.put(f"/api/admin/models/{ref}/contrato-dispatch", json=body,
                              headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 403, (rol, resp.text)
        assert client.portal.call(_contrato, ref) == (None, None), "un no-superadmin escribió la fila"
        assert client.portal.call(_auditoria, ref) == ()
    finally:
        client.portal.call(_borrar_fila, ref)


# --------------------------------------------------------------- validación ---

def _assert_422(resp, campos):
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "contrato_dispatch_invalido"
    assert detail["campos"] == campos
    return detail


def test_valores_invalidos_son_422_con_codigo_y_no_escriben(client):
    ref = client.portal.call(_crear_fila)
    try:
        casos = [
            ({"max_tokens_param": "max_tokenz", "max_output_tokens": 4096}, ["max_tokens_param"]),
            ({"max_tokens_param": None, "max_output_tokens": 4096}, ["max_tokens_param"]),
            ({"max_tokens_param": 7, "max_output_tokens": 4096}, ["max_tokens_param"]),
            ({"max_tokens_param": "max_tokens", "max_output_tokens": 0}, ["max_output_tokens"]),
            ({"max_tokens_param": "max_tokens", "max_output_tokens": -5}, ["max_output_tokens"]),
            ({"max_tokens_param": "max_tokens", "max_output_tokens": True}, ["max_output_tokens"]),
            ({"max_tokens_param": "max_tokens", "max_output_tokens": "4096"}, ["max_output_tokens"]),
            ({"max_tokens_param": "max_tokens", "max_output_tokens": 4096.5}, ["max_output_tokens"]),
            ({"max_tokens_param": "max_tokens"}, ["max_output_tokens"]),
            ({}, ["max_tokens_param", "max_output_tokens"]),
        ]
        for body, campos in casos:
            _assert_422(_declarar(client, ref, body), campos)
        assert client.portal.call(_contrato, ref) == (None, None), "un valor inválido llegó a la fila"
        assert client.portal.call(_auditoria, ref) == (), "un rechazo de validación dejó auditoría de escritura"
    finally:
        client.portal.call(_borrar_fila, ref)


def test_el_mensaje_de_422_es_el_del_validador_del_dispatch(client):
    """Una sola regla: el texto sale de contrato_dispatch, no de una copia."""
    from contrato_dispatch import ModelDispatchConfigError, _max_output_tokens_value
    ref = client.portal.call(_crear_fila)
    try:
        detail = _assert_422(
            _declarar(client, ref, {"max_tokens_param": "max_tokens", "max_output_tokens": 0}),
            ["max_output_tokens"],
        )
        try:
            _max_output_tokens_value(SIN_CONTRATO, 0)
            esperado = None
        except ModelDispatchConfigError as e:
            esperado = str(e)
        assert detail["message"] == esperado
    finally:
        client.portal.call(_borrar_fila, ref)


def test_modelo_inexistente_es_404(client):
    resp = _declarar(client, 999999999, {"max_tokens_param": "max_tokens", "max_output_tokens": 4096})
    assert resp.status_code == 404, resp.text


# ------------------------------------------------------------------ escritura ---

def test_declarar_contrato_valido_escribe_fila_auditoria_y_sello(client):
    import json

    import facet_resolver as fr
    ref = client.portal.call(_crear_fila)
    try:
        # Una entrada cacheada de ANTES de la escritura: el sello tiene que
        # declararla obsoleta para que el dispatch vea el valor nuevo sin
        # reiniciar (este proceso y, por el mismo archivo, LAS MANOS / REPL).
        entrada = fr._CacheEntry(object(), time.monotonic(), time.time() - 5)
        assert not os.path.exists(fr.FACET_SEAL_PATH)

        resp = _declarar(client, ref, {"max_tokens_param": "max_completion_tokens", "max_output_tokens": 128000})
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert (cuerpo["max_tokens_param"], cuerpo["max_output_tokens"]) == ("max_completion_tokens", 128000)
        assert cuerpo["antes"] == {"max_tokens_param": None, "max_output_tokens": None}

        assert client.portal.call(_contrato, ref) == ("max_completion_tokens", 128000)
        auditoria = client.portal.call(_auditoria, ref)
        assert len(auditoria) == 1
        action, facet_key, proposal_id, code, antes, despues, performed_by = auditoria[0]
        assert (action, facet_key, proposal_id, code, performed_by) == (
            "contrato_declarado", None, None, None, int(USER_ID))
        assert json.loads(antes) == {"max_tokens_param": None, "max_output_tokens": None}
        assert json.loads(despues) == {"max_tokens_param": "max_completion_tokens", "max_output_tokens": 128000}

        assert os.path.exists(fr.FACET_SEAL_PATH), "no se estampó el sello de facet_resolver"
        assert fr._entrada_sellada(entrada), "una resolución cacheada antes de la escritura seguiría viva"

        # Segunda declaración: la auditoría guarda el valor anterior real.
        resp = _declarar(client, ref, {"max_tokens_param": "max_tokens", "max_output_tokens": 8192})
        assert resp.status_code == 200, resp.text
        auditoria = client.portal.call(_auditoria, ref)
        assert len(auditoria) == 2
        assert json.loads(auditoria[1][4]) == {"max_tokens_param": "max_completion_tokens", "max_output_tokens": 128000}
        assert json.loads(auditoria[1][5]) == {"max_tokens_param": "max_tokens", "max_output_tokens": 8192}
    finally:
        client.portal.call(_borrar_fila, ref)


def test_la_escritura_es_por_pk(client):
    """LAS CUATRO / indexing: el UPDATE del endpoint va por PRIMARY, una fila.
    EXPLAIN sobre la sentencia REAL (la misma constante del módulo)."""
    from api.admin.models import _SQL_DECLARAR_CONTRATO, _SQL_CONTRATO_ACTUAL
    ref = client.portal.call(_crear_fila)
    try:
        for sql, params in (
            (_SQL_DECLARAR_CONTRATO, ("max_tokens", 4096, ref)),
            (_SQL_CONTRATO_ACTUAL, (ref,)),
        ):
            filas = client.portal.call(_explain, sql, params)
            print(f"\nEXPLAIN {sql.split()[0]} por PK: {filas}")
            assert len(filas) == 1, filas
            fila = filas[0]
            assert fila["key"] == "PRIMARY", fila
            # MariaDB reporta el SELECT por PK como 'const' y el UPDATE por PK
            # como 'range' (sobre PRIMARY, rows=1): los dos son una fila por
            # índice. 'ALL' / 'index' serían un scan.
            assert fila["type"] in ("const", "range"), fila
            assert int(fila["rows"]) == 1, fila
            extra = fila.get("Extra") or ""
            assert "filesort" not in extra and "temporary" not in extra, fila
        assert client.portal.call(_contrato, ref) == (None, None), "EXPLAIN no ejecuta"

        # El rastro de la lista de propuestas: una query para toda la lista,
        # que puede usar idx_proposal_id. Con la tabla casi vacía de test el
        # optimizador puede preferir un scan; lo que se fija es que el
        # índice sea candidato y que no haya tabla temporal.
        from api.admin.models import _SQL_RECHAZOS_DE_PROPUESTAS
        sql = _SQL_RECHAZOS_DE_PROPUESTAS.format(marcas="%s, %s")
        filas = client.portal.call(_explain, sql, (1, 2))
        print(f"\nEXPLAIN rastro de propuestas: {filas}")
        assert len(filas) == 1, filas
        assert "idx_proposal_id" in (filas[0]["possible_keys"] or ""), filas
        assert "temporary" not in (filas[0].get("Extra") or ""), filas
    finally:
        client.portal.call(_borrar_fila, ref)


async def _explain(sql, params):
    import aiomysql

    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("EXPLAIN " + sql.replace(" FOR UPDATE", ""), params)
            return await cur.fetchall()


def test_la_migracion_de_la_auditoria_es_idempotente(client):
    from db.migrations import run_migrations
    client.portal.call(run_migrations)
    client.portal.call(run_migrations)
    filas = client.portal.call(
        _q, "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'model_catalog_audit'")
    assert filas[0][0] == 1


# --------------------------------------------- rastro del rechazo + flujo ---

async def _binding(facet_key):
    rows = await _q(
        "SELECT provider_id, model_ref, model_id, approved_by, approved_at FROM facet_binding "
        "WHERE facet_key=%s AND role='primary'", (facet_key,),
    )
    return rows[0]


async def _restaurar(facet_key, binding):
    provider_id, model_ref, model_id, approved_by, approved_at = binding
    await _q(
        "UPDATE facet_binding SET provider_id=%s, model_ref=%s, model_id=%s, "
        "approved_by=%s, approved_at=%s WHERE facet_key=%s AND role='primary'",
        (provider_id, model_ref, model_id, approved_by, approved_at, facet_key), commit=True,
    )


async def _propuesta(facet_key, proposed_ref):
    (current,) = (await _q(
        "SELECT model_ref FROM facet_binding WHERE facet_key=%s AND role='primary'", (facet_key,)))[0]
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO model_binding_proposal "
                "(facet_key, current_model_ref, proposed_model_ref, reason, detail) "
                "VALUES (%s, %s, %s, 'drift_detected', 'test PR-L')",
                (facet_key, current, proposed_ref),
            )
            pid = cur.lastrowid
        await conn.commit()
    return pid


def test_flujo_approve_409_declarar_contrato_approve_200(client, monkeypatch):
    import json
    import jax_engine.background as background
    encoladas = []
    monkeypatch.setattr(background, "add_safe_task", lambda bt, fn, *a: encoladas.append((fn.__name__, a)))

    ref = client.portal.call(_crear_fila)
    antes = client.portal.call(_binding, "jekyll")
    try:
        pid = client.portal.call(_propuesta, "jekyll", ref)

        # 1. approve -> 409 por contrato; nada cambia, pero queda rastro.
        resp = client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "modelo_sin_contrato_de_dispatch"
        assert resp.json()["detail"]["model_ref"] == ref, "la UI necesita la fila para ofrecer 'declarar contrato'"
        assert client.portal.call(_binding, "jekyll") == antes
        auditoria = client.portal.call(_auditoria, ref)
        assert len(auditoria) == 1, auditoria
        action, facet_key, proposal_id, code, _antes, detalle, performed_by = auditoria[0]
        assert (action, facet_key, proposal_id, code, performed_by) == (
            "binding_rechazado", "jekyll", pid, "modelo_sin_contrato_de_dispatch", int(USER_ID))
        assert json.loads(detalle)["campos"] == ["max_tokens_param", "max_output_tokens"]

        # 2. La lista de propuestas muestra el rechazo.
        lista = client.get("/api/admin/models/proposals?status=pending", headers=_headers()).json()
        propuesta = next(p for p in lista["proposals"] if p["id"] == pid)
        rechazo = propuesta["ultimo_rechazo"]
        assert rechazo["code"] == "modelo_sin_contrato_de_dispatch"
        assert rechazo["campos"] == ["max_tokens_param", "max_output_tokens"]
        assert rechazo["model_ref"] == ref
        assert rechazo["performed_by"] == int(USER_ID)
        assert rechazo["performed_at"]

        # 3. Declarar el contrato de la fila.
        resp = _declarar(client, ref, {"max_tokens_param": "max_tokens", "max_output_tokens": 393216})
        assert resp.status_code == 200, resp.text

        # 4. approve -> 200 y el binding cambia.
        resp = client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())
        assert resp.status_code == 200, resp.text
        despues = client.portal.call(_binding, "jekyll")
        assert despues[1] == ref
        assert encoladas == [("probe_after_rebind", ("jekyll",))]
        acciones = [fila[0] for fila in client.portal.call(_auditoria, ref)]
        assert acciones == ["binding_rechazado", "contrato_declarado"]
    finally:
        client.portal.call(_restaurar, "jekyll", antes)
        client.portal.call(_borrar_fila, ref)


def test_propuesta_sin_rechazos_no_trae_ultimo_rechazo(client):
    ref = client.portal.call(_crear_fila)
    try:
        pid = client.portal.call(_propuesta, "jekyll", ref)
        lista = client.get("/api/admin/models/proposals?status=pending", headers=_headers()).json()
        propuesta = next(p for p in lista["proposals"] if p["id"] == pid)
        assert propuesta["ultimo_rechazo"] is None
    finally:
        client.portal.call(_borrar_fila, ref)


def test_rechazo_por_proveedor_tambien_deja_rastro(client):
    """El 409 `modelo_de_otro_proveedor` (PR-J ronda 1) es el otro rechazo
    del guard: también queda en la DB."""
    ref = client.portal.call(_crear_fila, "test-prl-openai-completo", "openai")
    client.portal.call(
        _q, "UPDATE model SET max_tokens_param='max_completion_tokens', max_output_tokens=4096 WHERE id=%s",
        (ref,), True)
    antes = client.portal.call(_binding, "jekyll")
    try:
        pid = client.portal.call(_propuesta, "jekyll", ref)
        resp = client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())
        assert resp.status_code == 409, resp.text
        assert client.portal.call(_binding, "jekyll") == antes
        auditoria = client.portal.call(_auditoria, ref)
        assert [(a[0], a[2], a[3]) for a in auditoria] == [
            ("binding_rechazado", pid, "modelo_de_otro_proveedor")]
    finally:
        client.portal.call(_restaurar, "jekyll", antes)
        client.portal.call(_borrar_fila, ref)


def test_put_de_binding_rechazado_deja_rastro_sin_propuesta(client):
    ref = client.portal.call(_crear_fila)
    antes = client.portal.call(_binding, "jekyll")
    try:
        resp = client.put(
            "/api/admin/facet-bindings/jekyll",
            json={"provider_id": _PROVEEDOR, "model_ref": ref}, headers=_headers(),
        )
        assert resp.status_code == 409, resp.text
        assert client.portal.call(_binding, "jekyll") == antes
        auditoria = client.portal.call(_auditoria, ref)
        assert [(a[0], a[1], a[2], a[3]) for a in auditoria] == [
            ("binding_rechazado", "jekyll", None, "modelo_sin_contrato_de_dispatch")]
    finally:
        client.portal.call(_restaurar, "jekyll", antes)
        client.portal.call(_borrar_fila, ref)
