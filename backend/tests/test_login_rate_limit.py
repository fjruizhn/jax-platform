"""Límite de intentos de login por IP y por email (2026-09-12).

Desde que el login ya no deja enumerar cuentas (jax-platform#57), todo
intento -- exista o no el email -- cuesta un bcrypt de ~155 ms de CPU. Sin
límite (no había ninguno), cualquiera que alcance /api/auth/login podía
hacerle gastar CPU al servidor: medido en vivo, c=5 -> 32 rps.

Reglas:
  - el límite se aplica ANTES de consultar la DB y ANTES del bcrypt;
  - por IP y por email normalizado (un ataque distribuido contra una cuenta
    también se frena);
  - excedido -> 429 con Retry-After y el MISMO cuerpo para todos (no revela
    si la cuenta existe);
  - la IP del cliente sale de X-Real-IP SOLO si la conexión viene de un
    proxy de confianza (JAX_TRUSTED_PROXIES); si no, cualquiera la falsifica;
  - memoria acotada: un tope de claves, sin fuga con millones de IPs.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from auth import rate_limit
from auth.rate_limit import SlidingWindowLimiter, client_ip, parse_rate


# ---------------------------------------------------------------- puros

def test_ventana_deslizante_bloquea_y_se_libera():
    lim = SlidingWindowLimiter(max_hits=2, window_s=60, max_keys=10)
    assert lim.hit("a", now=0.0) is None
    assert lim.hit("a", now=1.0) is None
    retry = lim.hit("a", now=2.0)
    assert retry is not None and 57.0 <= retry <= 58.0, retry
    assert lim.hit("a", now=61.0) is None  # el primero salió de la ventana


def test_un_intento_bloqueado_no_extiende_el_bloqueo():
    lim = SlidingWindowLimiter(max_hits=1, window_s=10, max_keys=10)
    assert lim.hit("a", now=0.0) is None
    for t in (1.0, 5.0, 9.0):
        assert lim.hit("a", now=t) is not None
    assert lim.hit("a", now=10.5) is None


def test_claves_independientes():
    lim = SlidingWindowLimiter(max_hits=1, window_s=60, max_keys=10)
    assert lim.hit("a", now=0.0) is None
    assert lim.hit("b", now=0.0) is None


def test_memoria_acotada_con_muchas_claves():
    lim = SlidingWindowLimiter(max_hits=5, window_s=60, max_keys=100)
    for i in range(10_000):
        lim.hit(f"ip-{i}", now=float(i) / 1000)
    assert len(lim) <= 100


def test_invitation_limiters_are_independent_from_login_and_each_other(monkeypatch):
    """A bearer flood must neither consume nor evict the login buckets."""
    login_ip = SlidingWindowLimiter(1, 60, 2)
    invitation_ip = SlidingWindowLimiter(100, 60, 2)
    invitation_token = SlidingWindowLimiter(1, 300, 2)
    monkeypatch.setattr(rate_limit, "LOGIN_IP_LIMITER", login_ip)
    monkeypatch.setattr(rate_limit, "INVITATION_ACCEPT_IP_LIMITER", invitation_ip)
    monkeypatch.setattr(rate_limit, "INVITATION_ACCEPT_TOKEN_LIMITER", invitation_token)
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", frozenset())

    request = _request("198.51.100.9")
    rate_limit.check_login_rate(request, "person@example.test")
    for index in range(50):
        rate_limit.check_invitation_accept_rate(request, f"digest-{index}")
    assert len(invitation_token) <= 2
    with pytest.raises(HTTPException) as login_limited:
        rate_limit.check_login_rate(request, "other@example.test")
    assert login_limited.value.status_code == 429

    rate_limit.check_invitation_accept_rate(request, "same-token")
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", frozenset({"proxy.test"}))
    with pytest.raises(HTTPException) as invitation_limited:
        rate_limit.check_invitation_accept_rate(
            _request("proxy.test", {"X-Real-IP": "203.0.113.10"}), "same-token")
    assert invitation_limited.value.status_code == 429


def test_invitation_ip_threshold_blocks_but_distinct_bearers_share_a_nat(monkeypatch):
    monkeypatch.setattr(rate_limit, "INVITATION_ACCEPT_IP_LIMITER", SlidingWindowLimiter(10, 60, 100))
    monkeypatch.setattr(rate_limit, "INVITATION_ACCEPT_TOKEN_LIMITER", SlidingWindowLimiter(10, 300, 100))
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", frozenset())
    request = _request("198.51.100.9")
    for index in range(10):
        rate_limit.check_invitation_accept_rate(request, f"digest-{index}")
    with pytest.raises(HTTPException) as rejected:
        rate_limit.check_invitation_accept_rate(request, "digest-over-limit")
    assert rejected.value.status_code == 429
    assert int(rejected.value.headers["Retry-After"]) >= 1


def test_invitation_untrusted_proxy_cannot_evade_and_rejected_hits_do_not_extend(monkeypatch):
    ip = SlidingWindowLimiter(1, 10, 100)
    monkeypatch.setattr(rate_limit, "INVITATION_ACCEPT_IP_LIMITER", ip)
    monkeypatch.setattr(rate_limit, "INVITATION_ACCEPT_TOKEN_LIMITER", SlidingWindowLimiter(100, 300, 100))
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", frozenset())
    first = _request("198.51.100.9", {"X-Real-IP": "10.0.0.1"})
    assert ip.hit(rate_limit.client_ip(first, rate_limit.TRUSTED_PROXIES), now=0) is None
    for now in (1, 5, 9):
        assert ip.hit(rate_limit.client_ip(_request("198.51.100.9", {"X-Real-IP": f"10.0.0.{now}"}),
                                           rate_limit.TRUSTED_PROXIES), now=now) is not None
    assert ip.hit("198.51.100.9", now=10.5) is None


def test_parse_rate():
    assert parse_rate("20/60") == (20, 60.0)
    for malo in ("", "20", "0/60", "20/0", "x/y", "-1/60", "1/nan", "1/inf", "1/-inf"):
        with pytest.raises(ValueError):
            parse_rate(malo)
    for malo in ("", "0", "-1", "many"):
        with pytest.raises(ValueError):
            rate_limit.parse_max_keys(malo)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("JAX_OFFICE_INVITATION_ACCEPT_RATE_IP", "not-a-rate"),
        ("JAX_OFFICE_INVITATION_ACCEPT_RATE_TOKEN", "0/300"),
        ("JAX_OFFICE_INVITATION_ACCEPT_RATE_TOKEN", "1/nan"),
        ("JAX_OFFICE_INVITATION_ACCEPT_RATE_TOKEN", "1/inf"),
        ("JAX_OFFICE_INVITATION_ACCEPT_RATE_MAX_KEYS", "0"),
    ],
)
def test_invalid_invitation_limiter_environment_fails_module_startup(name, value):
    env = dict(os.environ)
    env[name] = value
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-c", "import auth.rate_limit"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "inválido" in result.stderr


def test_uvicorn_workers_environment_fails_module_startup():
    env = dict(os.environ)
    env["UVICORN_WORKERS"] = "2"
    env["WEB_CONCURRENCY"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-c", "import auth.rate_limit"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "UVICORN_WORKERS=2" in result.stderr


@pytest.mark.parametrize("window", [float("nan"), float("inf"), float("-inf")])
def test_limiter_constructor_rejects_non_finite_windows(window):
    with pytest.raises(ValueError):
        SlidingWindowLimiter(1, window)


def _request(peer: str, headers: dict | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "client": (peer, 1234), "headers": raw})


def test_ip_de_proxy_de_confianza_usa_x_real_ip():
    r = _request("172.16.20.11", {"X-Real-IP": "203.0.113.7"})
    assert client_ip(r, frozenset({"172.16.20.11"})) == "203.0.113.7"


def test_ip_de_peer_no_confiable_ignora_los_headers():
    r = _request("198.51.100.9", {"X-Real-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8"})
    assert client_ip(r, frozenset({"172.16.20.11"})) == "198.51.100.9"


def test_proxy_de_confianza_sin_header_usa_el_peer():
    r = _request("172.16.20.11")
    assert client_ip(r, frozenset({"172.16.20.11"})) == "172.16.20.11"


# ---------------------------------------------------------------- endpoint

@pytest.fixture
def limites_chicos(monkeypatch):
    monkeypatch.setattr(rate_limit, "LOGIN_IP_LIMITER", SlidingWindowLimiter(2, 60, 1000))
    monkeypatch.setattr(rate_limit, "LOGIN_EMAIL_LIMITER", SlidingWindowLimiter(3, 300, 1000))
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", frozenset())


def _post(client, email, headers=None):
    return client.post("/api/auth/login", json={"email": email, "password": "x-no-real"}, headers=headers or {})


def test_ip_excedida_responde_429_antes_del_bcrypt(client, limites_chicos, monkeypatch):
    llamadas = []

    async def espia(plain, hashed):
        llamadas.append(1)
        return False

    monkeypatch.setattr("api.auth.verify_password", espia)
    assert _post(client, "a@rl.invalid").status_code == 401
    assert _post(client, "b@rl.invalid").status_code == 401
    r = _post(client, "c@rl.invalid")
    assert r.status_code == 429, r.text
    assert int(r.headers["Retry-After"]) >= 1
    assert r.json()["detail"] == rate_limit.DEMASIADOS_INTENTOS
    assert len(llamadas) == 2, "el intento limitado pagó un bcrypt"


def test_el_429_no_revela_si_la_cuenta_existe(client, limites_chicos):
    _post(client, "x1@rl.invalid")
    _post(client, "x2@rl.invalid")
    a = _post(client, "no-existe@rl.invalid")
    b = _post(client, "admin@axioma-ia.io")
    assert (a.status_code, a.json()) == (b.status_code, b.json())


def test_email_limitado_aunque_cambie_la_ip(client, monkeypatch):
    monkeypatch.setattr(rate_limit, "LOGIN_IP_LIMITER", SlidingWindowLimiter(100, 60, 1000))
    monkeypatch.setattr(rate_limit, "LOGIN_EMAIL_LIMITER", SlidingWindowLimiter(2, 300, 1000))
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", frozenset({"testclient"}))
    for i in range(2):
        assert _post(client, "Victima@RL.invalid", {"X-Real-IP": f"203.0.113.{i}"}).status_code == 401
    r = _post(client, " victima@rl.invalid ", {"X-Real-IP": "203.0.113.99"})
    assert r.status_code == 429, "el email normalizado debía contar como el mismo"


def test_x_real_ip_falsificado_no_evade_el_limite(client, limites_chicos):
    """TRUSTED_PROXIES vacío: los headers del cliente no cuentan."""
    for i in range(2):
        _post(client, f"s{i}@rl.invalid", {"X-Real-IP": f"10.0.0.{i}"})
    assert _post(client, "s9@rl.invalid", {"X-Real-IP": "10.0.0.99"}).status_code == 429
