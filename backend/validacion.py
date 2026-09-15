"""Validaciones compartidas (2026-09-12, administración de usuarios).

EMAIL_MAX = 254: máximo de RFC 5321, el mismo número que LoginRequest y
ForgotPasswordRequest ponen en su propio Field(max_length=254). Esos dos NO
usan este módulo: validan con Pydantic.

- direccion_unica_valida: la usa SMTP (destinatario de la prueba y
  from_email), porque esas direcciones van a un encabezado (To, From).
- email_valido: la regla base sobre la que se arma direccion_unica_valida.
  Hoy no tiene otro llamador en producción; se conserva para la etapa 5 del
  plan de administración de usuarios (validar el email de un usuario:
  docs/superpowers/plans/2026-09-12-admin-usuarios-etapa-5-editar-baja.md).

No hay `email-validator` instalado (medido 2026-09-12): la regla es
deliberadamente simple, una sola arroba, sin espacios y con un punto en el
dominio. Lo que decide si la dirección existe es el servidor de correo, no
esta función.
"""
import re
from email.utils import getaddresses

EMAIL_MAX = 254
# Etiquetas del dominio: solo letras ASCII, dígitos y guion (Ruling U32,
# etapa 5, Task 3 fix ronda 1, 2026-09-15). Antes admitían cualquier cosa
# salvo @, espacio y punto: "ana@x.io#baja-42-20260912" -- el correo que la
# baja le pone a un usuario -- pasaba, y un superadmin podía dárselo a OTRO
# usuario por el alta o el PUT; la baja real de aquel chocaba después con el
# UNIQUE de email (500). Un dominio internacionalizado se escribe en su forma
# ASCII (punycode, xn--...).
_EMAIL = re.compile(r"^[^@\s]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def email_valido(valor: str) -> bool:
    return bool(valor) and len(valor) <= EMAIL_MAX and _EMAIL.match(valor) is not None


# Caracteres con significado en un encabezado de direcciones (RFC 5322):
# separadores de lista, nombre visible, comentarios, comillas y escape.
_ESPECIALES_DE_ENCABEZADO = frozenset(',;<>()[]"\\')


def direccion_unica_valida(valor: str) -> bool:
    """email_valido y además UNA sola dirección al ponerla en un encabezado
    (To, From). email_valido deja pasar , ; < > " ( ): "postmaster,a@b.io"
    se volvía dos destinatarios y "x;y@b.io" se truncaba a "x" (revisión,
    2026-09-13). Es la que usa SMTP: destinatario de la prueba y from_email.
    email_valido es la regla base (la usa el email de un usuario, etapa 5, que
    no va a un encabezado). Desde U32 su dominio solo admite [A-Za-z0-9-], así
    que las rarezas EN EL DOMINIO ("<a@b.io>", "a@b.io,") ya caen ahí; las de
    la parte local (, ; < " ( ) \\ [ ]) siguen siendo trabajo de esta."""
    return (email_valido(valor)
            and not _ESPECIALES_DE_ENCABEZADO.intersection(valor)
            and getaddresses([valor]) == [("", valor)])


# Caracteres de control C0 (\x00-\x1f, incluye \t \r \n) y DEL. En un
# encabezado de correo o una línea SMTP, un salto de línea es una inyección.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def tiene_caracteres_de_control(valor: str) -> bool:
    return _CONTROL.search(valor) is not None
