"""Contraseñas (2026-09-12, administración de usuarios, etapa 4, spec §3.4).

Una sola regla en todo el sistema: mínimo 8 caracteres, máximo 72 bytes
(bcrypt 5 LANZA con más, antes era un 500). Mi cuenta exige la actual y
cierra las otras sesiones; el reset por admin es un enlace por correo que
falla a la vista si no hay SMTP; completar un reset corta las sesiones
viejas.
"""
import uuid

from auth.password_rules import problema_de_password
from tests.identidades import auth, sql, token_para

CLAVE = "clave-vieja-123"
NUEVA = "clave-nueva-456"


def _admin():
    return auth(token_para(1, role="superadmin"))


# ---------------------------------------------------------------- puros

def test_menos_de_8_caracteres_es_corta():
    assert problema_de_password("1234567") == "corta"
    assert problema_de_password("12345678") is None


def test_mas_de_72_bytes_es_larga_aunque_tenga_menos_caracteres():
    assert problema_de_password("ñ" * 37) == "larga"   # 37 caracteres, 74 bytes
    assert problema_de_password("ñ" * 36) is None      # 72 bytes: el máximo


def test_cuenta_caracteres_como_python_no_unidades_utf16():
    assert problema_de_password("😀" * 7) == "corta"    # 7 caracteres (28 bytes)


# ------------------------------------------------------------------ alta

def test_el_alta_aplica_la_regla_y_no_da_500(client):
    email = f"test-alta-regla-{uuid.uuid4().hex[:10]}@example.invalid"
    for password, codigo in (("corta", "password_corta"), ("x" * 73, "password_larga")):
        r = client.post("/api/admin/users", json={"email": email, "role": "viewer", "password": password},
                        headers=_admin())
        assert (r.status_code, r.json()["detail"]) == (400, codigo)
    ((cuantos,),) = client.portal.call(sql, "SELECT COUNT(*) FROM jax_users WHERE email = %s", (email,), True)
    assert cuantos == 0
