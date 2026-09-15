"""Redaccion de secretos en textos de error -- Task 6 S1 (2026-09-15).

Gemini recibe la key en la query (`?key=`), y httpx.HTTPStatusError incluye
la URL COMPLETA en str(e). Sin redactar, la key quedaba en claro en
facet_health_event.detail (chat y sonda), en el journal y en la respuesta del
sync de modelos. Este modulo es el unico lugar donde un error se convierte en
texto apto para guardar, loguear o devolver.

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
#   - El valor entre comillas conserva las comillas; sin comillas termina en
#     `&`, espacio, comilla, `,`, `;`, `<`, `>`, `}` o `]`.
_PARAM_SECRETO = re.compile(
    r"""(?ix)
    (?<![a-z0-9_\-])
    (["']?)
    ((?:[a-z0-9]+[_\-])*(?:api_?key|key|token|password|passwd|secret))
    \1
    (\s*[=:]\s*)
    (?:"([^"]*)"|'([^']*)'|([^&\s'",;<>}\]]+))
    """)

# `Authorization: Bearer x` / `Basic x`: el esquema queda, el valor no.
_ESQUEMA_AUTH = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]+")


def _tapar_param(m: re.Match) -> str:
    comilla, nombre, separador = m.group(1), m.group(2), m.group(3)
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
    texto = _PARAM_SECRETO.sub(_tapar_param, texto)
    texto = _ESQUEMA_AUTH.sub(lambda m: f"{m.group(1)} {MARCA}", texto)
    return _KEY_GOOGLE.sub(MARCA, texto)


def texto_de_error(e: BaseException, secretos: Iterable[str | None] = ()) -> str:
    """`"<Tipo>: <mensaje>"` de una excepcion, ya redactado. Es la forma
    canonica de convertir un error de proveedor en texto."""
    return redactar_secretos(f"{type(e).__name__}: {e}", secretos)


class FiltroDeSecretos(logging.Filter):
    """Filtro de logging que redacta el mensaje ya formateado.

    httpx loguea en INFO `HTTP Request: POST <url completa>` en CADA pedido,
    exitoso o no -- con Gemini, la URL lleva `?key=`. Hoy no llega al journal
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
