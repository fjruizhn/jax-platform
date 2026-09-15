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

# `key=`, `api_key=`, `x-goog-api-key=`, `token=`, `access_token=`... en
# query strings o en texto libre. El valor termina en &, espacio o comilla.
_PARAM_SECRETO = re.compile(
    r"(?i)\b([a-z0-9_\-]*(?:key|token))=([^&\s'\"<>]+)")

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
    texto = _PARAM_SECRETO.sub(lambda m: f"{m.group(1)}={MARCA}", texto)
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
