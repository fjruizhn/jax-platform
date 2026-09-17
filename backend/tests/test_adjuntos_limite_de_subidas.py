"""Límite de subidas por usuario (RD7, decisión del principal 2026-09-17).

POST /api/chat/upload: JAX_ADJUNTOS_SUBIDAS_POR_MINUTO por usuario, ventana
deslizante de 60 s (auth.rate_limit.SlidingWindowLimiter). Pasado: 429
`adjuntos_subidas_limite` con Retry-After. Corre en un middleware ASGI ANTES
de leer el cuerpo: FastAPI lee el multipart entero antes de resolver las
dependencias (lo fija el primer test), así que un Depends llegaría tarde."""
import asyncio
import json

import pytest
from fastapi import Depends, FastAPI, File, UploadFile

from adjuntos import limite_de_subidas as mod
from adjuntos.limites import LimitesDeAdjuntosInvalidos
from tests.identidades import cabeceras, token_para

RUTA = "/api/chat/upload"
LIMITE_CHUNKS = 8
CHUNK = b"x" * 65536


def _multipart():
    frontera = "fronteraRD7"
    cabeza = (f"--{frontera}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"f.bin\"\r\n"
              f"Content-Type: application/octet-stream\r\n\r\n").encode()
    cola = f"\r\n--{frontera}--\r\n".encode()
    partes = [cabeza] + [CHUNK] * LIMITE_CHUNKS + [cola]
    return f"multipart/form-data; boundary={frontera}", partes


class _Canal:
    """receive() que cuenta cuántos trozos del cuerpo se leyeron."""

    def __init__(self, partes):
        self.partes = list(partes)
        self.leidos = 0

    async def receive(self):
        if self.leidos < len(self.partes):
            i = self.leidos
            self.leidos += 1
            return {"type": "http.request", "body": self.partes[i], "more_body": i < len(self.partes) - 1}
        await asyncio.sleep(3600)


def _llamar(app, token=None, path=RUTA, method="POST", autorizacion=None):
    tipo, partes = _multipart()
    headers = [(b"content-type", tipo.encode())]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    elif autorizacion is not None:
        headers.append((b"authorization", autorizacion.encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": headers, "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80)}
    canal = _Canal(partes)
    enviados = []

    async def send(mensaje):
        enviados.append(mensaje)

    asyncio.run(app(scope, canal.receive, send))
    inicio = next(m for m in enviados if m["type"] == "http.response.start")
    cuerpo = b"".join(m.get("body", b"") for m in enviados if m["type"] == "http.response.body")
    return inicio["status"], dict(inicio["headers"]), cuerpo, canal


class _Interna:
    """App interna que lee el cuerpo entero y responde 200."""

    def __init__(self):
        self.llamadas = 0

    async def __call__(self, scope, receive, send):
        self.llamadas += 1
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


@pytest.fixture
def limite(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTOS_SUBIDAS_POR_MINUTO", "2")
    monkeypatch.setenv("JAX_ADJUNTOS_RECHAZO_ESPERA_MS", "1000")
    mod.reiniciar()
    frenos = []

    async def dormir(segundos):
        frenos.append(segundos)

    monkeypatch.setattr(mod, "_dormir", dormir)
    yield frenos
    mod.reiniciar()


# ------------------------------------------------ por qué no es un Depends

def test_fastapi_lee_el_multipart_entero_antes_de_resolver_las_dependencias():
    """Evidencia (FastAPI 0.139, fastapi/routing.py get_request_handler:
    `await request.form()` y DESPUÉS `solve_dependencies`). Si esto cambia,
    un Depends podría cortar antes del volcado y el middleware sobraría."""
    vistos = []
    canal_actual = {}

    def dependencia():
        vistos.append(canal_actual["canal"].leidos)

    app = FastAPI()

    @app.post(RUTA)
    async def subir(file: UploadFile = File(...), _=Depends(dependencia)):
        return {"ok": True}

    tipo, partes = _multipart()
    original = _Canal.__init__

    def init(self, p):
        original(self, p)
        canal_actual["canal"] = self

    try:
        _Canal.__init__ = init
        status, _, _, canal = _llamar(app)
    finally:
        _Canal.__init__ = original
    assert status == 200
    assert vistos == [len(partes)]  # la dependencia corrió con el cuerpo ya leído entero


# ------------------------------------------------------------- el límite

def test_pasado_el_limite_es_429_sin_leer_el_cuerpo_ni_llegar_a_la_ruta(limite):
    interna = _Interna()
    app = mod.LimiteDeSubidas(interna)
    token = token_para(5)
    for _ in range(2):
        status, _, _, canal = _llamar(app, token)
        assert status == 200 and canal.leidos == LIMITE_CHUNKS + 2
    status, headers, cuerpo, canal = _llamar(app, token)
    assert status == 429
    assert canal.leidos == 0 and interna.llamadas == 2
    segundos = int(headers[b"retry-after"])
    assert 1 <= segundos <= 60
    assert headers[b"content-type"] == b"application/json"
    assert json.loads(cuerpo) == {"detail": {"code": "adjuntos_subidas_limite", "retry_after": segundos}}


def test_retry_after_dice_cuando_se_libera_el_lugar(limite, monkeypatch):
    reloj = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: reloj[0])
    app = mod.LimiteDeSubidas(_Interna())
    token = token_para(5)
    _llamar(app, token)
    reloj[0] += 20
    _llamar(app, token)
    reloj[0] += 5
    _, headers, _, _ = _llamar(app, token)
    assert int(headers[b"retry-after"]) == 35  # el primero sale de la ventana a los 60 s
    reloj[0] += 35
    assert _llamar(app, token)[0] == 200


def test_el_limite_es_por_usuario(limite):
    app = mod.LimiteDeSubidas(_Interna())
    for _ in range(2):
        _llamar(app, token_para(5))
    assert _llamar(app, token_para(5))[0] == 429
    assert _llamar(app, token_para(6))[0] == 200


# ---------------------- R28: sin token de acceso válido, el 401 de la ruta

def _token_vencido():
    import time as _t
    from jose import jwt as _jwt
    from auth.jwt import ALGORITHM, SECRET
    return _jwt.encode({"user_id": "5", "tenant_id": "1", "role": "operator", "tv": 0,
                        "exp": int(_t.time()) - 30, "type": "access"}, SECRET, algorithm=ALGORITHM)


def _firmado(**payload):
    import time as _t
    from jose import jwt as _jwt
    from auth.jwt import ALGORITHM, SECRET
    base = {"user_id": "5", "tenant_id": "1", "role": "operator", "tv": 0, "exp": int(_t.time()) + 600,
            "type": "access"}
    base.update(payload)
    return _jwt.encode({k: v for k, v in base.items() if v is not None}, SECRET, algorithm=ALGORITHM)


_SIN_ACCESO_VALIDO = {
    "sin_cabecera": lambda: None,
    "cabecera_vacia": lambda: "",
    "otro_esquema": lambda: "Basic YTpi",
    "bearer_sin_token": lambda: "Bearer ",
    "token_basura": lambda: "Bearer no-es-un-jwt",
    "firma_ajena": lambda: "Bearer " + token_para(5)[:-3] + "AAA",
    "vencido": lambda: "Bearer " + _token_vencido(),
    "de_refresh": lambda: "Bearer " + token_para(5, tipo="refresh"),
    "sin_user_id": lambda: "Bearer " + _firmado(user_id=None),
    "user_id_no_entero": lambda: "Bearer " + _firmado(user_id="../5"),
    "tv_no_entero": lambda: "Bearer " + _firmado(tv="x"),
}


def _ruta_sola():
    """La ruta real con su dependencia de auth, SIN el middleware: su 401 es
    la referencia."""
    import api.upload as upload_mod
    app = FastAPI()
    app.include_router(upload_mod.router)
    return app


@pytest.mark.parametrize("caso", sorted(_SIN_ACCESO_VALIDO))
def test_sin_token_de_acceso_valido_el_middleware_da_el_mismo_401_que_la_ruta_sin_leer_el_cuerpo(limite, caso):
    import main
    autorizacion = _SIN_ACCESO_VALIDO[caso]()
    ref_status, ref_headers, ref_cuerpo, ref_canal = _llamar(_ruta_sola(), autorizacion=autorizacion)
    assert ref_status == 401 and ref_canal.leidos > 0  # la ruta lee el cuerpo antes de su 401
    status, headers, cuerpo, canal = _llamar(main.app, autorizacion=autorizacion)
    assert (status, headers, cuerpo) == (ref_status, ref_headers, ref_cuerpo)
    assert canal.leidos == 0
    # R30: el 401 del middleware espera lo mismo que el 429 (1000 ms en el fixture).
    assert limite == [1.0]


def test_el_401_del_middleware_no_gasta_cupo(limite):
    app = mod.LimiteDeSubidas(_Interna())
    for _ in range(5):
        assert _llamar(app, autorizacion="Bearer basura")[0] == 401
    for _ in range(2):
        assert _llamar(app, token_para(5))[0] == 200
    assert _llamar(app, token_para(5))[0] == 429


def test_un_token_con_firma_valida_pasa_a_la_ruta_que_revisa_la_revocacion(limite):
    """El middleware no mira la base: un token firmado de un usuario dado de
    baja (o con token_version vieja) llega a la ruta, que es la que lo
    rechaza con la base."""
    interna = _Interna()
    app = mod.LimiteDeSubidas(interna)
    assert _llamar(app, token_para(987654321, tv=99))[0] == 200
    assert interna.llamadas == 1


def test_el_codigo_del_limite_esta_declarado():
    from adjuntos.errores import CODIGOS
    assert mod.CODIGO in CODIGOS


@pytest.mark.parametrize("path,method", [("/api/chat", "POST"), ("/api/chat/adjuntos", "GET"),
                                         (RUTA, "GET"), (RUTA + "/x", "POST")])
def test_otras_rutas_no_se_limitan(limite, path, method):
    app = mod.LimiteDeSubidas(_Interna())
    for _ in range(5):
        assert _llamar(app, token_para(5), path=path, method=method)[0] == 200


def test_cambiar_el_valor_rehace_el_limitador(limite, monkeypatch):
    app = mod.LimiteDeSubidas(_Interna())
    for _ in range(2):
        _llamar(app, token_para(5))
    assert _llamar(app, token_para(5))[0] == 429
    monkeypatch.setenv("JAX_ADJUNTOS_SUBIDAS_POR_MINUTO", "3")
    assert _llamar(app, token_para(5))[0] == 200


# ----------------------------------------------------- entorno fail-closed

@pytest.mark.parametrize("valor", [None, "", "0", "-1", "601", "abc", "030", " 30", "30.0", "1e2"])
def test_variable_ausente_o_fuera_de_rango_no_arranca(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("JAX_ADJUNTOS_SUBIDAS_POR_MINUTO", raising=False)
    else:
        monkeypatch.setenv("JAX_ADJUNTOS_SUBIDAS_POR_MINUTO", valor)
    with pytest.raises(LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_subidas_por_minuto()
    assert "JAX_ADJUNTOS_SUBIDAS_POR_MINUTO" in str(e.value)


@pytest.mark.parametrize("valor", ["1", "30", "600"])
def test_variable_en_rango(monkeypatch, valor):
    monkeypatch.setenv("JAX_ADJUNTOS_SUBIDAS_POR_MINUTO", valor)
    assert mod.cargar_subidas_por_minuto() == int(valor)
    assert (mod.SUBIDAS_POR_MINUTO_MIN, mod.SUBIDAS_POR_MINUTO_MAX, mod.VENTANA_SEGUNDOS) == (1, 600, 60)


def test_sin_variable_una_subida_autenticada_falla_cerrado(monkeypatch):
    monkeypatch.delenv("JAX_ADJUNTOS_SUBIDAS_POR_MINUTO", raising=False)
    mod.reiniciar()
    app = mod.LimiteDeSubidas(_Interna())
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        _llamar(app, token_para(5))


def test_lifespan_valida_la_variable_antes_de_abrir_la_base(monkeypatch):
    import main

    llamadas = []

    async def pool_espia():
        llamadas.append("pool")

    monkeypatch.delenv("JAX_ADJUNTOS_SUBIDAS_POR_MINUTO", raising=False)
    monkeypatch.setattr(main, "get_pool", pool_espia)

    async def arrancar():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(LimitesDeAdjuntosInvalidos) as e:
        asyncio.run(arrancar())
    assert "JAX_ADJUNTOS_SUBIDAS_POR_MINUTO" in str(e.value)
    assert llamadas == []


def test_la_app_monta_el_middleware_dentro_de_cors():
    """CORS tiene que envolver al 429 (si no, el navegador de otro origen no
    lo puede leer); en la lista de Starlette, el primero es el de afuera."""
    import main
    from fastapi.middleware.cors import CORSMiddleware
    clases = [m.cls for m in main.app.user_middleware]
    assert mod.LimiteDeSubidas in clases
    assert clases.index(CORSMiddleware) < clases.index(mod.LimiteDeSubidas)


# ------------------------------------------------------ por HTTP (con DB)

def test_por_http_la_tercera_subida_es_429_con_retry_after(client, monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTOS_SUBIDAS_POR_MINUTO", "2")
    mod.reiniciar()
    hdrs = cabeceras(client, "test-limite-de-subidas")
    try:
        for _ in range(2):
            r = client.post(RUTA, files={"file": ("n.txt", b"hola", "text/plain")}, headers=hdrs)
            assert r.status_code == 200, r.text
        r = client.post(RUTA, files={"file": ("n.txt", b"hola", "text/plain")}, headers=hdrs)
        assert r.status_code == 429
        assert 1 <= int(r.headers["retry-after"]) <= 60
        assert r.json()["detail"]["code"] == "adjuntos_subidas_limite"
        otro = cabeceras(client, "test-limite-de-subidas-otro")
        assert client.post(RUTA, files={"file": ("n.txt", b"hola", "text/plain")}, headers=otro).status_code == 200
    finally:
        mod.reiniciar()


def test_el_429_espera_un_segundo_sin_leer_el_cuerpo_y_sin_cortar_la_conexion(limite):
    """Medido en RD7 (flood c=25, un usuario, 10 MB): responder el 429 al
    instante dejaba a uvicorn leyendo y descartando ~540 cuerpos/s en el
    event loop (health p95 37-40 ms); con `Connection: close` el 19 % de los
    clientes recibía un reset en vez del 429 (health p95 6,2 ms); frenando
    1 s antes de responder, sin leer el cuerpo (TCP frena al cliente),
    health p95 0,30 ms y 0 resets."""
    interna = _Interna()
    app = mod.LimiteDeSubidas(interna)
    for _ in range(2):
        _llamar(app, token_para(5))
    assert limite == []  # lo permitido no se frena
    status, headers, _, canal = _llamar(app, token_para(5))
    assert status == 429 and canal.leidos == 0
    assert limite == [1.0]  # JAX_ADJUNTOS_RECHAZO_ESPERA_MS=1000
    assert b"connection" not in headers


@pytest.mark.parametrize("ms,segundos", [("0", 0.0), ("250", 0.25), ("5000", 5.0)])
def test_la_espera_de_rechazo_del_429_usa_el_valor_configurado(limite, monkeypatch, ms, segundos):
    monkeypatch.setenv("JAX_ADJUNTOS_RECHAZO_ESPERA_MS", ms)
    app = mod.LimiteDeSubidas(_Interna())
    for _ in range(3):
        _llamar(app, token_para(5))
    assert limite == [segundos]


@pytest.mark.parametrize("valor", [None, "", "-1", "5001", "abc", "01000", " 1000", "1000.0", "1e3"])
def test_espera_de_rechazo_ausente_o_fuera_de_rango_no_arranca(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("JAX_ADJUNTOS_RECHAZO_ESPERA_MS", raising=False)
    else:
        monkeypatch.setenv("JAX_ADJUNTOS_RECHAZO_ESPERA_MS", valor)
    with pytest.raises(LimitesDeAdjuntosInvalidos) as e:
        mod.cargar_espera_de_rechazo_ms()
    assert "JAX_ADJUNTOS_RECHAZO_ESPERA_MS" in str(e.value)


@pytest.mark.parametrize("valor", ["0", "1", "1000", "5000"])
def test_espera_de_rechazo_en_rango(monkeypatch, valor):
    monkeypatch.setenv("JAX_ADJUNTOS_RECHAZO_ESPERA_MS", valor)
    assert mod.cargar_espera_de_rechazo_ms() == int(valor)
    assert (mod.ESPERA_DE_RECHAZO_MS_MIN, mod.ESPERA_DE_RECHAZO_MS_MAX) == (0, 5000)


def test_sin_espera_configurada_un_rechazo_falla_cerrado(limite, monkeypatch):
    app = mod.LimiteDeSubidas(_Interna())
    for _ in range(2):
        _llamar(app, token_para(5))
    monkeypatch.delenv("JAX_ADJUNTOS_RECHAZO_ESPERA_MS")
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        _llamar(app, token_para(5))


def test_lifespan_valida_la_espera_de_rechazo_antes_de_abrir_la_base(monkeypatch):
    import main

    llamadas = []

    async def pool_espia():
        llamadas.append("pool")

    monkeypatch.delenv("JAX_ADJUNTOS_RECHAZO_ESPERA_MS", raising=False)
    monkeypatch.setattr(main, "get_pool", pool_espia)

    async def arrancar():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(LimitesDeAdjuntosInvalidos) as e:
        asyncio.run(arrancar())
    assert "JAX_ADJUNTOS_RECHAZO_ESPERA_MS" in str(e.value)
    assert llamadas == []


# ------------------------------ menor 9: cuota y límite en la misma ventana

def test_cuota_y_limite_juntos_los_rechazos_de_la_ruta_gastan_cupo(client, monkeypatch):
    """El límite cuenta intentos, también los que la ruta rechaza después
    (413 de cuota): solo gastan el cupo del propio usuario."""
    monkeypatch.setenv("JAX_ADJUNTOS_SUBIDAS_POR_MINUTO", "4")
    monkeypatch.setenv("JAX_ADJUNTOS_RECHAZO_ESPERA_MS", "0")
    monkeypatch.setenv("JAX_ADJUNTOS_CUOTA_BYTES_USUARIO", str(1024 * 1024))
    mod.reiniciar()
    hdrs = cabeceras(client, "test-limite-y-cuota")
    from tests.adjuntos_muestras import PNG
    lleno = PNG + b"\x00" * (1024 * 1024 - len(PNG))
    try:
        estados = []
        r = client.post(RUTA, files={"file": ("f.png", lleno, "image/png")}, headers=hdrs)
        estados.append(r.status_code)
        for _ in range(3):
            r = client.post(RUTA, files={"file": ("n.txt", b"x", "text/plain")}, headers=hdrs)
            estados.append((r.status_code, r.json()["detail"]["code"]))
        r = client.post(RUTA, files={"file": ("n.txt", b"x", "text/plain")}, headers=hdrs)
        estados.append((r.status_code, r.json()["detail"]["code"]))
        assert estados == [200] + [(413, "adjuntos_cuota_excedida")] * 3 + [(429, "adjuntos_subidas_limite")]
        otro = cabeceras(client, "test-limite-y-cuota-otro")
        assert client.post(RUTA, files={"file": ("n.txt", b"x", "text/plain")}, headers=otro).status_code == 200
    finally:
        mod.reiniciar()


@pytest.mark.parametrize("ms,segundos", [("0", 0.0), ("250", 0.25), ("5000", 5.0)])
def test_el_401_del_middleware_espera_lo_configurado_y_no_lee_el_cuerpo(limite, monkeypatch, ms, segundos):
    """R30 (enmienda R28): flood anónimo c=25 con 10 MB, health p95 36 ms con
    el 401 inmediato contra 0,29 ms con la espera de 1000 ms."""
    monkeypatch.setenv("JAX_ADJUNTOS_RECHAZO_ESPERA_MS", ms)
    interna = _Interna()
    status, _, _, canal = _llamar(mod.LimiteDeSubidas(interna), autorizacion="Bearer basura")
    assert (status, canal.leidos, interna.llamadas) == (401, 0, 0)
    assert limite == [segundos]
