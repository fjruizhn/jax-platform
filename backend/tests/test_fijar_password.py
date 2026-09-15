"""Fijar contraseña por admin y cambio obligatorio (2026-09-15, admin usuarios,
DECISIONES de Fernando que revierten U2; Ruling U34).

El superadmin le fija la contraseña a OTRO usuario; ese usuario queda con
jax_users.must_change_password y, mientras la tenga, su sesión sólo sirve para
/me, /me/password, /refresh y /logout (403 cambio_de_password_requerido en
todo lo demás, y WS/SSE rechazados). Mi cuenta y el reset por enlace la limpian.
"""
import uuid
from datetime import timedelta

import pytest

from auth.middleware import verificar_sesion
from tests.identidades import auth, sql, token_para
from tiempo import utc_ahora

CLAVE = "clave-vieja-123"
NUEVA = "clave-nueva-456"
FIJADA = "clave-fijada-789"


def _admin():
    return auth(token_para(1, role="superadmin"))


async def _marcar(user_id, valor=True):
    await sql("UPDATE jax_users SET must_change_password = %s WHERE user_id = %s", (valor, user_id))


async def _marca(user_id):
    ((m,),) = await sql("SELECT must_change_password FROM jax_users WHERE user_id = %s", (user_id,), True)
    return bool(m)


# --------------------------------------------------------------- esquema

def test_columna_must_change_password_no_nula_y_falsa_por_defecto(client):
    filas = client.portal.call(
        sql,
        "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jax_users' AND COLUMN_NAME = 'must_change_password'",
        (), True)
    assert [tuple(f) for f in filas] == [("tinyint", "NO", "0")]


def test_verificar_sesion_informa_la_marca(client, usuarios):
    u, _ = usuarios()
    payload = {"type": "access", "user_id": str(u), "tenant_id": "1", "tv": 0}
    assert client.portal.call(verificar_sesion, payload, "access").must_change_password is False
    client.portal.call(_marcar, u)
    # Desde la Task 2 el camino por defecto la hace cumplir (403, ver
    # test_verificar_sesion_con_la_marca_rechaza_salvo_que_se_admita); acá se
    # fija que la marca se INFORMA a quien la admite.

    async def admitida():
        return await verificar_sesion(payload, "access", admite_cambio_pendiente=True)

    assert client.portal.call(admitida).must_change_password is True


# ----------------------------------------------------- la marca se cumple

import ast  # noqa: E402
import pathlib  # noqa: E402

from fastapi.routing import APIRoute  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import auth.middleware as mw  # noqa: E402


def _login(client, email, password):
    try:
        return client.post("/api/auth/login", json={"email": email, "password": password})
    finally:
        client.cookies.clear()


def _cambiar(client, token, actual, nueva):
    try:
        return client.post("/api/auth/me/password", json={"current_password": actual, "new_password": nueva},
                           headers=auth(token))
    finally:
        client.cookies.clear()


def test_con_la_marca_solo_entran_me_mi_cuenta_refresh_y_logout(client, usuarios):
    s, _ = usuarios(role="superadmin")
    client.portal.call(_marcar, s)
    h = auth(token_para(s, role="superadmin"))
    for metodo, url in (("GET", "/api/facets"), ("GET", "/api/admin/users"), ("GET", "/api/pipelines")):
        r = client.request(metodo, url, headers=h)
        assert (r.status_code, r.json()["detail"]) == (403, "cambio_de_password_requerido"), url
    assert client.get("/api/auth/me", headers=h).status_code == 200
    r = client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={token_para(s, tipo='refresh')}"})
    client.cookies.clear()
    assert r.status_code == 200, r.text
    assert client.post("/api/auth/logout").status_code == 200


def test_sin_la_marca_nada_cambia(client, usuarios):
    u, _ = usuarios()
    assert client.get("/api/facets", headers=auth(token_para(u))).status_code == 200


def test_una_sesion_revocada_con_la_marca_sigue_siendo_401(client, usuarios):
    u, _ = usuarios(token_version=1)
    client.portal.call(_marcar, u)
    r = client.get("/api/facets", headers=auth(token_para(u, tv=0)))
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_invalida")


def test_con_la_marca_el_ws_se_cierra_con_4001(client, usuarios):
    u, _ = usuarios()
    client.portal.call(_marcar, u)
    with client.websocket_connect(f"/ws/{u}") as ws:
        ws.send_json({"type": "auth", "token": token_para(u)})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4001


def test_verificar_sesion_con_la_marca_rechaza_salvo_que_se_admita(client, usuarios):
    """Cubre el SSE: su re-verificación (reverificar_sesion) usa el camino por defecto."""
    from fastapi import HTTPException

    u, _ = usuarios()
    client.portal.call(_marcar, u)
    payload = {"type": "access", "user_id": str(u), "tenant_id": "1", "tv": 0}
    with pytest.raises(HTTPException) as exc:
        client.portal.call(verificar_sesion, payload, "access")
    assert (exc.value.status_code, exc.value.detail) == (403, "cambio_de_password_requerido")
    with pytest.raises(HTTPException):
        client.portal.call(mw.reverificar_sesion, mw.AuthUser(user_id=str(u), tenant_id="1", role="operator"))

    async def admitida():
        return await verificar_sesion(payload, "access", admite_cambio_pendiente=True)

    assert client.portal.call(admitida).must_change_password is True


def _dependencias(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependencias(sub)


def test_solo_me_y_mi_cuenta_admiten_la_marca():
    """Puro (importa la app, no la arranca). Una ruta nueva que pida la
    dependencia permisiva sin sumarse a la lista rompe este test.

    FastAPI 0.139 ya no aplana los routers incluidos: `app.routes` trae
    `_IncludedRouter`, y un recorrido directo de APIRoute daba el conjunto
    VACÍO (medido 2026-09-15). `iter_route_contexts` es la API pública que da
    la ruta EFECTIVA: el path con el prefijo de include_router y el dependant
    con las dependencias a nivel de router."""
    from fastapi.routing import iter_route_contexts

    from main import app

    efectivas = [rc for rc in iter_route_contexts(app.routes) if isinstance(rc.original_route, APIRoute)]
    # Contra la vacuidad: si el recorrido no ve las rutas de la app, el
    # conjunto de abajo sale vacío y el test no prueba nada.
    assert ("POST", "/api/auth/login") in {(m, rc.path) for rc in efectivas for m in rc.methods}
    permisivas = {
        (metodo, rc.path)
        for rc in efectivas
        if mw.get_current_user_con_cambio_pendiente in set(_dependencias(rc.dependant))
        for metodo in rc.methods
    }
    assert permisivas == set(mw.RUTAS_CON_CAMBIO_PENDIENTE)


def _llamadas_que_admiten_la_marca(arbol):
    """(función que la contiene, línea) de cada llamada a verificar_sesion que
    admite la marca o que no se puede leer: admite_cambio_pendiente con algo que
    no sea la constante False (True, una variable, una expresión), o **kwargs
    (que puede traerla sin que se vea). Lee el AST: los espacios, los
    paréntesis o un dict desempacado no la esconden."""
    hallados = []

    def visitar(nodo, funcion):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcion = nodo.name
        if isinstance(nodo, ast.Call):
            f = nodo.func
            nombre = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if nombre == "verificar_sesion":
                for kw in nodo.keywords:
                    if kw.arg is None or (kw.arg == "admite_cambio_pendiente" and not (
                            isinstance(kw.value, ast.Constant) and kw.value.value is False)):
                        hallados.append((funcion, nodo.lineno))
        for hijo in ast.iter_child_nodes(nodo):
            visitar(hijo, funcion)

    visitar(arbol, None)
    return hallados


def test_nadie_mas_admite_la_marca():
    """Sólo dos lugares admiten la marca: la dependencia permisiva (que usan
    /me y /me/password) y /refresh. Fix ronda 1 de la review: antes contaba el
    texto exacto `admite_cambio_pendiente=True`, que `= True` con espacios o
    `**{...}` esquivaban."""
    raiz = pathlib.Path(__file__).resolve().parents[1]
    hallados = set()
    for archivo in raiz.rglob("*.py"):
        partes = archivo.relative_to(raiz).parts
        if partes[0] in ("tests", ".venv") or "site-packages" in partes:
            continue
        ruta = archivo.relative_to(raiz).as_posix()
        arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=ruta)
        hallados |= {(ruta, funcion) for funcion, _ in _llamadas_que_admiten_la_marca(arbol)}
    assert hallados == {
        ("auth/middleware.py", "get_current_user_con_cambio_pendiente"),
        ("api/auth.py", "refresh"),
    }


def test_el_guard_del_opt_in_ve_las_variantes_escondidas():
    """Puro. Fija que el detector del test de arriba no depende del texto."""
    fuente = (
        "async def a(p):\n    return await verificar_sesion(p, 'access', admite_cambio_pendiente = True)\n"
        "async def b(p):\n    return await mw.verificar_sesion(p, 'access', **{'admite_cambio_pendiente': True})\n"
        "async def c(p, x):\n    return await verificar_sesion(p, 'access', admite_cambio_pendiente=x)\n"
        "async def d(p):\n    return await verificar_sesion(p, 'access', admite_cambio_pendiente=False)\n"
        "async def e(p):\n    return await verificar_sesion(p, 'access')\n"
    )
    assert [f for f, _ in _llamadas_que_admiten_la_marca(ast.parse(fuente))] == ["a", "b", "c"]


def test_login_y_me_informan_la_marca(client, usuarios):
    u, email = usuarios(password=CLAVE)
    assert _login(client, email, CLAVE).json()["must_change_password"] is False
    client.portal.call(_marcar, u)
    r = _login(client, email, CLAVE)
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert client.get("/api/auth/me", headers=auth(r.json()["access_token"])).json()["must_change_password"] is True


def test_mi_cuenta_limpia_la_marca_y_la_app_vuelve(client, usuarios):
    u, _ = usuarios(password=CLAVE)
    client.portal.call(_marcar, u)
    r = _cambiar(client, token_para(u), CLAVE, NUEVA)
    assert r.status_code == 200, r.text
    assert client.portal.call(_marca, u) is False
    nuevo = auth(r.json()["access_token"])
    assert client.get("/api/auth/me", headers=nuevo).json()["must_change_password"] is False
    assert client.get("/api/facets", headers=nuevo).status_code == 200


def test_mi_cuenta_con_la_marca_no_acepta_la_misma_contrasena(client, usuarios):
    u, _ = usuarios(password=CLAVE)
    client.portal.call(_marcar, u)
    r = _cambiar(client, token_para(u), CLAVE, CLAVE)
    assert (r.status_code, r.json()["detail"]) == (400, "password_igual_a_la_actual")
    assert client.portal.call(_marca, u) is True


def test_mi_cuenta_sin_la_marca_acepta_la_misma_contrasena(client, usuarios):
    """P1 vale SÓLO con la marca (fix ronda 1, M3): sin ella, Mi cuenta no
    cambia -- reenviar la actual como nueva sigue siendo un 200."""
    u, _ = usuarios(password=CLAVE)
    r = _cambiar(client, token_para(u), CLAVE, CLAVE)
    assert r.status_code == 200, r.text
    assert client.portal.call(_marca, u) is False


def test_el_reset_por_enlace_limpia_la_marca(client, usuarios):
    u, email = usuarios(password=CLAVE)
    client.portal.call(_marcar, u)
    token = str(uuid.uuid4())
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                            "VALUES (%s, %s, %s, 'test')", (u, token, utc_ahora() + timedelta(hours=1)))
    assert client.post("/api/auth/reset-password", json={"token": token, "password": NUEVA}).status_code == 200
    assert client.portal.call(_marca, u) is False
    assert _login(client, email, NUEVA).json()["must_change_password"] is False
