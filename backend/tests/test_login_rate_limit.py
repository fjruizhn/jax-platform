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
import pytest
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


def test_parse_rate():
    assert parse_rate("20/60") == (20, 60.0)
    for malo in ("", "20", "0/60", "20/0", "x/y", "-1/60"):
        with pytest.raises(ValueError):
            parse_rate(malo)


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
