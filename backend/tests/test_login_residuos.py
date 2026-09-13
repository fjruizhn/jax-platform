"""Residuos del login (2026-09-12, "termina el login").

El límite de intentos (jax-platform#59) y el cierre de la enumeración de
cuentas (#57) dejaron abiertos caminos que ninguno de los dos cubría. Medidos
antes de escribir esto:

  - bcrypt 5.0.0 LANZA ValueError con contraseñas de más de 72 bytes (antes
    las truncaba): login y reset-password respondían 500, y una cuenta cuya
    contraseña larga se fijó con el bcrypt viejo ya no podía entrar;
  - forgot-password dejaba saber qué cuentas existen: con una real hacía
    DELETE + INSERT + SMTP síncrono (hasta 10 s, bloqueando el event loop),
    con una inexistente respondía al instante. Tampoco tenía límite: se podía
    inundar de correos a una víctima;
  - `email` sin largo máximo y nginx acepta 50 MB: cada email distinto queda
    como clave del limitador hasta que el LRU la expulsa;
  - el limitador vive en memoria de UN proceso, y uvicorn toma `--workers` de
    WEB_CONCURRENCY: una línea en /etc/jax/.env multiplicaba el límite.

Los tests con `client` corren contra jax_memory_test (conftest.py); cada uno
crea su usuario con un email único y lo borra al terminar.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

import bcrypt
import pytest
from pydantic import ValidationError

from auth import rate_limit
from auth.rate_limit import SlidingWindowLimiter

GENERICO = (401, "Usuario o contraseña incorrectos")
MENSAJE_RECUPERACION = "Si el correo existe, recibirás las instrucciones."


# ---------------------------------------------------------------- puros

def test_email_de_login_mas_largo_que_el_maximo_rfc_se_rechaza():
    from auth.models import LoginRequest
    with pytest.raises(ValidationError):
        LoginRequest(email="a" * 250 + "@x.io", password="p")  # 255 > 254
    LoginRequest(email="a" * 249 + "@x.io", password="p")  # 254: el máximo


def test_email_de_recuperacion_mas_largo_que_el_maximo_rfc_se_rechaza():
    from api.auth import ForgotPasswordRequest
    with pytest.raises(ValidationError):
        ForgotPasswordRequest(email="a" * 300)


def test_verify_password_trunca_a_72_bytes_como_el_bcrypt_viejo():
    # Un hash hecho con el bcrypt viejo a partir de una contraseña larga es,
    # en los hechos, el hash de sus primeros 72 bytes.
    from db.seed import verify_password
    largo = "ñ" * 50  # 100 bytes
    legado = bcrypt.hashpw(largo.encode()[:72], bcrypt.gensalt(rounds=4)).decode()
    assert asyncio.run(verify_password(largo, legado)) is True
    assert asyncio.run(verify_password("otra-cosa", legado)) is False


def test_el_limitador_exige_un_solo_proceso():
    exigir = rate_limit.exigir_un_solo_proceso
    exigir({}, ["uvicorn", "main:app"])
    exigir({"WEB_CONCURRENCY": "1"}, ["uvicorn", "main:app", "--workers", "1"])
    for env, argv in (
        ({"WEB_CONCURRENCY": "2"}, ["uvicorn", "main:app"]),
        ({}, ["uvicorn", "main:app", "--workers", "3"]),
        ({}, ["uvicorn", "main:app", "--workers=2"]),
    ):
        with pytest.raises(RuntimeError):
            exigir(env, argv)


# ---------------------------------------------------------------- DB

def _email():
    return f"test-residuo-{uuid.uuid4().hex[:12]}@example.invalid"


async def _sql(q, args=(), fetch=False):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(q, args)
            filas = await cur.fetchall() if fetch else None
        await conn.commit()
    return filas


async def _crear_con_hash(email, password_hash):
    await _sql(
        "INSERT INTO jax_users (tenant_id, email, password_hash, role, status) "
        "VALUES (1, %s, %s, 'operator', 'active')", (email, password_hash))
    filas = await _sql("SELECT user_id FROM jax_users WHERE email = %s", (email,), fetch=True)
    return filas[0][0]


async def _borrar(email):
    await _sql("DELETE FROM password_reset_tokens WHERE user_id IN "
               "(SELECT user_id FROM jax_users WHERE email = %s)", (email,))
    await _sql("DELETE FROM jax_users WHERE email = %s", (email,))


@pytest.fixture
def limites_chicos(monkeypatch):
    monkeypatch.setattr(rate_limit, "LOGIN_IP_LIMITER", SlidingWindowLimiter(2, 60, 1000))
    monkeypatch.setattr(rate_limit, "LOGIN_EMAIL_LIMITER", SlidingWindowLimiter(3, 300, 1000))
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", frozenset())


def test_login_con_password_de_mas_de_72_bytes_no_da_500(client):
    r = client.post("/api/auth/login", json={"email": _email(), "password": "x" * 100})
    assert (r.status_code, r.json()["detail"]) == GENERICO


def test_cuenta_con_password_larga_del_bcrypt_viejo_sigue_entrando(client):
    email, largo = _email(), "z" * 100
    legado = bcrypt.hashpw(largo.encode()[:72], bcrypt.gensalt()).decode()
    client.portal.call(_crear_con_hash, email, legado)
    try:
        r = client.post("/api/auth/login", json={"email": email, "password": largo})
        assert r.status_code == 200, r.text
    finally:
        client.portal.call(_borrar, email)


def test_forgot_password_no_toca_la_db_ni_el_correo_dentro_del_request(client, monkeypatch):
    # Si el request no consulta nada, no puede tardar distinto según exista
    # la cuenta: la búsqueda, el token y el correo van en segundo plano.
    procesadas = []

    async def espia(email, ip):
        procesadas.append((email, ip))

    def prohibido(*a, **k):
        raise AssertionError("forgot-password consultó la DB dentro del request")

    monkeypatch.setattr("api.auth._procesar_recuperacion", espia)
    monkeypatch.setattr("api.auth.get_pool", prohibido)
    r = client.post("/api/auth/forgot-password", json={"email": " alguien@example.invalid "})
    assert r.status_code == 200, r.text
    assert r.json()["message"] == MENSAJE_RECUPERACION
    assert [e for e, _ in procesadas] == ["alguien@example.invalid"]


def test_forgot_password_tiene_limite_de_intentos(client, limites_chicos, monkeypatch):
    async def nada(email, ip):
        pass

    monkeypatch.setattr("api.auth._procesar_recuperacion", nada)
    for i in range(2):
        assert client.post("/api/auth/forgot-password", json={"email": f"f{i}@example.invalid"}).status_code == 200
    r = client.post("/api/auth/forgot-password", json={"email": "f9@example.invalid"})
    assert r.status_code == 429, r.text
    assert int(r.headers["Retry-After"]) >= 1


def test_recuperacion_de_cuenta_real_crea_token_y_manda_el_correo(client, monkeypatch):
    from api import auth as auth_mod
    enviados = []
    monkeypatch.setattr(auth_mod, "_send_reset_email", lambda to, link: enviados.append((to, link)))
    email = _email()
    user_id = client.portal.call(_crear_con_hash, email, bcrypt.hashpw(b"clave-x", bcrypt.gensalt(rounds=4)).decode())
    try:
        client.portal.call(auth_mod._procesar_recuperacion, email, "203.0.113.5")
        filas = client.portal.call(
            _sql, "SELECT token, ip_address FROM password_reset_tokens WHERE user_id = %s AND used = FALSE",
            (user_id,), True)
        assert len(filas) == 1 and filas[0][1] == "203.0.113.5", filas
        assert len(enviados) == 1 and enviados[0][0] == email
        assert filas[0][0] in enviados[0][1], "el enlace no lleva el token guardado"
    finally:
        client.portal.call(_borrar, email)


def test_recuperacion_de_email_inexistente_no_manda_nada(client, monkeypatch):
    from api import auth as auth_mod
    enviados = []
    monkeypatch.setattr(auth_mod, "_send_reset_email", lambda to, link: enviados.append(to))
    client.portal.call(auth_mod._procesar_recuperacion, _email(), "203.0.113.5")
    assert enviados == []


def test_reset_con_password_de_mas_de_72_bytes_da_400_y_no_500(client):
    r = client.post("/api/auth/reset-password", json={"token": str(uuid.uuid4()), "password": "x" * 100})
    assert (r.status_code, r.json()["detail"]) == (400, "reset_password_larga")


def test_reset_devuelve_codigos_estables_que_el_frontend_traduce(client):
    def reset(token, password="clave-nueva-larga"):
        r = client.post("/api/auth/reset-password", json={"token": token, "password": password})
        return r.status_code, r.json()["detail"]

    assert reset(str(uuid.uuid4()), "corta") == (400, "reset_password_corta")
    assert reset(str(uuid.uuid4())) == (400, "reset_token_invalido")

    email = _email()
    user_id = client.portal.call(_crear_con_hash, email, bcrypt.hashpw(b"clave-x", bcrypt.gensalt(rounds=4)).decode())
    try:
        usado, vencido = str(uuid.uuid4()), str(uuid.uuid4())
        client.portal.call(
            _sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, used, ip_address) "
                  "VALUES (%s, %s, %s, TRUE, 'test'), (%s, %s, %s, FALSE, 'test')",
            (user_id, usado, datetime.utcnow() + timedelta(hours=1),
             user_id, vencido, datetime.utcnow() - timedelta(hours=1)))
        assert reset(usado) == (400, "reset_token_usado")
        assert reset(vencido) == (400, "reset_token_expirado")
    finally:
        client.portal.call(_borrar, email)
