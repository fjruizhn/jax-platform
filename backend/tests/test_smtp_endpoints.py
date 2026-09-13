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
    assert _guardar(client, password=smtp_config.MASCARA, host="otro.example.test").status_code == 200
    despues = client.portal.call(_filas_smtp)
    assert despues["smtp.password"] == antes and despues["smtp.host"] == "otro.example.test"


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
