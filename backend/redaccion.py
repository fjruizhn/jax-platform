"""Redaccion de secretos en textos de error -- Task 6 S1 (2026-09-15).

Origen: Gemini recibia la key en la query (`?key=`) y httpx.HTTPStatusError
incluye la URL COMPLETA en str(e), asi que la key quedaba en claro en
facet_health_event.detail, en el journal y en la respuesta del sync de modelos.
Desde T6-2 (2026-09-15) la key viaja en la cabecera `x-goog-api-key`, que no
aparece en str(e) ni en el log de httpx: este modulo queda como DEFENSA EN
PROFUNDIDAD para cualquier secreto que igual llegue a un texto. Es el unico
lugar donde un error se convierte en texto apto para guardar, loguear o
devolver.

Puro: sin I/O, sin estado. Se aplica en el punto de escritura
(facet_health.record_facet_health) Y donde la excepcion se vuelve texto
(texto_de_error), para que un llamador nuevo no pueda saltarselo.
"""
import logging
import re
from collections.abc import Iterable

MARCA = "***"

# Nombre de secreto seguido de `=` o `:` (con o sin espacios) y su valor, en
# query strings, cabeceras, JSON o texto libre (fix round 1, review de
# 3bed155): `key=`, `api_key="…"`, `'token': '…'`, `"api_key": "…"`,
# `token = x`, `x-goog-api-key: …`, `password=`, `secret=`.
#   - El nombre empieza en un borde (lookbehind: ni letra, ni digito, ni `_`
#     ni `-`), asi `monkey=5` y `turkey=` NO se tocan.
#   - Puede llevar prefijos separados por `_`/`-` (`api_key`, `access_token`,
#     `x-goog-api-key`, `client_secret`). Perdida aceptada: `sort_key=` y
#     `cache_key=` tambien se tapan -- mejor de mas que de menos.
#   - `for key 'PRIMARY'` no tiene `=`/`:` -> intacto.
#   - Fix round 2 (re-review de 3bed155..e6f2b75): tambien `credential=`,
#     `credentials=` y el sufijo `_id` (`private_key_id: …`). El nombre
#     `key` A SECAS con `:` y sin comillas NO se tapa: `for key: PRIMARY` es
#     el texto de un error de MariaDB, no un secreto (`key=` y `"key": "…"`
#     si se tapan).
#   - El valor entre comillas conserva las comillas; sin comillas termina en
#     `&`, espacio, comilla, `,`, `;`, `<`, `>`, `}` o `]`.
_PARAM_SECRETO = re.compile(
    r"""(?ix)
    (?<![a-z0-9_\-])
    (["']?)
    ((?:[a-z0-9]+[_\-])*(?:api_?key|key|token|password|passwd|secret|credentials?)(?:[_\-]id)?)
    \1
    (\s*[=:]\s*)
    (?:"([^"]*)"|'([^']*)'|([^&\s'",;<>}\]]+))
    """)

# Esquemas de autenticacion (Bearer/Basic/Token/Digest). REGLA (fix round 2):
#   1. En contexto Authorization -- cabecera `Authorization: <esquema> <valor>`,
#      parametro `authorization=<valor>` o JSON `"authorization": "…"` -- el
#      valor se tapa SIEMPRE, con o sin esquema; el esquema queda visible.
#   2. Fuera de ese contexto, un esquema suelto solo se trata como credencial
#      si lo que sigue TIENE FORMA de credencial: 16 caracteres de token o mas
#      y al menos un digito. Asi "basic idea of it" y "the bearer of bad news"
#      quedan intactos, y "Bearer eyJhbGciOiJIUzI1NiJ9…" no.
#   3. Fix wave final (2026-09-15, paridad con jax/core/redaccion.py): el
#      valor puede ir ENTRE COMILLAS despues del esquema (`Bearer 'x'`,
#      `Bearer "x y"`); antes la clase sin comillas no lo tomaba, el esquema
#      pasaba a ser el "valor" y el secreto entre comillas quedaba en claro.
#      _tapar_auth devuelve las comillas alrededor de la marca.
_AUTH_CONTEXTO = re.compile(
    r"""(?ix)
    (?<![a-z0-9_\-])
    (["']?)(authorization)\1
    (\s*[=:]\s*)
    (["']?)
    (?:(bearer|basic|token|digest)\s+)?
    (?:"([^"]*)"|'([^']*)'|([^\s"'&,;<>}\]]+))
    """)
_ESQUEMA_SUELTO = re.compile(
    r"(?i)\b(bearer|basic|token|digest)\s+(?=[A-Za-z0-9._~+/=\-]*\d)[A-Za-z0-9._~+/=\-]{16,}")


def _tapar_auth(m: re.Match) -> str:
    comilla, nombre, separador, comilla_valor, esquema = m.group(1, 2, 3, 4, 5)
    prefijo = f"{esquema} " if esquema else ""
    if m.group(6) is not None:
        valor = f'"{MARCA}"'
    elif m.group(7) is not None:
        valor = f"'{MARCA}'"
    else:
        valor = MARCA
    return f"{comilla}{nombre}{comilla}{separador}{comilla_valor}{prefijo}{valor}"


def _tapar_param(m: re.Match) -> str:
    comilla, nombre, separador = m.group(1), m.group(2), m.group(3)
    if nombre.lower() == "key" and not comilla and ":" in separador:
        return m.group(0)          # `for key: PRIMARY` -- texto de MariaDB
    if m.group(4) is not None:
        valor = f'"{MARCA}"'
    elif m.group(5) is not None:
        valor = f"'{MARCA}'"
    else:
        valor = MARCA
    return f"{comilla}{nombre}{comilla}{separador}{valor}"

# Forma de las API keys de Google (Gemini): "AIza" + 35 caracteres. Se exige
# un minimo de 10 para no comerse palabras cortas que empiecen igual.
_KEY_GOOGLE = re.compile(r"AIza[0-9A-Za-z_\-]{10,}")


def redactar_secretos(texto: str | None, secretos: Iterable[str | None] = ()) -> str | None:
    """Reemplaza por `***` los secretos conocidos (`secretos`), los valores
    de `key=`/`api_key=`/`token=` y las keys con forma `AIza...`.

    None y "" pasan tal cual. Un texto sin secretos no cambia."""
    if not texto:
        return texto
    # Los mas largos primero: si un secreto contiene a otro, el corto no
    # deja un resto del largo sin tapar.
    for s in sorted((s for s in secretos if s), key=len, reverse=True):
        texto = texto.replace(s, MARCA)
    # Authorization primero: si _PARAM_SECRETO viera antes `Token abc`, no lo
    # reconoceria, y en `Authorization: Bearer x` taparia el esquema y dejaria x.
    texto = _AUTH_CONTEXTO.sub(_tapar_auth, texto)
    texto = _PARAM_SECRETO.sub(_tapar_param, texto)
    texto = _ESQUEMA_SUELTO.sub(lambda m: f"{m.group(1)} {MARCA}", texto)
    return _KEY_GOOGLE.sub(MARCA, texto)


def recortar_redactado(texto: str | None, limite: int,
                       secretos: Iterable[str | None] = ()) -> str | None:
    """Redacta PRIMERO y recorta despues. Al reves, un secreto que cruza el
    corte queda partido: el pedazo ya no tiene la forma que reconocen las
    reglas (ni el secreto conocido entero) y se filtra en claro. Es la forma
    de recortar un texto de error de proveedor (fix wave final, 2026-09-15:
    la misma funcion que jax/core/redaccion.py)."""
    limpio = redactar_secretos(texto, secretos)
    return limpio if limpio is None else limpio[:limite]


def texto_de_error(e: BaseException, secretos: Iterable[str | None] = ()) -> str:
    """`"<Tipo>: <mensaje>"` de una excepcion, ya redactado. Es la forma
    canonica de convertir un error de proveedor en texto."""
    return redactar_secretos(f"{type(e).__name__}: {e}", secretos)


class FiltroDeSecretos(logging.Filter):
    """Filtro de logging que redacta el mensaje ya formateado.

    httpx loguea en INFO `HTTP Request: POST <url completa>` en CADA pedido,
    exitoso o no. La key de Gemini ya no va en la URL (cabecera
    x-goog-api-key, T6-2), pero cualquier secreto que un llamador ponga en
    una query quedaria en ese log: defensa en profundidad. Hoy no llega al journal
    (uvicorn --log-level warning y sin config de root: cae en
    logging.lastResort, que es WARNING), pero bastaria con subir el nivel para
    que la key quede en claro en cada turno. Se instala en los loggers de
    httpx/httpcore (instalar_filtro_en_loggers_http), no en un handler: asi
    vale para cualquier handler que alguien agregue despues."""

    def filter(self, record: logging.LogRecord) -> bool:
        mensaje = record.getMessage()
        limpio = redactar_secretos(mensaje)
        if limpio != mensaje:
            record.msg = limpio
            record.args = None
        return True


_LOGGERS_HTTP = ("httpx", "httpcore")
_FILTRO = FiltroDeSecretos()


def instalar_filtro_en_loggers_http() -> None:
    """Idempotente: logging.Logger.addFilter no duplica la misma instancia."""
    for nombre in _LOGGERS_HTTP:
        logging.getLogger(nombre).addFilter(_FILTRO)
