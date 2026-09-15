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
    # Fix ronda 2 (Ruling F3): los nombres locales ligados a verificar_sesion,
    # ANTES de buscar llamadas -- `from auth.middleware import verificar_sesion
    # as vs` los esquivaba. `import auth.middleware as mw` + `mw.verificar_sesion`
    # ya lo cubre el chequeo de Attribute.attr.
    nombres = {"verificar_sesion"} | {
        alias.asname or alias.name
        for nodo in ast.walk(arbol) if isinstance(nodo, ast.ImportFrom)
        for alias in nodo.names if alias.name == "verificar_sesion"
    }

    def visitar(nodo, funcion):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcion = nodo.name
        if isinstance(nodo, ast.Call):
            f = nodo.func
            nombre = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if nombre in nombres:
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
        "from auth.middleware import verificar_sesion as vs\n"
        "async def f(p):\n    return await vs(p, 'access', admite_cambio_pendiente=True)\n"
    )
    assert [f for f, _ in _llamadas_que_admiten_la_marca(ast.parse(fuente))] == ["a", "b", "c", "f"]


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


# ------------------------------------------------------ endpoint del admin

from contextlib import asynccontextmanager  # noqa: E402

import api.admin.users as users_mod  # noqa: E402
import api.auth as auth_mod  # noqa: E402
from auth import conexiones as conexiones_mod  # noqa: E402
from jax_engine.websocket_hub import ws_hub  # noqa: E402


async def _version(user_id):
    ((tv,),) = await sql("SELECT token_version FROM jax_users WHERE user_id = %s", (user_id,), True)
    return tv


async def _auditoria(target):
    return [tuple(f) for f in await sql("SELECT action, detail FROM user_admin_audit WHERE target_user_id = %s "
                                        "ORDER BY id", (target,), True)]


@pytest.fixture
def cortes(monkeypatch):
    """U9: cada corte registra la token_version que ve OTRA conexión (si
    corriera dentro de la transacción, vería la vieja)."""
    registro = []

    async def ws(user_id, code=4001):
        registro.append(("ws", user_id, await _version(int(user_id))))
        return 0

    async def sse(user_id):
        registro.append(("sse", user_id, await _version(int(user_id))))
        return 0

    monkeypatch.setattr(ws_hub, "close_user", ws)
    monkeypatch.setattr(conexiones_mod, "close_user_streams", sse)
    return registro


def _fijar(client, target, password=FIJADA, cabeceras=None, **extra):
    return client.post(f"/api/admin/users/{target}/password", json={"new_password": password, **extra},
                       headers=cabeceras or _admin())


def test_fijar_password_cambia_corta_marca_y_audita_sin_la_contrasena(client, usuarios, cortes):
    u, email = usuarios(password=CLAVE)
    viejo = token_para(u)
    r = _fijar(client, u)
    assert (r.status_code, r.json()) == (200, {"ok": True})
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 401, "las sesiones viejas mueren"
    assert _login(client, email, CLAVE).status_code == 401
    r = _login(client, email, FIJADA)
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert client.portal.call(_version, u) == 1
    ((accion, detalle),) = client.portal.call(_auditoria, u)
    assert (accion, detalle) == ("password_set_by_admin", None)
    assert cortes == [("ws", str(u), 1), ("sse", str(u), 1)], "corte tras el commit"


def test_fijar_password_aplica_la_regla_sin_tocar_nada(client, usuarios, cortes):
    u, _ = usuarios(password=CLAVE)
    for password, codigo in (("corta", "password_corta"), ("ñ" * 37, "password_larga")):
        r = _fijar(client, u, password)
        assert (r.status_code, r.json()["detail"]) == (400, codigo)
    assert client.portal.call(_version, u) == 0 and client.portal.call(_marca, u) is False
    assert client.portal.call(_auditoria, u) == [] and cortes == []


def test_fijar_password_limpia_el_bloqueo(client, usuarios, cortes):
    u, email = usuarios(password=CLAVE)
    client.portal.call(sql, "UPDATE jax_users SET failed_attempts = 5, locked_until = %s WHERE user_id = %s",
                       (utc_ahora() + timedelta(minutes=15), u))
    assert _fijar(client, u).status_code == 200
    ((intentos, hasta),) = client.portal.call(
        sql, "SELECT failed_attempts, locked_until FROM jax_users WHERE user_id = %s", (u,), True)
    assert (intentos, hasta) == (0, None)
    assert _login(client, email, FIJADA).status_code == 200


def test_fijar_password_borra_solo_los_enlaces_pendientes(client, usuarios, cortes):
    u, _ = usuarios(password=CLAVE)
    pendiente, usado = str(uuid.uuid4()), str(uuid.uuid4())
    vence = utc_ahora() + timedelta(hours=1)
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address, used) "
                            "VALUES (%s, %s, %s, 'test', FALSE), (%s, %s, %s, 'test', TRUE)",
                       (u, pendiente, vence, u, usado, vence))
    assert _fijar(client, u).status_code == 200
    filas = client.portal.call(sql, "SELECT token FROM password_reset_tokens WHERE user_id = %s", (u,), True)
    assert [f[0] for f in filas] == [usado]
    r = client.post("/api/auth/reset-password", json={"token": pendiente, "password": NUEVA})
    assert (r.status_code, r.json()["detail"]) == (400, "reset_token_invalido")
    assert client.portal.call(_marca, u) is True, "un enlace viejo no apaga la marca"


def test_fijar_password_a_un_inactivo_no_lo_activa(client, usuarios, cortes):
    u, email = usuarios(status="inactive", password=CLAVE)
    assert _fijar(client, u).status_code == 200
    ((estado,),) = client.portal.call(sql, "SELECT status FROM jax_users WHERE user_id = %s", (u,), True)
    assert estado == "inactive" and client.portal.call(_marca, u) is True
    r = _login(client, email, FIJADA)
    assert (r.status_code, r.json()["detail"]) == (403, "Usuario inactivo")


def test_fijar_password_rechazos(client, usuarios, cortes):
    s, _ = usuarios(role="superadmin")
    r = _fijar(client, s, cabeceras=auth(token_para(s, role="superadmin")))
    assert (r.status_code, r.json()["detail"]) == (403, "auto_accion_prohibida"), "la propia va por Mi cuenta"
    ido, _ = usuarios()
    assert client.post(f"/api/admin/users/{ido}/baja", headers=_admin()).status_code == 200
    assert (_fijar(client, ido).status_code, _fijar(client, ido).json()["detail"]) == (404, "usuario_no_encontrado")
    assert _fijar(client, 2**31 - 1).json()["detail"] == "usuario_no_encontrado"
    op, _ = usuarios()
    otro, _ = usuarios()
    assert _fijar(client, otro, cabeceras=auth(token_para(op))).status_code == 403
    assert _fijar(client, otro, must_change_password=False).status_code == 422, "extra='forbid'"
    assert client.portal.call(_version, otro) == 0


def test_fijar_password_si_el_corte_falla_responde_igual(client, usuarios, monkeypatch):
    async def revienta(user_id, code=4001):
        raise RuntimeError("hub caído")

    monkeypatch.setattr(ws_hub, "close_user", revienta)
    monkeypatch.setattr(conexiones_mod, "close_user_streams", revienta)
    u, _ = usuarios()
    assert _fijar(client, u).status_code == 200
    assert client.portal.call(_version, u) == 1


def test_fijar_password_hashea_antes_y_bloquea_en_el_orden_fijo(client, usuarios, cortes, monkeypatch):
    """bcrypt ANTES de abrir la transacción (nunca con filas tomadas), READ
    COMMITTED, superadmins -> usuario -> tokens -> auditoría (U11, U21, U33)."""
    u, _ = usuarios()
    pasos = []
    hash_real, transaccion_real = users_mod._hash, users_mod.transaccion

    def hash_que_graba(p):
        pasos.append("HASH")
        return hash_real(p)

    class _Graba:
        def __init__(self, cur):
            self._cur = cur

        def __getattr__(self, nombre):
            return getattr(self._cur, nombre)

        async def execute(self, consulta, args=()):
            pasos.append(" ".join(consulta.split()))
            return await self._cur.execute(consulta, args)

    @asynccontextmanager
    async def con_registro(*args, **kw):
        pasos.append(("BEGIN", args, kw))
        async with transaccion_real(*args, **kw) as cur:
            yield _Graba(cur)

    monkeypatch.setattr(users_mod, "_hash", hash_que_graba)
    monkeypatch.setattr(users_mod, "transaccion", con_registro)
    assert _fijar(client, u).status_code == 200
    assert pasos[0] == "HASH"
    assert pasos[1] == ("BEGIN", ("READ COMMITTED",), {})
    sqls = [p for p in pasos[2:] if isinstance(p, str)]
    orden = [next(i for i, q in enumerate(sqls) if cond(q)) for cond in (
        lambda q: q == " ".join(users_mod.SQL_SUPERADMINS_ACTIVOS.split()),
        lambda q: q.startswith("SELECT role, status, email FROM jax_users") and q.endswith("FOR UPDATE"),
        lambda q: q.startswith("UPDATE jax_users SET password_hash"),
        lambda q: q.startswith("DELETE FROM password_reset_tokens WHERE user_id"),
        lambda q: q.startswith("INSERT INTO user_admin_audit"),
    )]
    assert orden == sorted(orden), sqls


def test_mi_cuenta_no_deshace_una_contrasena_fijada_en_el_medio(client, usuarios, cortes, monkeypatch):
    """El admin fija la contraseña mientras la persona verifica la actual en
    Mi cuenta (ventana de bcrypt): Mi cuenta da 401 y queda lo del admin."""
    u, email = usuarios(password=CLAVE)
    verificar_real = auth_mod.verify_password

    async def verifica_y_el_admin_fija(plain, hashed):
        ok = await verificar_real(plain, hashed)
        from httpx import ASGITransport, AsyncClient
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(f"/api/admin/users/{u}/password", json={"new_password": FIJADA}, headers=_admin())
            assert r.status_code == 200, r.text
        return ok

    monkeypatch.setattr(auth_mod, "verify_password", verifica_y_el_admin_fija)
    r = _cambiar(client, token_para(u), CLAVE, NUEVA)
    monkeypatch.setattr(auth_mod, "verify_password", verificar_real)
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_invalida")
    assert client.portal.call(_marca, u) is True
    assert _login(client, email, FIJADA).status_code == 200
    assert [a for a, _ in client.portal.call(_auditoria, u)] == ["password_set_by_admin"]


# ------------------------------------- enlace de recuperación con la marca
# (Task 3 fix ronda 1, Ruling F5). Completar un enlace y apagar la marca se
# acepta; lo que no: terminar con la contraseña del admin (esquiva P1).

async def _enlace_pendiente(user_id):
    token = str(uuid.uuid4())
    await sql("INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
              "VALUES (%s, %s, %s, 'test')", (user_id, token, utc_ahora() + timedelta(hours=1)))
    return token


async def _usado(token):
    ((u,),) = await sql("SELECT used FROM password_reset_tokens WHERE token = %s", (token,), True)
    return bool(u)


def _resetear(client, token, password):
    return client.post("/api/auth/reset-password", json={"token": token, "password": password})


def test_el_enlace_no_acepta_la_contrasena_del_admin_y_el_mismo_token_sirve_despues(client, usuarios, cortes):
    u, email = usuarios(password=CLAVE)
    assert _fijar(client, u).status_code == 200
    token = client.portal.call(_enlace_pendiente, u)  # creado DESPUÉS de fijar
    r = _resetear(client, token, FIJADA)
    assert (r.status_code, r.json()["detail"]) == (400, "password_igual_a_la_actual")
    assert client.portal.call(_usado, token) is False, "el token no se consume"
    assert client.portal.call(_marca, u) is True
    r = _resetear(client, token, NUEVA)
    assert r.status_code == 200, r.text
    assert client.portal.call(_marca, u) is False
    assert _login(client, email, NUEVA).status_code == 200


def test_el_enlace_sin_la_marca_acepta_la_misma_contrasena(client, usuarios, cortes):
    """P1 vale SÓLO con la marca (como Mi cuenta, M3 de la Task 2)."""
    u, email = usuarios(password=CLAVE)
    token = client.portal.call(_enlace_pendiente, u)
    r = _resetear(client, token, CLAVE)
    assert r.status_code == 200, r.text
    assert client.portal.call(_usado, token) is True


def test_el_enlace_con_la_marca_rechaza_si_la_contrasena_cambio_antes_del_bloqueo(client, usuarios, cortes,
                                                                                  monkeypatch):
    """Defensa en profundidad (F5 c): entre la verificación sin bloqueo y el
    FOR UPDATE, la contraseña cambia y la marca sigue puesta (costura:
    verify_password). Bajo el bloqueo se compara el hash como string y se
    revierte ANTES de reclamar el token."""
    from db.seed import _hash

    u, _ = usuarios(password=CLAVE)
    assert _fijar(client, u).status_code == 200
    token = client.portal.call(_enlace_pendiente, u)
    otro_hash = _hash("clave-de-otro-000")
    verificar_real = auth_mod.verify_password

    async def verifica_y_cambia(plain, hashed):
        ok = await verificar_real(plain, hashed)
        await sql("UPDATE jax_users SET password_hash = %s WHERE user_id = %s", (otro_hash, u))
        return ok

    monkeypatch.setattr(auth_mod, "verify_password", verifica_y_cambia)
    r = _resetear(client, token, NUEVA)
    assert (r.status_code, r.json()["detail"]) == (400, "reset_token_invalido")
    assert client.portal.call(_usado, token) is False
    assert client.portal.call(_marca, u) is True
    ((h,),) = client.portal.call(sql, "SELECT password_hash FROM jax_users WHERE user_id = %s", (u,), True)
    assert h == otro_hash, "no se escribió nada"


def test_la_consulta_del_token_de_recuperacion_va_por_indices(client, usuarios):
    """LAS CUATRO (indexing): EXPLAIN sobre la consulta REAL (la constante),
    con un token que EXISTE (uno inexistente en un índice único colapsa el plan
    a 'Impossible WHERE' y no prueba nada): el token por su índice único y el
    usuario por PRIMARY, los dos const/eq_ref, sin filesort ni temporary."""
    u, _ = usuarios()
    token = client.portal.call(_enlace_pendiente, u)
    filas = [tuple(f) for f in client.portal.call(
        sql, "EXPLAIN " + auth_mod.SQL_TOKEN_DE_RECUPERACION, (token,), True)]
    planes = {f[2]: (f[3], f[5]) for f in filas}  # table -> (type, key)
    assert set(planes) == {"t", "u"}, filas
    assert planes["u"][0] in ("const", "eq_ref") and planes["u"][1] == "PRIMARY", filas
    assert planes["t"][0] in ("const", "eq_ref") and planes["t"][1] is not None, filas
    assert all("filesort" not in str(f[-1]) and "temporary" not in str(f[-1]) for f in filas), filas
