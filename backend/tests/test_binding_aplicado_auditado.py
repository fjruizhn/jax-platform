"""Los dos escritores de facet_binding dejan rastro del cambio APLICADO.

Hueco que cierra (2026-10-04): `facet_binding` tiene dos escritores legítimos,
`PUT /api/admin/facet-bindings/{facet_key}` y `POST
/api/admin/models/proposals/{id}/approve`. Ninguno dejaba fila en
`model_catalog_audit` cuando el cambio SE APLICABA (solo los 409 quedaban,
como 'binding_rechazado'). Caso real: jax_local y el_juez pasaron a
qwen3.8-mesa-131k el 2026-09-23 por el PUT y no quedó más rastro que
approved_by/approved_at, que se pisan en el siguiente cambio.

Ahora cada cambio aplicado deja UNA fila 'binding_aplicado' en la MISMA
transacción que el INSERT/UPDATE de facet_binding (fallo cerrado: si la
auditoría falla, el binding no cambia).

Todo va contra la base de test de la sesión (conftest) y se limpia en un
finally. Nada de pytest.raises dentro de client.portal.call.
"""
import json

import pytest

from auth.jwt import create_access_token

USER_ID = "1"  # jax_users.user_id real (FK de approved_by / decided_by)
TENANT_ID = "1"
FACETA = "jekyll"  # deepseek, http_openai_compat: el guard exige el contrato completo
PROVEEDOR = "deepseek"
MODELO_OK = "test-binding-aplicado-completo"
MODELO_SIN_CONTRATO = "test-binding-aplicado-sin-contrato"


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


async def _crear_modelo(model_id, completo=True):
    param, tope = ("max_tokens", 4096) if completo else (None, None)
    await _q(
        "INSERT INTO model (provider_id, model_id, max_tokens_param, max_output_tokens, "
        "source, source_checked_at) VALUES (%s, %s, %s, %s, 'manual', NOW()) "
        "ON DUPLICATE KEY UPDATE max_tokens_param=VALUES(max_tokens_param), "
        "max_output_tokens=VALUES(max_output_tokens)",
        (PROVEEDOR, model_id, param, tope), commit=True,
    )
    return (await _q("SELECT id FROM model WHERE provider_id=%s AND model_id=%s",
                     (PROVEEDOR, model_id)))[0][0]


async def _binding():
    return (await _q(
        "SELECT provider_id, model_ref, model_id, approved_by, approved_at FROM facet_binding "
        "WHERE facet_key=%s AND role='primary'", (FACETA,)))[0]


async def _legible(ref):
    """provider_id/model_id de la fila de `model` a la que apunta un model_ref:
    lo que la auditoria guarda como identificadores legibles."""
    return (await _q("SELECT provider_id, model_id FROM model WHERE id=%s", (ref,)))[0]


async def _restaurar(antes):
    provider_id, model_ref, model_id, approved_by, approved_at = antes
    await _q(
        "UPDATE facet_binding SET provider_id=%s, model_ref=%s, model_id=%s, "
        "approved_by=%s, approved_at=%s WHERE facet_key=%s AND role='primary'",
        (provider_id, model_ref, model_id, approved_by, approved_at, FACETA), commit=True,
    )


async def _propuesta(proposed_ref):
    (current,) = (await _q(
        "SELECT model_ref FROM facet_binding WHERE facet_key=%s AND role='primary'", (FACETA,)))[0]
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO model_binding_proposal "
                "(facet_key, current_model_ref, proposed_model_ref, reason, detail) "
                "VALUES (%s, %s, %s, 'drift_detected', 'test binding_aplicado')",
                (FACETA, current, proposed_ref),
            )
            pid = cur.lastrowid
        await conn.commit()
    return pid


async def _estado_propuesta(pid):
    return (await _q("SELECT status FROM model_binding_proposal WHERE id=%s", (pid,)))[0][0]


async def _filas_de_auditoria(ref):
    return await _q(
        "SELECT action, model_ref, provider_id, model_id, facet_key, proposal_id, code, "
        "valor_antes, valor_despues, performed_by, performed_by_email, performed_from_ip "
        "FROM model_catalog_audit WHERE model_ref=%s ORDER BY id", (ref,))


async def _limpiar(ref):
    await _q("DELETE FROM model_catalog_audit WHERE model_ref=%s", (ref,), commit=True)
    await _q("DELETE FROM model_binding_proposal WHERE proposed_model_ref=%s OR current_model_ref=%s",
             (ref, ref), commit=True)
    await _q("DELETE FROM model WHERE id=%s", (ref,), commit=True)


@pytest.fixture
def modelo(client):
    """Una fila de `model` con el contrato completo, el binding original de la
    faceta guardado y restaurado, y la auditoría de la fila borrada al final."""
    ref = client.portal.call(_crear_modelo, MODELO_OK)
    antes = client.portal.call(_binding)
    yield ref, antes
    client.portal.call(_restaurar, antes)
    client.portal.call(_limpiar, ref)


def _sin_sonda(monkeypatch):
    """La sonda posterior al commit llamaría a un proveedor: se encola a nada."""
    import jax_engine.background as background
    monkeypatch.setattr(background, "add_safe_task", lambda *a, **k: None)


def _put(client, ref):
    return client.put(
        f"/api/admin/facet-bindings/{FACETA}",
        json={"provider_id": PROVEEDOR, "model_ref": ref}, headers=_headers())


# ------------------------------------------------------------------- PUT ---

def test_put_exitoso_escribe_exactamente_una_fila_binding_aplicado(client, modelo, monkeypatch):
    _sin_sonda(monkeypatch)
    ref, antes = modelo
    _p, ref_antes, _m, by_antes, at_antes = antes
    p_antes, mid_antes = client.portal.call(_legible, ref_antes)

    resp = _put(client, ref)
    assert resp.status_code == 200, resp.text

    filas = client.portal.call(_filas_de_auditoria, ref)
    assert len(filas) == 1, filas
    (action, model_ref, provider_id, model_id, facet_key, proposal_id, code,
     valor_antes, valor_despues, performed_by, email, ip) = filas[0]
    assert action == "binding_aplicado"
    assert model_ref == ref
    assert (provider_id, model_id) == (PROVEEDOR, MODELO_OK)  # legibles del modelo NUEVO
    assert facet_key == FACETA
    assert proposal_id is None  # el PUT no tiene propuesta
    assert performed_by == int(USER_ID)
    assert email  # snapshot del email de quien actuó
    assert ip
    assert json.loads(valor_antes) == {
        "model_ref": ref_antes, "provider_id": p_antes, "model_id": mid_antes,
        "approved_by": by_antes, "approved_at": str(at_antes) if at_antes else None,
    }
    assert json.loads(valor_despues) == {
        "model_ref": ref, "provider_id": PROVEEDOR, "model_id": MODELO_OK, "role": "primary"}


def test_put_de_binding_inexistente_deja_valor_antes_null(client, monkeypatch):
    """Una faceta/rol sin fila previa: valor_antes es NULL, no un objeto vacío."""
    _sin_sonda(monkeypatch)
    ref = client.portal.call(_crear_modelo, MODELO_OK)
    try:
        client.portal.call(_q, "DELETE FROM facet_binding WHERE facet_key=%s AND role='fallback_2'",
                           (FACETA,), True)
        resp = client.put(
            f"/api/admin/facet-bindings/{FACETA}",
            json={"provider_id": PROVEEDOR, "model_ref": ref, "role": "fallback_2"},
            headers=_headers())
        assert resp.status_code == 200, resp.text
        filas = client.portal.call(_filas_de_auditoria, ref)
        assert len(filas) == 1 and filas[0][0] == "binding_aplicado", filas
        assert filas[0][7] is None  # valor_antes
        assert json.loads(filas[0][8])["role"] == "fallback_2"
    finally:
        client.portal.call(_q, "DELETE FROM facet_binding WHERE facet_key=%s AND role='fallback_2'",
                           (FACETA,), True)
        client.portal.call(_limpiar, ref)


def test_put_rechazado_por_el_guard_no_escribe_binding_aplicado(client, monkeypatch):
    _sin_sonda(monkeypatch)
    ref = client.portal.call(_crear_modelo, MODELO_SIN_CONTRATO, False)
    antes = client.portal.call(_binding)
    try:
        resp = _put(client, ref)
        assert resp.status_code == 409, resp.text
        assert client.portal.call(_binding) == antes
        acciones = [f[0] for f in client.portal.call(_filas_de_auditoria, ref)]
        assert acciones == ["binding_rechazado"], acciones
    finally:
        client.portal.call(_restaurar, antes)
        client.portal.call(_limpiar, ref)


def test_put_con_auditoria_rota_no_cambia_el_binding(client, modelo, monkeypatch):
    """Fallo cerrado: el INSERT de auditoría revienta DESPUÉS de escribir
    facet_binding en la misma transacción; el binding queda como estaba."""
    import api.admin.facet_bindings as fb
    _sin_sonda(monkeypatch)
    ref, antes = modelo

    async def _auditoria_rota(cur, *_a, **_k):
        # Un INSERT real contra el servidor que el servidor rechaza (action fuera del ENUM).
        await cur.execute(
            "INSERT INTO model_catalog_audit (action, model_ref, performed_by, performed_from_ip) "
            "VALUES ('no_es_una_accion', 1, 1, 'x')")

    monkeypatch.setattr(fb, "registrar_binding_aplicado", _auditoria_rota)
    with pytest.raises(Exception):
        _put(client, ref)

    assert client.portal.call(_binding) == antes, "el binding cambió aunque la auditoría falló"
    assert client.portal.call(_filas_de_auditoria, ref) == ()


# ---------------------------------------------------------------- approve ---

def test_approve_exitoso_escribe_binding_aplicado_con_proposal_id(client, modelo, monkeypatch):
    _sin_sonda(monkeypatch)
    ref, antes = modelo
    _p, ref_antes, _m, by_antes, at_antes = antes
    p_antes, mid_antes = client.portal.call(_legible, ref_antes)
    pid = client.portal.call(_propuesta, ref)

    resp = client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())
    assert resp.status_code == 200, resp.text

    filas = client.portal.call(_filas_de_auditoria, ref)
    assert len(filas) == 1, filas
    (action, model_ref, provider_id, model_id, facet_key, proposal_id, _code,
     valor_antes, valor_despues, performed_by, email, ip) = filas[0]
    assert action == "binding_aplicado"
    assert (model_ref, provider_id, model_id, facet_key) == (ref, PROVEEDOR, MODELO_OK, FACETA)
    assert proposal_id == pid
    assert performed_by == int(USER_ID) and email and ip
    assert json.loads(valor_antes) == {
        "model_ref": ref_antes, "provider_id": p_antes, "model_id": mid_antes,
        "approved_by": by_antes, "approved_at": str(at_antes) if at_antes else None,
    }
    assert json.loads(valor_despues) == {
        "model_ref": ref, "provider_id": PROVEEDOR, "model_id": MODELO_OK, "role": "primary"}


def test_approve_rechazado_por_el_guard_no_escribe_binding_aplicado(client, monkeypatch):
    _sin_sonda(monkeypatch)
    ref = client.portal.call(_crear_modelo, MODELO_SIN_CONTRATO, False)
    antes = client.portal.call(_binding)
    try:
        pid = client.portal.call(_propuesta, ref)
        resp = client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())
        assert resp.status_code == 409, resp.text
        assert client.portal.call(_binding) == antes
        acciones = [f[0] for f in client.portal.call(_filas_de_auditoria, ref)]
        assert acciones == ["binding_rechazado"], acciones
    finally:
        client.portal.call(_restaurar, antes)
        client.portal.call(_limpiar, ref)


def test_approve_con_auditoria_rota_no_cambia_el_binding_ni_la_propuesta(client, modelo, monkeypatch):
    import api.admin.models as modelos
    _sin_sonda(monkeypatch)
    ref, antes = modelo
    pid = client.portal.call(_propuesta, ref)

    async def _auditoria_rota(cur, *_a, **_k):
        await cur.execute(
            "INSERT INTO model_catalog_audit (action, model_ref, performed_by, performed_from_ip) "
            "VALUES ('no_es_una_accion', 1, 1, 'x')")

    monkeypatch.setattr(modelos, "registrar_binding_aplicado", _auditoria_rota)
    with pytest.raises(Exception):
        client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())

    assert client.portal.call(_binding) == antes, "el binding cambió aunque la auditoría falló"
    assert client.portal.call(_estado_propuesta, pid) == "pending"
    assert client.portal.call(_filas_de_auditoria, ref) == ()


# ------------------------------------------ las lecturas existentes no se
# ------------------------------------------ confunden con la fila nueva ---

def test_una_fila_binding_aplicado_no_se_lee_como_rechazo(client, modelo, monkeypatch):
    """GET /facet-bindings (ultimo_rechazo) y GET /proposals (ultimo_rechazo)
    filtran por action='binding_rechazado': tras un cambio aplicado ninguna
    muestra un rechazo."""
    _sin_sonda(monkeypatch)
    ref, _antes = modelo
    pid = client.portal.call(_propuesta, ref)
    assert client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers()).status_code == 200

    filas = client.portal.call(_filas_de_auditoria, ref)
    assert [f[0] for f in filas] == ["binding_aplicado"]

    lista = client.get("/api/admin/facet-bindings", headers=_headers())
    assert lista.status_code == 200, lista.text
    jekyll = next(b for b in lista.json()["bindings"] if b["facet_key"] == FACETA)
    assert jekyll["ultimo_rechazo"] is None

    props = client.get("/api/admin/models/proposals", headers=_headers())
    assert props.status_code == 200, props.text
    mia = next(p for p in props.json()["proposals"] if p["id"] == pid)
    assert mia["ultimo_rechazo"] is None


# ------------------------------------------------------------- concurrencia ---

def test_dos_approves_concurrentes_de_la_misma_propuesta_aplican_uno_solo(client, modelo, monkeypatch):
    """La carrera de approve_proposal: el 'pending' se leia ANTES de abrir la
    transaccion, asi que dos approves simultaneos pasaban los dos y escribian
    dos binding_aplicado. Para que el choque no dependa de la suerte, el guard
    (que corre despues de leer 'pending' y antes de escribir) retiene a cada
    request hasta que las DOS llegaron: es la ventana de la carrera, forzada.
    Dos requests reales en paralelo -> dos conexiones reales del pool."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    import api.admin.models as modelos

    _sin_sonda(monkeypatch)
    ref, _antes = modelo
    pid = client.portal.call(_propuesta, ref)
    guard_real = modelos.detalle_si_rompe_el_contrato
    llegaron = []

    async def guard_con_cita(*a, **k):
        llegaron.append(1)
        for _ in range(500):  # hasta 5 s: si la otra no llega, sigue (no cuelga el test)
            if len(llegaron) >= 2:
                break
            await asyncio.sleep(0.01)
        return await guard_real(*a, **k)

    monkeypatch.setattr(modelos, "detalle_si_rompe_el_contrato", guard_con_cita)

    def approve():
        return client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())

    with ThreadPoolExecutor(max_workers=2) as pool:
        respuestas = [f.result() for f in [pool.submit(approve), pool.submit(approve)]]

    codigos = sorted(r.status_code for r in respuestas)
    assert codigos == [200, 409], [(r.status_code, r.text) for r in respuestas]
    perdedora = next(r for r in respuestas if r.status_code == 409)
    assert "approved" in perdedora.json()["detail"]
    acciones = [f[0] for f in client.portal.call(_filas_de_auditoria, ref)]
    assert acciones == ["binding_aplicado"], acciones


def test_la_relectura_de_la_propuesta_va_por_primary(client):
    """Las Cuatro del Rendimiento, 1: el SELECT ... FOR UPDATE de la propuesta
    va por la PK, una fila."""
    import api.admin.models as modelos

    plan = client.portal.call(_q, "EXPLAIN " + modelos._SQL_PROPUESTA_PARA_ACTUALIZAR, (1,))
    assert plan[0][5] == "PRIMARY", plan
    assert plan[0][3] in ("const", "ref"), plan


# ----------------------------------------------------------------- índice ---

def test_la_lectura_del_binding_anterior_va_por_el_indice_unico(client):
    """Las Cuatro del Rendimiento, 1: el SELECT ... FOR UPDATE de los dos
    escritores va por uk_facet_role (facet_key, role), no por un recorrido."""
    import contrato_dispatch

    sql = contrato_dispatch.SQL_BINDING_PARA_ACTUALIZAR
    plan = client.portal.call(_q, "EXPLAIN " + sql, (FACETA, "primary"))
    # EXPLAIN: id, select_type, table, type, possible_keys, key, ...
    assert plan[0][5] == "uk_facet_role", plan
    assert plan[0][3] in ("const", "ref"), plan


# -------------------------------------------------------------- migración ---

_TABLA_ENUM_VIEJO = "model_catalog_audit_prueba_enum"


async def _enum_de(tabla):
    return (await _q(
        "SELECT COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME=%s AND COLUMN_NAME='action'", (tabla,)))[0][0]


async def _aplicar_extension_enum(tabla):
    """Corre, sobre `tabla`, la entrada de _ENUM_EXTENSIONS de binding_aplicado
    con el mismo criterio que run_migrations (mira antes de actuar)."""
    from db import migrations as m
    from db.connection import get_pool

    entradas = [e for e in m._ENUM_EXTENSIONS
                if e[0] == "model_catalog_audit" and e[1] == "action" and e[2] == "binding_aplicado"]
    assert len(entradas) == 1, "falta (o está duplicada) la extensión del ENUM de binding_aplicado"
    ddl = entradas[0][3].replace("ALTER TABLE model_catalog_audit", f"ALTER TABLE {tabla}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            if not await m._enum_has_value(cur, tabla, "action", "binding_aplicado"):
                await cur.execute(ddl)
        await conn.commit()


def test_la_migracion_del_enum_convierte_la_tabla_vieja_y_es_idempotente(client):
    try:
        client.portal.call(_q, f"DROP TABLE IF EXISTS {_TABLA_ENUM_VIEJO}", (), True)
        client.portal.call(
            _q, f"CREATE TABLE {_TABLA_ENUM_VIEJO} ("
                "id INT AUTO_INCREMENT PRIMARY KEY, "
                "action ENUM('contrato_declarado','binding_rechazado') NOT NULL)", (), True)
        assert "binding_aplicado" not in client.portal.call(_enum_de, _TABLA_ENUM_VIEJO)

        client.portal.call(_aplicar_extension_enum, _TABLA_ENUM_VIEJO)
        client.portal.call(_aplicar_extension_enum, _TABLA_ENUM_VIEJO)  # idempotente

        tipo = client.portal.call(_enum_de, _TABLA_ENUM_VIEJO)
        assert tipo == "enum('contrato_declarado','binding_rechazado','binding_aplicado')", tipo
    finally:
        client.portal.call(_q, f"DROP TABLE IF EXISTS {_TABLA_ENUM_VIEJO}", (), True)


def test_run_migrations_dos_veces_deja_el_enum_completo(client):
    from db.migrations import run_migrations
    client.portal.call(run_migrations)
    client.portal.call(run_migrations)
    tipo = client.portal.call(_enum_de, "model_catalog_audit")
    assert tipo == "enum('contrato_declarado','binding_rechazado','binding_aplicado')", tipo
