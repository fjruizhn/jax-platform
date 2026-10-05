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

import aiomysql
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
    with pytest.raises(aiomysql.DataError) as error_del_servidor:
        _put(client, ref)

    assert error_del_servidor.value.args[0] == 1265  # Data truncated: action fuera del ENUM
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
    with pytest.raises(aiomysql.DataError) as error_del_servidor:
        client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())

    assert error_del_servidor.value.args[0] == 1265  # Data truncated: action fuera del ENUM
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
    agotaron_la_espera = []

    async def guard_con_cita(*a, **k):
        if k.get("bloquear_modelo"):  # el chequeo DENTRO de la transaccion no es la cita
            return await guard_real(*a, **k)
        llegaron.append(1)
        for _ in range(500):  # hasta 5 s
            if len(llegaron) >= 2:
                break
            await asyncio.sleep(0.01)
        else:
            agotaron_la_espera.append(1)
        return await guard_real(*a, **k)

    monkeypatch.setattr(modelos, "detalle_si_rompe_el_contrato", guard_con_cita)

    def approve():
        return client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())

    with ThreadPoolExecutor(max_workers=2) as pool:
        respuestas = [f.result() for f in [pool.submit(approve), pool.submit(approve)]]

    # La carrera OCURRIO: las dos llegaron a la cita y ninguna salio por el tope.
    assert len(llegaron) == 2 and not agotaron_la_espera, (llegaron, agotaron_la_espera)
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


# ------------------------------------------- MINOR-1: deadlock por gap lock ---

_ROLES_NUEVOS = ("fallback_1", "fallback_2")


async def _crear_faceta_de_prueba(clave):
    await _q("INSERT INTO facet (`key`, display_name, transport) VALUES (%s, 'prueba binding', "
             "'http_openai_compat')", (clave,), commit=True)


async def _borrar_faceta_de_prueba(clave):
    await _q("DELETE FROM facet_binding WHERE facet_key=%s", (clave,), commit=True)
    await _q("DELETE FROM model_catalog_audit WHERE facet_key=%s", (clave,), commit=True)
    await _q("DELETE FROM facet WHERE `key`=%s", (clave,), commit=True)


def test_dos_put_a_roles_nuevos_del_mismo_hueco_no_se_trancan(client, monkeypatch):
    """Una faceta SIN bindings: fallback_1 y fallback_2 no existen, y el SELECT
    ... FOR UPDATE por uk_facet_role sobre una clave ausente toma un gap lock en
    REPEATABLE READ; los dos INSERT siguientes se esperan entre si (ERROR 1213 ->
    500). La cita retiene a cada request DESPUES de su lectura y ANTES de
    escribir, que es el momento del choque. La transaccion en READ COMMITTED no
    toma huecos. Faceta propia y nueva por corrida: una fila borrada por otro test
    deja un registro marcado en el indice hasta que InnoDB la purga, y eso cambia
    la forma del hueco (visto: el mismo test pasaba o fallaba segun el orden)."""
    import asyncio
    import uuid
    from concurrent.futures import ThreadPoolExecutor

    import api.admin.facet_bindings as fb

    _sin_sonda(monkeypatch)
    faceta = f"test-hueco-{uuid.uuid4().hex[:12]}"
    ref = client.portal.call(_crear_modelo, MODELO_OK)
    client.portal.call(_crear_faceta_de_prueba, faceta)
    real = fb.binding_de
    llegaron, agotaron = [], []

    async def binding_con_cita(cur, facet_key, role, para_actualizar=False):
        resultado = await real(cur, facet_key, role, para_actualizar)
        if para_actualizar:
            llegaron.append(role)
            for _ in range(500):
                if len(llegaron) >= 2:
                    break
                await asyncio.sleep(0.01)
            else:
                agotaron.append(role)
        return resultado

    monkeypatch.setattr(fb, "binding_de", binding_con_cita)

    def poner(rol):
        return client.put(
            f"/api/admin/facet-bindings/{faceta}",
            json={"provider_id": PROVEEDOR, "model_ref": ref, "role": rol}, headers=_headers())

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [pool.submit(poner, rol) for rol in _ROLES_NUEVOS]
            resultados = []
            for f in futuros:
                try:
                    resultados.append(f.result().status_code)
                except Exception as e:  # fail-soft: no es codigo de produccion; el error se registra como repr(e) y el assert de abajo lo hace fallar con el detalle
                    resultados.append(repr(e))
        assert len(llegaron) == 2 and not agotaron, (llegaron, agotaron)  # la carrera ocurrio
        assert resultados == [200, 200], resultados
        roles = client.portal.call(
            _q, "SELECT role FROM facet_binding WHERE facet_key=%s ORDER BY role", (faceta,))
        assert roles == (("fallback_1",), ("fallback_2",))
        acciones = [f[0] for f in client.portal.call(_filas_de_auditoria, ref)]
        assert acciones == ["binding_aplicado", "binding_aplicado"], acciones
    finally:
        client.portal.call(_borrar_faceta_de_prueba, faceta)
        client.portal.call(_limpiar, ref)


@pytest.mark.parametrize("codigo", [1213, 1205])
@pytest.mark.parametrize("escritor", ["put", "approve"])
def test_un_deadlock_o_timeout_de_lock_es_409_y_no_cambia_nada(client, modelo, monkeypatch, codigo, escritor):
    """Si aun asi el servidor devuelve 1213 (deadlock) o 1205 (lock wait
    timeout) en medio de la transaccion: rollback, 409 'reintente' y ni binding
    ni propuesta ni auditoria cambian. Cualquier otro OperationalError sigue
    subiendo (no es un conflicto de concurrencia)."""
    import api.admin.facet_bindings as fb
    import api.admin.models as modelos

    _sin_sonda(monkeypatch)
    ref, antes = modelo

    async def choque(cur, *_a, **_k):
        raise aiomysql.OperationalError(codigo, "Deadlock found when trying to get lock")

    monkeypatch.setattr(fb if escritor == "put" else modelos, "registrar_binding_aplicado", choque)
    pid = None
    if escritor == "put":
        resp = _put(client, ref)
    else:
        pid = client.portal.call(_propuesta, ref)
        resp = client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())

    assert resp.status_code == 409, resp.text
    detalle = resp.json()["detail"]
    assert detalle["code"] == "binding_conflicto_concurrente"
    assert "reintente" in detalle["message"].lower()
    assert client.portal.call(_binding) == antes
    assert client.portal.call(_filas_de_auditoria, ref) == ()
    if pid is not None:
        assert client.portal.call(_estado_propuesta, pid) == "pending"


def test_otro_operational_error_no_se_disfraza_de_conflicto(client, modelo, monkeypatch):
    import api.admin.facet_bindings as fb
    _sin_sonda(monkeypatch)
    ref, antes = modelo

    async def caido(cur, *_a, **_k):
        raise aiomysql.OperationalError(2013, "Lost connection to MySQL server")

    monkeypatch.setattr(fb, "registrar_binding_aplicado", caido)
    with pytest.raises(aiomysql.OperationalError):
        _put(client, ref)
    assert client.portal.call(_binding) == antes


# ------------------------- MINOR-2: el guard se repite DENTRO de la transaccion ---

def _cambiar_contrato_entre_guard_y_escritura(monkeypatch, modulo, ref):
    """Envuelve el guard del modulo: tras la PRIMERA llamada (la de antes del
    BEGIN, que da el visto bueno) otro actor quita el contrato del modelo. La
    SEGUNDA (dentro de la transaccion) tiene que verlo."""
    real = modulo.detalle_si_rompe_el_contrato
    llamadas = []

    async def guard(cur, *a, **k):
        resultado = await real(cur, *a, **k)
        llamadas.append(k.get("bloquear_modelo", False))
        if len(llamadas) == 1:
            assert resultado is None
            await _q("UPDATE model SET max_tokens_param=NULL, max_output_tokens=NULL WHERE id=%s",
                     (ref,), commit=True)
        return resultado

    monkeypatch.setattr(modulo, "detalle_si_rompe_el_contrato", guard)
    return llamadas


def test_put_con_el_contrato_quitado_entre_el_guard_y_la_escritura_es_409(client, modelo, monkeypatch):
    import api.admin.facet_bindings as fb
    _sin_sonda(monkeypatch)
    ref, antes = modelo
    llamadas = _cambiar_contrato_entre_guard_y_escritura(monkeypatch, fb, ref)

    resp = _put(client, ref)

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "modelo_sin_contrato_de_dispatch"
    assert llamadas == [False, True]  # antes del BEGIN, y dentro con el modelo bloqueado
    assert client.portal.call(_binding) == antes
    acciones = [f[0] for f in client.portal.call(_filas_de_auditoria, ref)]
    assert acciones == ["binding_rechazado"], acciones


def test_approve_con_el_contrato_quitado_entre_el_guard_y_la_escritura_es_409(client, modelo, monkeypatch):
    import api.admin.models as modelos
    _sin_sonda(monkeypatch)
    ref, antes = modelo
    pid = client.portal.call(_propuesta, ref)
    llamadas = _cambiar_contrato_entre_guard_y_escritura(monkeypatch, modelos, ref)

    resp = client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "modelo_sin_contrato_de_dispatch"
    assert llamadas == [False, True]
    assert client.portal.call(_binding) == antes
    assert client.portal.call(_estado_propuesta, pid) == "pending"
    filas = client.portal.call(_filas_de_auditoria, ref)
    assert [(f[0], f[5]) for f in filas] == [("binding_rechazado", pid)], filas


# ------------------------------ MINOR-5: approve sin binding 'primary' previo ---

def test_approve_sin_binding_primary_es_409_y_deja_la_propuesta_pendiente(client, modelo, monkeypatch):
    """Antes marcaba la propuesta 'approved' (200) sin aplicar nada ni auditar.
    409 y no 422: el pedido es valido, es el ESTADO el que no lo admite (como el
    409 de una propuesta que ya no esta pendiente)."""
    _sin_sonda(monkeypatch)
    ref, antes = modelo
    pid = client.portal.call(_propuesta, ref)
    fila = client.portal.call(_q, "SELECT facet_key, provider_id, model_id, model_ref, role, approved_by, "
                                  "approved_at FROM facet_binding WHERE facet_key=%s AND role='primary'",
                              (FACETA,))[0]
    client.portal.call(_q, "DELETE FROM facet_binding WHERE facet_key=%s AND role='primary'", (FACETA,), True)
    try:
        resp = client.post(f"/api/admin/models/proposals/{pid}/approve", headers=_headers())
        assert resp.status_code == 409, resp.text
        # Código estable para i18n (PR #192): nunca un texto del backend en la UI.
        assert resp.json()["detail"] == {"code": "faceta_sin_binding_primary", "facet_key": FACETA}, resp.text
        assert client.portal.call(_estado_propuesta, pid) == "pending"
        assert client.portal.call(_filas_de_auditoria, ref) == ()
    finally:
        client.portal.call(
            _q, "INSERT INTO facet_binding (facet_key, provider_id, model_id, model_ref, role, approved_by, "
                "approved_at) VALUES (%s, %s, %s, %s, %s, %s, %s)", fila, True)


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


# ---------------------------- MINOR-6: el ALTER del ENUM no espera un dia ---

_TABLA_MDL = "model_catalog_audit_prueba_mdl"


def test_el_alter_de_un_enum_con_la_tabla_tomada_falla_claro_y_no_cuelga(client, monkeypatch):
    """Una transaccion abierta sobre la tabla tiene su metadata lock: el ALTER
    esperaria lock_wait_timeout (86400 s por defecto) y el arranque colgaria un
    dia. Con la espera acotada, vence y dice cual tabla/columna fue; liberada
    la tabla, el mismo ALTER corre."""
    from db import migrations as m

    ddl = (f"ALTER TABLE {_TABLA_MDL} MODIFY COLUMN action "
           "ENUM('contrato_declarado','binding_rechazado','binding_aplicado') NOT NULL")

    async def escenario():
        from db.connection import get_pool
        pool = await get_pool()
        monkeypatch.setattr(m, "_LOCK_WAIT_DDL_SEGUNDOS", 1)
        await _q(f"DROP TABLE IF EXISTS {_TABLA_MDL}", commit=True)
        await _q(f"CREATE TABLE {_TABLA_MDL} (id INT AUTO_INCREMENT PRIMARY KEY, "
                 "action ENUM('contrato_declarado','binding_rechazado') NOT NULL)", commit=True)
        try:
            async with pool.acquire() as retiene, pool.acquire() as migra:
                async with retiene.cursor() as c1, migra.cursor() as c2:
                    await retiene.begin()
                    await c1.execute(f"SELECT * FROM {_TABLA_MDL}")  # toma el MDL compartido
                    await c2.execute("SELECT @@SESSION.lock_wait_timeout")
                    (previo,) = await c2.fetchone()
                    error = None
                    try:
                        await m._aplicar_extension_de_enum(
                            c2, _TABLA_MDL, "action", "binding_aplicado", ddl)
                    except RuntimeError as e:
                        error = e
                    await c2.execute("SELECT @@SESSION.lock_wait_timeout")
                    (despues,) = await c2.fetchone()
                    await retiene.rollback()
                    await m._aplicar_extension_de_enum(c2, _TABLA_MDL, "action", "binding_aplicado", ddl)
                    return error, previo, despues, await _enum_de(_TABLA_MDL)
        finally:
            await _q(f"DROP TABLE IF EXISTS {_TABLA_MDL}", commit=True)

    error, previo, despues, tipo = client.portal.call(escenario)
    assert error is not None, "el ALTER no fallo con la tabla tomada"
    assert _TABLA_MDL in str(error) and "action" in str(error)
    assert despues == previo, "la espera acotada no se restauro en la sesion"
    assert "binding_aplicado" in tipo


def test_run_migrations_aplica_las_extensiones_de_enum_por_el_camino_acotado():
    import inspect

    from db import migrations as m
    assert "_aplicar_extension_de_enum" in inspect.getsource(m.run_migrations)


def test_put_que_pisa_un_binding_creado_en_la_carrera_es_409_y_no_audita_antes_null(client, modelo, monkeypatch):
    """MINOR-7 de la segunda auditoría del PR 192: con READ COMMITTED, el FOR UPDATE
    sobre una (faceta, rol) sin fila no toma candado; si otro PUT la crea y confirma
    entre la lectura y el INSERT, el INSERT ... ODKU de este pedido la PISA
    (rowcount 2) y la auditoría diría valor_antes=NULL, que es falso. Se reproduce
    haciendo que la lectura bloqueada no vea la fila que sí existe."""
    _sin_sonda(monkeypatch)
    ref, antes = modelo
    import api.admin.facet_bindings as fb
    original = fb.binding_de

    async def lectura_que_llega_tarde(cur, facet_key, role, para_actualizar=False):
        if para_actualizar:
            return None  # lo que ve la transacción si la fila se confirmó después de leer
        return await original(cur, facet_key, role, para_actualizar=para_actualizar)

    monkeypatch.setattr(fb, "binding_de", lectura_que_llega_tarde)
    resp = _put(client, ref)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "binding_conflicto_concurrente", resp.text
    assert client.portal.call(_binding) == antes  # el binding no cambió
    assert not client.portal.call(_filas_de_auditoria, ref)  # y no hay auditoría falsa
