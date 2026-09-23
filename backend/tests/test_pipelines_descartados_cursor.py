"""Paginación por cursor de los dos listados de descartados + FORCE INDEX de
la vista del dueño (2026-09-23; ver api/paginacion_descartados.py y
docs/carga-descartados-cursor-2026-09-23.md).

Qué se exige, y por qué cada cosa:

- `SQL_DESCARTADOS_DEL_USUARIO` (y su variante por cursor) lleva
  `FORCE INDEX (idx_pipelines_descartados)`. Medido con descartados de 500
  usuarios INTERCALADOS en el tiempo: con el índice global
  `idx_pipelines_ocultos` el usuario con 3 descartados lee 55.004 filas
  (p95 55-63 ms) en vez de 4 (0,12-0,18 ms). El plan elegido sin hint
  alternaba entre los dos (jax-platform#157). El control es estructural (el
  SQL) Y de comportamiento (sin el índice la consulta revienta con 1176: un
  hint ignorable no lo haría).
- El cursor devuelve EXACTAMENTE lo mismo que el recorrido por offset, con
  empates en `descartado_at`, con `descartado_at` NULL y en la última
  página (has_more=False, cursor_siguiente=None).
- La página profunda por cursor lee ~limite+1 filas (Handler_read real),
  sin filesort, en las dos vistas; el mismo punto por offset lee todo lo
  anterior (el control demuestra que la medida discrimina).
- Un cursor ilegible, o cursor + offset, es un 422 -- nunca la primera
  página en silencio.
"""
import base64
import json
import time
import uuid

import aiomysql
import pytest

from api import paginacion_descartados as pag
from api import pipelines as mod
from tests.identidades import cabeceras, sql, uid
from tests.test_pipelines_descarte import _explain_y_handler_read, _insertar_pipelines_bulk

TENANT = "descarte-cursor-t1"


# ---------------------------------------------------------------------------
# Puros (corren también sin DB)
# ---------------------------------------------------------------------------
def test_la_consulta_del_usuario_fuerza_idx_pipelines_descartados():
    assert "FORCE INDEX (idx_pipelines_descartados)" in mod.SQL_DESCARTADOS_DEL_USUARIO
    for cursor in (None, pag.codificar_cursor(1758650000.5, "b" * 36), pag.codificar_cursor(None, "a")):
        consulta, _ = pag.consulta_y_parametros(mod.SQL_DESCARTADOS_DEL_USUARIO_BASE, ("u", "t"), 50, 0, cursor)
        assert "FORCE INDEX (idx_pipelines_descartados)" in consulta, consulta


def test_orden_total_con_desempate_por_pipeline_id_en_las_dos_vistas():
    from api.admin.pipelines_ocultos import SQL_DESCARTADOS_ADMIN
    for consulta in (mod.SQL_DESCARTADOS_DEL_USUARIO, SQL_DESCARTADOS_ADMIN):
        assert "ORDER BY descartado_at DESC, pipeline_id DESC" in consulta, consulta


@pytest.mark.parametrize("d", [1758650000.123456789, 0.1 + 0.2, 1e-300, 1758650000.0, None])
def test_el_cursor_vuelve_exacto(d):
    """repr del float es de ida y vuelta exacta: el `descartado_at = %s` del
    desempate compara el mismo DOUBLE, no uno redondeado."""
    pid = str(uuid.uuid4())
    assert pag.decodificar_cursor(pag.codificar_cursor(d, pid)) == (d, pid)


def _b64(texto: str) -> str:
    return base64.urlsafe_b64encode(texto.encode()).decode().rstrip("=")


@pytest.mark.parametrize("cursor", [
    "no-es-base64!!",
    _b64("no es json"),
    _b64("[1.0]"),
    _b64('{"d": 1.0, "p": "x"}'),
    _b64('[NaN, "x"]'),
    _b64('[Infinity, "x"]'),
    _b64('[true, "x"]'),
    _b64('["1.0", "x"]'),
    _b64("[1.0, 5]"),
    _b64('[1.0, ""]'),
    _b64(json.dumps([1.0, "x" * 37])),
])
def test_un_cursor_ilegible_es_422_cursor_invalido(cursor):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        pag.decodificar_cursor(cursor)
    assert exc.value.status_code == 422
    assert exc.value.detail == "cursor_invalido"


def test_cursor_siguiente_solo_si_hay_mas():
    filas = [("p1", "n", "discarded", 1.0, 2.0, 3.5)]
    assert pag.cursor_siguiente(filas, False, idx_fecha=5) is None
    assert pag.cursor_siguiente([], True, idx_fecha=5) is None
    assert pag.decodificar_cursor(pag.cursor_siguiente(filas, True, idx_fecha=5)) == (3.5, "p1")


# ---------------------------------------------------------------------------
# Con DB
# ---------------------------------------------------------------------------
def _filas_descartadas(user_id, tenant_id, fechas):
    """Una fila `discarded` (con ack del dueño) por fecha; None = sin fecha."""
    filas = []
    for d in fechas:
        creado = (d or time.time()) - 60
        filas.append((str(uuid.uuid4()), "discarded", creado, creado + 1, user_id, tenant_id, creado,
                      "aborted", user_id, d))
    return filas


def _esperado(filas):
    """El orden que la vista promete: descartado_at DESC con los NULL al
    final (así ordena MariaDB en DESC), desempate pipeline_id DESC."""
    return [f[0] for f in sorted(filas, key=lambda f: (f[9] is not None, f[9] or 0.0, f[0]), reverse=True)]


def _recorrer(client, url, headers, limite, por, fijos=None):
    """Recorre TODA la vista, por `offset` o por `cursor`, como lo haría el
    "ver más" de la interfaz. Devuelve los ids en orden y cuántas páginas."""
    ids, paginas, cursor = [], 0, None
    while True:
        params = {**(fijos or {}), "limite": limite}
        if por == "offset":
            params["offset"] = len(ids)
        elif cursor is not None:
            params["cursor"] = cursor
        resp = client.get(url, params=params, headers=headers)
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        paginas += 1
        ids += [p["pipeline_id"] for p in cuerpo["pipelines"]]
        assert "cursor_siguiente" in cuerpo, cuerpo
        if not cuerpo["has_more"]:
            assert cuerpo["cursor_siguiente"] is None, cuerpo
            return ids, paginas
        assert cuerpo["cursor_siguiente"], cuerpo
        cursor = cuerpo["cursor_siguiente"]
        assert paginas < 1000, "el recorrido no termina"


def test_el_cursor_del_usuario_da_las_mismas_paginas_que_offset_con_empates_y_null(client):
    duenio = uid(client, "descarte-cursor-duenio", "operator")
    headers = cabeceras(client, "descarte-cursor-duenio", "operator", tenant_id=TENANT)
    base = time.time() - 10_000
    # 30 fechas, cada una repetida 3 veces (empates que caen en el borde de
    # página con limite=4), + 3 sin fecha (NULL) para que un cursor caiga
    # sobre una fila NULL y se use la rama "sin fecha" del predicado.
    fechas = [base + (i // 3) for i in range(90)] + [None, None, None]
    filas = _filas_descartadas(duenio, TENANT, fechas)
    # ruido: otro dueño, mismas fechas, no puede aparecer
    ruido = _filas_descartadas("descarte-cursor-otro", TENANT, fechas[:20])
    try:
        client.portal.call(_insertar_pipelines_bulk, filas + ruido)
        esperado = _esperado(filas)
        for limite in (4, 7, 50):
            fijos = {"estado": "discarded"}
            por_offset, _ = _recorrer(client, "/api/pipelines", headers, limite, "offset", fijos)
            por_cursor, paginas = _recorrer(client, "/api/pipelines", headers, limite, "cursor", fijos)
            assert por_offset == esperado, limite
            assert por_cursor == esperado, limite
            assert paginas == -(-len(esperado) // limite), (limite, paginas)
        # los tres NULL al final, en orden de pipeline_id DESC
        assert esperado[-3:] == sorted([f[0] for f in filas if f[9] is None], reverse=True)
    finally:
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE tenant_id=%s", (TENANT,))


def test_el_cursor_del_admin_da_las_mismas_paginas_que_offset(client, client_superadmin):
    base = time.time() - 20_000
    fechas = [base + (i // 2) for i in range(60)] + [None, None]
    filas = []
    for i in range(0, len(fechas), 6):
        filas += _filas_descartadas(f"descarte-cursor-adm-{i}", TENANT, fechas[i:i + 6])
    try:
        client.portal.call(_insertar_pipelines_bulk, filas)
        todas = client.portal.call(
            sql, "SELECT pipeline_id, descartado_at FROM jacobs_pipelines WHERE status='discarded'", (), True)
        esperado = [p for p, _d in sorted(todas, key=lambda r: (r[1] is not None, r[1] or 0.0, r[0]), reverse=True)]
        for limite in (5, 50):
            por_offset, _ = _recorrer(client_superadmin, "/api/admin/pipelines/descartados", {}, limite, "offset")
            por_cursor, _ = _recorrer(client_superadmin, "/api/admin/pipelines/descartados", {}, limite, "cursor")
            assert por_offset == esperado, limite
            assert por_cursor == esperado, limite
    finally:
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE tenant_id=%s", (TENANT,))


# `params=` de httpx REEMPLAZA la query de la URL (no la mezcla): por eso
# `estado` va en `fijos`, nunca en la URL.
VISTAS = [("/api/pipelines", {"estado": "discarded"}), ("/api/admin/pipelines/descartados", {})]


@pytest.mark.parametrize("url, fijos", VISTAS)
def test_cursor_y_offset_juntos_es_422(client, client_superadmin, url, fijos):
    cliente = client_superadmin if "admin" in url else client
    extra = {} if "admin" in url else {"headers": cabeceras(client, "descarte-cursor-422", "operator")}
    resp = cliente.get(url, params={**fijos, "cursor": pag.codificar_cursor(1.0, "x"), "offset": 3}, **extra)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "cursor_y_offset"


@pytest.mark.parametrize("url, fijos", VISTAS)
def test_un_cursor_ilegible_por_http_es_422(client, client_superadmin, url, fijos):
    cliente = client_superadmin if "admin" in url else client
    extra = {} if "admin" in url else {"headers": cabeceras(client, "descarte-cursor-422", "operator")}
    resp = cliente.get(url, params={**fijos, "cursor": _b64('[NaN, "x"]')}, **extra)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "cursor_invalido"


@pytest.mark.parametrize("url, fijos, modulo", [
    ("/api/pipelines", {"estado": "discarded"}, "api.pipelines"),
    ("/api/admin/pipelines/descartados", {}, "api.admin.pipelines_ocultos"),
])
def test_un_cursor_ilegible_no_toma_conexion_del_pool(client, client_superadmin, monkeypatch, url, fijos, modulo):
    """Un 422 `cursor_invalido` no necesita la base: el cursor se decodifica
    ANTES de `get_pool()`/`pool.acquire()` en las dos vistas. Pool espía que
    revienta si se lo pide: con el código viejo (decodificar dentro del
    `acquire()` en la vista del dueño) esta prueba fallaba."""
    import importlib

    pedidos = []

    async def _pool_espia():
        pedidos.append("get_pool")
        raise AssertionError("un cursor ilegible pidió el pool")

    monkeypatch.setattr(importlib.import_module(modulo), "get_pool", _pool_espia)
    cliente = client_superadmin if "admin" in url else client
    extra = {} if "admin" in url else {"headers": cabeceras(client, "descarte-cursor-422", "operator")}
    resp = cliente.get(url, params={**fijos, "cursor": _b64('[NaN, "x"]')}, **extra)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "cursor_invalido"
    assert pedidos == []


def test_cursor_en_la_lista_principal_es_422(client):
    resp = client.get("/api/pipelines", params={"cursor": pag.codificar_cursor(1.0, "x")},
                      headers=cabeceras(client, "descarte-cursor-422", "operator"))
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "cursor_solo_descartados"


def test_sin_idx_pipelines_descartados_la_consulta_del_usuario_revienta(client):
    """El FORCE INDEX es de verdad un FORCE: sin el índice, MariaDB da 1176
    (un hint ignorable dejaría correr la consulta por otro plan). Mismo
    patrón que test_pipelines_del_usuario_depende_de_idx_pipelines_visibles."""
    ddl = ("CREATE INDEX idx_pipelines_descartados ON jacobs_pipelines "
           "(user_id, tenant_id, status, descartado_at) ALGORITHM=INPLACE LOCK=NONE")
    try:
        client.portal.call(sql, "DROP INDEX idx_pipelines_descartados ON jacobs_pipelines")
        with pytest.raises(aiomysql.OperationalError) as excinfo:
            client.portal.call(sql, "EXPLAIN " + mod.SQL_DESCARTADOS_DEL_USUARIO,
                               ("x", "TENANT-SIN-IDX", 51, 0), True)
        assert excinfo.value.args[0] == 1176, excinfo.value
    finally:
        client.portal.call(sql, ddl)
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)


def _intercalados(n_duenio, n_otros, duenio, tenant):
    """Descartes del dueño INTERCALADOS con los de otros usuarios (la forma
    de producción): uno del dueño cada (n_otros // n_duenio) ajenos."""
    base = time.time() - 50_000
    filas = []
    paso = max(1, n_otros // max(1, n_duenio))
    k = propias = 0
    for i in range(n_otros):
        filas += _filas_descartadas(f"descarte-cursor-ajeno-{i % 97}", tenant, [base + k])
        k += 1
        if i % paso == 0 and propias < n_duenio:
            filas += _filas_descartadas(duenio, tenant, [base + k])
            k += 1
            propias += 1
    return filas


def test_pagina_profunda_por_cursor_lee_limite_mas_uno_en_la_vista_del_usuario(client):
    duenio = "descarte-cursor-hr-duenio"
    filas = _intercalados(600, 1800, duenio, TENANT)
    try:
        client.portal.call(_insertar_pipelines_bulk, filas)
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)
        propias = [f for f in filas if f[4] == duenio]
        esperado = _esperado(propias)
        limite = mod.LISTA_PIPELINES_MAX
        # cursor en la fila 500 (página 11 de 50): lo que la interfaz pide
        # tras diez "ver más".
        ancla = next(f for f in propias if f[0] == esperado[499])
        consulta, params = pag.consulta_y_parametros(
            mod.SQL_DESCARTADOS_DEL_USUARIO_BASE, (duenio, TENANT), limite, 0,
            pag.codificar_cursor(ancla[9], ancla[0]))
        explain, handler, filas_leidas = client.portal.call(_explain_y_handler_read, consulta, params)
        assert explain["key"] == "idx_pipelines_descartados", explain
        extra = (explain["Extra"] or "").lower()
        assert "filesort" not in extra and "temporary" not in extra, explain
        assert [f[0] for f in filas_leidas] == esperado[500:500 + limite + 1]
        assert sum(handler.values()) <= limite + 3, handler
        # Control: el mismo punto por OFFSET sí lee todo lo anterior -- si
        # esto no fuera cierto, la cota de arriba no probaría nada.
        _e, handler_off, _f = client.portal.call(
            _explain_y_handler_read, mod.SQL_DESCARTADOS_DEL_USUARIO, (duenio, TENANT, limite + 1, 500))
        assert sum(handler_off.values()) >= 500, handler_off
    finally:
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE tenant_id=%s", (TENANT,))


def test_pagina_profunda_por_cursor_lee_limite_mas_uno_en_la_vista_del_admin(client):
    from api.admin.pipelines_ocultos import LIMITE_MAX, SQL_DESCARTADOS_ADMIN, SQL_DESCARTADOS_ADMIN_BASE

    filas = _intercalados(300, 900, "descarte-cursor-hr-adm", TENANT)
    try:
        client.portal.call(_insertar_pipelines_bulk, filas)
        client.portal.call(sql, "ANALYZE TABLE jacobs_pipelines", (), True)
        todas = client.portal.call(
            sql, "SELECT pipeline_id, descartado_at FROM jacobs_pipelines WHERE status='discarded'", (), True)
        esperado = [p for p, _d in sorted(todas, key=lambda r: (r[1] is not None, r[1] or 0.0, r[0]), reverse=True)]
        d_ancla = dict(todas)[esperado[799]]
        consulta, params = pag.consulta_y_parametros(
            SQL_DESCARTADOS_ADMIN_BASE, (), LIMITE_MAX, 0, pag.codificar_cursor(d_ancla, esperado[799]))
        explain, handler, filas_leidas = client.portal.call(_explain_y_handler_read, consulta, params)
        assert explain["key"] == "idx_pipelines_ocultos", explain
        extra = (explain["Extra"] or "").lower()
        assert "filesort" not in extra and "temporary" not in extra, explain
        assert [f[0] for f in filas_leidas] == esperado[800:800 + LIMITE_MAX + 1]
        assert sum(handler.values()) <= LIMITE_MAX + 3, handler
        _e, handler_off, _f = client.portal.call(
            _explain_y_handler_read, SQL_DESCARTADOS_ADMIN, (LIMITE_MAX + 1, 800))
        assert sum(handler_off.values()) >= 800, handler_off
    finally:
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE tenant_id=%s", (TENANT,))
