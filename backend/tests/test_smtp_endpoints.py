"""Endpoints de Admin para el correo saliente (2026-09-12, etapa 1).

Contra jax_memory_test (conftest.py). Cada test deja axioma_config sin
filas smtp.* al terminar. Ningún test sale a la red: smtplib queda
reemplazado por un doble que explota, y cada test que necesita "enviar" o
"probar" reemplaza la función de smtp_config que corresponde.
"""
import uuid

import pytest
from cryptography.fernet import Fernet

import smtp_config
from auth.jwt import create_access_token
from auth.rate_limit import SlidingWindowLimiter

CLAVE = "clave-smtp-de-prueba"


def _admin():
    # user_id=1 es el superadmin sembrado en jax_memory_test; nadie lo modifica.
    return {"Authorization": f"Bearer {create_access_token('1', '1', 'superadmin')}"}


async def _sql(q, args=(), fetch=False):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(q, args)
            return await cur.fetchall() if fetch else cur.lastrowid


async def _borrar_smtp():
    await _sql("DELETE FROM axioma_config WHERE config_key LIKE %s", ("smtp.%",))


async def _filas_smtp():
    return dict(await _sql("SELECT config_key, config_value FROM axioma_config WHERE config_key LIKE %s",
                           ("smtp.%",), True))


@pytest.fixture(autouse=True)
def entorno(client, monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())

    class _SinRed:
        def __init__(self, *a, **k):
            raise AssertionError("un test de SMTP intentó abrir una conexión real")

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", _SinRed)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _SinRed)
    import api.admin.smtp as smtp_mod
    monkeypatch.setattr(smtp_mod, "SMTP_TEST_LIMITER", SlidingWindowLimiter(100, 300, 1000))
    monkeypatch.setattr(smtp_mod, "SMTP_CONN_LIMITER", SlidingWindowLimiter(100, 300, 1000))
    client.portal.call(_borrar_smtp)
    yield
    client.portal.call(_borrar_smtp)


def _config(**cambios):
    datos = {"host": "mail.example.test", "port": 587, "encryption": "tls",
             "user": "no-reply@example.test", "password": CLAVE,
             "from_name": "Axioma", "from_email": "no-reply@example.test"}
    datos.update(cambios)
    return datos


def _guardar(client, **cambios):
    return client.put("/api/admin/smtp", json=_config(**cambios), headers=_admin())


# ----------------------------------------------------------- GET / PUT

def test_sin_configurar(client):
    r = client.get("/api/admin/smtp", headers=_admin())
    assert r.status_code == 200, r.text
    assert r.json()["configurado"] is False and r.json()["password"] == ""


def test_guardar_cifra_la_contrasena_y_el_get_solo_muestra_la_mascara(client):
    assert _guardar(client).status_code == 200
    filas = client.portal.call(_filas_smtp)
    assert filas["smtp.password"] != CLAVE
    assert smtp_config.decrypt_db_secret(filas["smtp.password"]) == CLAVE
    cuerpo = client.get("/api/admin/smtp", headers=_admin()).json()
    assert cuerpo["password"] == smtp_config.MASCARA
    assert CLAVE not in cuerpo.values() and filas["smtp.password"] not in cuerpo.values()


def test_guardar_con_la_mascara_conserva_la_contrasena(client):
    assert _guardar(client).status_code == 200
    antes = client.portal.call(_filas_smtp)["smtp.password"]
    # Cambia from_name, no el servidor: con otro host la máscara ya NO
    # conserva la contraseña (revisión final, 2026-09-13; ver abajo).
    assert _guardar(client, password=smtp_config.MASCARA, from_name="Axioma Mail").status_code == 200
    despues = client.portal.call(_filas_smtp)
    assert despues["smtp.password"] == antes and despues["smtp.from_name"] == "Axioma Mail"


def test_la_primera_vez_exige_contrasena(client):
    r = _guardar(client, password="")
    assert (r.status_code, r.json()["detail"]) == (422, "smtp_exige_contrasena")
    assert client.portal.call(_filas_smtp) == {}


def test_estado_corrupto_se_nombra_y_reconfigurar_exige_contrasena(client):
    assert _guardar(client).status_code == 200
    ajena = Fernet(Fernet.generate_key()).encrypt(b"x").decode()  # como si rotara FERNET_KEY
    client.portal.call(_sql, "UPDATE axioma_config SET config_value = %s WHERE config_key = 'smtp.password'", (ajena,))
    cuerpo = client.get("/api/admin/smtp", headers=_admin()).json()
    assert (cuerpo["corrupta"], cuerpo["motivo"], cuerpo["password"]) == (True, "password_ilegible", "")
    r = _guardar(client, password=smtp_config.MASCARA)
    assert (r.status_code, r.json()["detail"]) == (422, "smtp_exige_contrasena")
    assert _guardar(client, password="reescrita").status_code == 200
    assert client.get("/api/admin/smtp", headers=_admin()).json()["corrupta"] is False


def test_remitente_invalido(client):
    r = _guardar(client, from_email="no-es-un-correo")
    assert (r.status_code, r.json()["detail"]) == (400, "smtp_from_email_invalido")


async def _crear_operador():
    email = f"test-smtp-op-{uuid.uuid4().hex[:10]}@example.invalid"
    user_id = await _sql(
        "INSERT INTO jax_users (tenant_id, email, password_hash, role, status) "
        "VALUES (1, %s, 'sin-login', 'operator', 'active')", (email,))
    return user_id


async def _borrar_usuario(user_id):
    await _sql("DELETE FROM jax_users WHERE user_id = %s", (user_id,))


def test_solo_superadmin(client):
    # Operador REAL (no un token con rol inventado): desde la etapa 2 el rol
    # sale de la base, y este test tiene que seguir diciendo lo mismo.
    user_id = client.portal.call(_crear_operador)
    try:
        token = create_access_token(str(user_id), "1", "operator")
        r = client.get("/api/admin/smtp", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
    finally:
        client.portal.call(_borrar_usuario, user_id)


# ------------------------------------------------------ probar conexión

def test_probar_conexion_usa_la_contrasena_guardada_si_llega_la_mascara(client, monkeypatch):
    assert _guardar(client).status_code == 200
    vistos = []
    monkeypatch.setattr(smtp_config, "probar_conexion", lambda *a: vistos.append(a))
    datos = {k: v for k, v in _config(password=smtp_config.MASCARA).items() if k not in ("from_name", "from_email")}
    r = client.post("/api/admin/smtp/test-connection", json=datos, headers=_admin())
    assert r.status_code == 200, r.text
    assert vistos == [("mail.example.test", 587, "tls", "no-reply@example.test", CLAVE)]


def test_probar_conexion_devuelve_el_paso_exacto(client, monkeypatch):
    def falla(*a):
        raise smtp_config.SmtpPasoFallido("smtp_auth_rechazada", "535 5.7.8 Authentication failed")

    monkeypatch.setattr(smtp_config, "probar_conexion", falla)
    datos = {k: v for k, v in _config().items() if k not in ("from_name", "from_email")}
    r = client.post("/api/admin/smtp/test-connection", json=datos, headers=_admin())
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "smtp_auth_rechazada", "server": "535 5.7.8 Authentication failed"}


# ------------------------------------------------------- correo de prueba

def test_prueba_sin_configurar_es_503(client):
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (503, "smtp_no_configurado")


def test_prueba_con_config_corrupta_es_503(client):
    assert _guardar(client).status_code == 200
    client.portal.call(_sql, "DELETE FROM axioma_config WHERE config_key = 'smtp.from_name'")
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (503, "smtp_config_corrupta")


def test_prueba_va_al_correo_del_superadmin(client, monkeypatch):
    assert _guardar(client).status_code == 200
    enviados = []
    monkeypatch.setattr(smtp_config, "enviar", lambda s, msg: enviados.append((s.host, msg["To"])))
    (fila,) = client.portal.call(_sql, "SELECT email FROM jax_users WHERE user_id = 1", (), True)
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "to": fila[0]}
    assert enviados == [("mail.example.test", fila[0])]


def test_prueba_tiene_limite_de_intentos(client, monkeypatch):
    import api.admin.smtp as smtp_mod
    monkeypatch.setattr(smtp_mod, "SMTP_TEST_LIMITER", SlidingWindowLimiter(2, 300, 1000))
    monkeypatch.setattr(smtp_config, "enviar", lambda s, msg: None)
    assert _guardar(client).status_code == 200
    for _ in range(2):
        assert client.post("/api/admin/smtp/test", headers=_admin()).status_code == 200
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (429, "smtp_demasiadas_pruebas")
    assert int(r.headers["Retry-After"]) >= 1


def test_prueba_que_el_servidor_rechaza_es_502_con_su_respuesta(client, monkeypatch):
    import smtplib

    def rechaza(s, msg):
        raise smtplib.SMTPRecipientsRefused({"x@y": (550, b"5.1.1 User unknown")})

    monkeypatch.setattr(smtp_config, "enviar", rechaza)
    assert _guardar(client).status_code == 200
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "smtp_envio_fallido"
    assert "User unknown" in r.json()["detail"]["server"]


# -------------------------------------- /api/admin/config no ve smtp.*

def test_config_generico_no_lista_smtp(client):
    assert _guardar(client).status_code == 200
    claves = [i["key"] for i in client.get("/api/admin/config", headers=_admin()).json()["config"]]
    assert claves and not [c for c in claves if c.startswith("smtp.")]


def test_config_generico_rechaza_escribir_smtp(client):
    assert _guardar(client).status_code == 200
    antes = client.portal.call(_filas_smtp)
    r = client.put("/api/admin/config", json=[{"key": "system_name", "value": "Axioma"},
                                               {"key": "smtp.password", "value": "en-claro"}],
                   headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (400, "config_clave_reservada")
    assert client.portal.call(_filas_smtp) == antes


# -------------------- fix wave de la revisión final (2026-09-13)

CAMBIOS_DE_SERVIDOR = [("host", "atacante.example.test"), ("port", 2525),
                       ("encryption", "ssl"), ("user", "otro@example.test")]


def _conexion(**cambios):
    return {k: v for k, v in _config(**cambios).items() if k not in ("from_name", "from_email")}


@pytest.mark.parametrize("campo,valor", CAMBIOS_DE_SERVIDOR)
def test_guardar_con_mascara_y_otro_servidor_exige_reescribir_la_contrasena(client, campo, valor):
    # CRÍTICO: si no, /smtp/test mandaría la contraseña guardada al host nuevo.
    assert _guardar(client).status_code == 200
    antes = client.portal.call(_filas_smtp)
    r = _guardar(client, password=smtp_config.MASCARA, **{campo: valor})
    assert (r.status_code, r.json()["detail"]) == (422, "smtp_reescribir_contrasena_al_cambiar_servidor")
    assert client.portal.call(_filas_smtp) == antes


@pytest.mark.parametrize("password", [smtp_config.MASCARA, "", None])
@pytest.mark.parametrize("campo,valor", CAMBIOS_DE_SERVIDOR)
def test_probar_conexion_no_manda_la_guardada_a_otro_servidor(client, monkeypatch, campo, valor, password):
    assert _guardar(client).status_code == 200
    vistos = []
    monkeypatch.setattr(smtp_config, "probar_conexion", lambda *a: vistos.append(a))
    r = client.post("/api/admin/smtp/test-connection", json=_conexion(password=password, **{campo: valor}),
                    headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (422, "smtp_reescribir_contrasena_al_cambiar_servidor")
    assert vistos == []


@pytest.mark.parametrize("password", [smtp_config.MASCARA, ""])
def test_probar_conexion_sin_nada_guardado_y_sin_contrasena(client, monkeypatch, password):
    vistos = []
    monkeypatch.setattr(smtp_config, "probar_conexion", lambda *a: vistos.append(a))
    r = client.post("/api/admin/smtp/test-connection", json=_conexion(password=password), headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (422, "smtp_sin_contrasena")
    assert vistos == []


def test_contrasena_no_ascii_es_422_al_guardar_y_al_probar(client, monkeypatch):
    vistos = []
    monkeypatch.setattr(smtp_config, "probar_conexion", lambda *a: vistos.append(a))
    r = _guardar(client, password="contraseña")
    assert (r.status_code, r.json()["detail"]) == (422, "smtp_password_no_ascii")
    assert client.portal.call(_filas_smtp) == {}
    r = client.post("/api/admin/smtp/test-connection", json=_conexion(password="contraseña"), headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (422, "smtp_password_no_ascii")
    assert vistos == []


def test_prueba_con_contrasena_guardada_no_ascii_no_es_500(client, monkeypatch):
    # Defensa en profundidad para filas viejas: smtplib lanza UnicodeEncodeError.
    def no_ascii(s, msg):
        "contraseña".encode("ascii")

    monkeypatch.setattr(smtp_config, "enviar", no_ascii)
    assert _guardar(client).status_code == 200
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert r.status_code == 502
    assert r.json()["detail"] == {"code": "smtp_password_no_ascii", "server": ""}


@pytest.mark.parametrize("campo", ["host", "user", "from_name"])
@pytest.mark.parametrize("malo", ["a\r\nBcc: x@y.io", "a\tb", "a\x00b", "a\x7fb"])
def test_caracteres_de_control_son_400_al_guardar(client, campo, malo):
    r = _guardar(client, **{campo: malo})
    assert (r.status_code, r.json()["detail"]) == (400, "smtp_campo_invalido")
    assert client.portal.call(_filas_smtp) == {}


@pytest.mark.parametrize("campo", ["host", "user"])
def test_caracteres_de_control_son_400_al_probar(client, monkeypatch, campo):
    vistos = []
    monkeypatch.setattr(smtp_config, "probar_conexion", lambda *a: vistos.append(a))
    r = client.post("/api/admin/smtp/test-connection", json=_conexion(**{campo: "a\nb"}), headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (400, "smtp_campo_invalido")
    assert vistos == []


def test_prueba_con_mensaje_imposible_de_armar_no_es_500(client, monkeypatch):
    def invalido(*a):
        raise ValueError("Header values may not contain linefeed or carriage return characters")

    monkeypatch.setattr(smtp_config, "construir_mensaje", invalido)
    monkeypatch.setattr(smtp_config, "enviar", lambda s, msg: None)
    assert _guardar(client).status_code == 200
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (503, "smtp_config_corrupta")


def test_probar_conexion_tiene_limite_de_intentos(client, monkeypatch):
    import api.admin.smtp as smtp_mod
    monkeypatch.setattr(smtp_mod, "SMTP_CONN_LIMITER", SlidingWindowLimiter(2, 300, 1000))
    monkeypatch.setattr(smtp_config, "probar_conexion", lambda *a: None)
    for _ in range(2):
        r = client.post("/api/admin/smtp/test-connection", json=_conexion(), headers=_admin())
        assert r.status_code == 200, r.text
    r = client.post("/api/admin/smtp/test-connection", json=_conexion(), headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (429, "smtp_demasiadas_pruebas")
    assert int(r.headers["Retry-After"]) >= 1


@pytest.mark.parametrize("fernet_key", [None, "no-es-una-clave-fernet"])
def test_sin_clave_de_cifrado_utilizable_es_503_y_no_escribe(client, monkeypatch, fernet_key):
    if fernet_key is None:
        monkeypatch.delenv("FERNET_KEY", raising=False)
    else:
        monkeypatch.setenv("FERNET_KEY", fernet_key)
    r = _guardar(client)
    assert (r.status_code, r.json()["detail"]) == (503, "smtp_sin_clave_de_cifrado")
    assert client.portal.call(_filas_smtp) == {}


@pytest.mark.parametrize("clave", ["SMTP.password", " smtp.password", "Smtp.Host"])
def test_config_generico_rechaza_smtp_con_mayusculas_o_espacios(client, clave):
    # config_key es utf8mb4_uca1400_ai_ci: "SMTP.password" ES la fila de smtp.password.
    assert _guardar(client).status_code == 200
    antes = client.portal.call(_filas_smtp)
    r = client.put("/api/admin/config", json=[{"key": clave, "value": "en-claro"}], headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (400, "config_clave_reservada")
    assert client.portal.call(_filas_smtp) == antes


def test_config_generico_no_lista_smtp_con_mayusculas(client):
    client.portal.call(_sql, "INSERT INTO axioma_config (config_key, config_value) VALUES ('SMTP.Otra', 'x')")
    claves = [i["key"] for i in client.get("/api/admin/config", headers=_admin()).json()["config"]]
    assert claves and not [c for c in claves if c.lower().startswith("smtp.")]
