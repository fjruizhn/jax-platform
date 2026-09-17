"""session_timeout_min manda (spec 2026-09-16 §C): vida ABSOLUTA del refresh
desde que se emitió -- el refresh no se rota (api/auth.py). Se aplica al
emitir (exp y Max-Age de la cookie) y al renovar, contra `iat`: acortar el
ajuste alcanza a las sesiones ya abiertas. El access sigue en 15 min."""
import re
import time

import pytest
from jose import jwt as jose_jwt

from api.auth import sesion_vencida
from auth.jwt import ALGORITHM, SECRET, create_refresh_token, decode_token
from tests.identidades import auth, sql, token_para

CLAVE = "clave-ajuste-sesion-1"


# ------------------------------------------------------------------ puros

def test_el_refresh_lleva_iat_y_vence_segun_la_vida_pedida():
    antes = int(time.time())
    p = decode_token(create_refresh_token("5", "1", "operator", 2, vida_segundos=3600))
    assert antes <= p["iat"] <= int(time.time())
    assert (p["exp"] - p["iat"], p["type"], p["tv"]) == (3600, "refresh", 2)


def test_el_refresh_no_se_emite_sin_decir_cuanto_vive():
    with pytest.raises(TypeError):
        create_refresh_token("5", "1", "operator", 2)


def test_sesion_vencida_cuenta_desde_iat():
    assert sesion_vencida({"iat": 1000}, 3600, 1000 + 3600) is False
    assert sesion_vencida({"iat": 1000}, 3600, 1000 + 3601) is True
    assert sesion_vencida({}, 3600, 1000) is True  # emitido antes del ajuste: no hay con qué medirlo
    assert sesion_vencida({"iat": "1000"}, 3600, 1000) is True
    assert sesion_vencida({"iat": True}, 3600, 1000) is True


# ------------------------------------------------------------------ con DB

def _max_age(respuesta):
    m = re.search(r"refresh_token=[^;]*;.*?Max-Age=(\d+)", respuesta.headers.get("set-cookie", ""))
    return int(m.group(1)) if m else None


def _refresh(client, token):
    try:
        return client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={token}"})
    finally:
        client.cookies.clear()


def _refresh_emitido_hace(user_id, segundos, tv=0):
    iat = int(time.time()) - segundos
    return jose_jwt.encode(
        {"user_id": str(user_id), "tenant_id": "1", "role": "operator", "tv": tv,
         "iat": iat, "exp": iat + 7 * 24 * 3600, "type": "refresh"},
        SECRET, algorithm=ALGORITHM)


def test_el_login_emite_la_cookie_con_la_vida_del_ajuste(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "60"})
    _, email = usuarios(password=CLAVE)
    try:
        r = client.post("/api/auth/login", json={"email": email, "password": CLAVE})
        cookie = r.cookies.get("refresh_token")
    finally:
        client.cookies.clear()
    assert r.status_code == 200, r.text
    assert _max_age(r) == 3600
    p = decode_token(cookie)
    assert p["exp"] - p["iat"] == 3600


def test_acortar_la_sesion_alcanza_a_las_que_ya_estan_abiertas(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "60"})
    u, _ = usuarios()
    r = _refresh(client, _refresh_emitido_hace(u, 2 * 3600))
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_expirada")


def test_dentro_de_la_vida_el_refresh_renueva(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "60"})
    u, _ = usuarios()
    assert _refresh(client, _refresh_emitido_hace(u, 30 * 60)).status_code == 200


def test_un_refresh_sin_iat_ya_no_renueva(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    u, _ = usuarios()
    viejo = jose_jwt.encode(
        {"user_id": str(u), "tenant_id": "1", "role": "operator", "tv": 0,
         "exp": int(time.time()) + 3600, "type": "refresh"},
        SECRET, algorithm=ALGORITHM)
    r = _refresh(client, viejo)
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_expirada")


def test_con_el_ajuste_ilegible_el_login_responde_503_y_no_mata_la_sesion_vieja(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "abc"})
    u, email = usuarios(password=CLAVE, token_version=4)
    try:
        r = client.post("/api/auth/login", json={"email": email, "password": CLAVE})
    finally:
        client.cookies.clear()
    assert (r.status_code, r.json()) == (503, {"detail": {"code": "ajuste_ilegible", "clave": "session_timeout_min"}})
    assert client.portal.call(sql, "SELECT token_version FROM jax_users WHERE user_id = %s", (u,), True) == ((4,),)


def test_cambiar_la_contrasena_emite_la_cookie_con_la_vida_del_ajuste(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "90"})
    u, _ = usuarios(password=CLAVE)
    try:
        r = client.post("/api/auth/me/password",
                        json={"current_password": CLAVE, "new_password": "Otra-clave-bien-larga-2026"},
                        headers=auth(token_para(u)))
    finally:
        client.cookies.clear()
    assert r.status_code == 200, r.text
    assert _max_age(r) == 5400


# Guardas (revisión final 2026-09-17): los otros dos lectores de la vida de la
# sesión también responden 503 con el ajuste ilegible y no tocan nada.
def test_con_el_ajuste_ilegible_el_refresh_responde_503(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "abc"})
    u, _ = usuarios()
    r = _refresh(client, _refresh_emitido_hace(u, 60))
    assert (r.status_code, r.json()) == (503, {"detail": {"code": "ajuste_ilegible", "clave": "session_timeout_min"}})


def test_con_el_ajuste_ilegible_cambiar_la_contrasena_responde_503_y_no_cambia_nada(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "abc"})
    u, _ = usuarios(password=CLAVE, token_version=4)
    consulta = "SELECT token_version, password_hash FROM jax_users WHERE user_id = %s"
    antes = client.portal.call(sql, consulta, (u,), True)
    try:
        r = client.post("/api/auth/me/password",
                        json={"current_password": CLAVE, "new_password": "Otra-clave-bien-larga-2026"},
                        headers=auth(token_para(u, tv=4)))
    finally:
        client.cookies.clear()
    assert (r.status_code, r.json()) == (503, {"detail": {"code": "ajuste_ilegible", "clave": "session_timeout_min"}})
    assert antes[0][0] == 4
    assert client.portal.call(sql, consulta, (u,), True) == antes
