"""Validaciones compartidas (2026-09-12, administración de usuarios).

EMAIL_MAX = 254: máximo de RFC 5321, el mismo tope que ya usan LoginRequest y
ForgotPasswordRequest. No hay `email-validator` instalado (medido
2026-09-12): la regla es deliberadamente simple, una sola arroba, sin
espacios y con un punto en el dominio. Lo que decide si la dirección existe es
el servidor de correo, no esta función.
"""
import re

EMAIL_MAX = 254
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def email_valido(valor: str) -> bool:
    return bool(valor) and len(valor) <= EMAIL_MAX and _EMAIL.match(valor) is not None
