"""Límite global de profundidad de anidamiento para los cuerpos JSON.

Hallazgo de la auditoría del 2026-09-16: un cuerpo JSON con anidamiento
profundo (`{"a":{"a":{...}}}`) hacía que el decodificador de la librería
estándar agotara la pila de Python — `RecursionError` — y la API respondía
**500**. Un 500 dice "me rompí": el servidor se atribuye la culpa de un
cuerpo que, en realidad, decidió no aceptar. Lo correcto es un **422**
explícito, con el límite declarado en la respuesta.

El control es GLOBAL, no un parche por endpoint: un middleware ASGI puro
delante del router mira el cuerpo de todo pedido que la API vaya a parsear
como JSON. Un límite que solo cubre los endpoints que alguien se acordó de
anotar no es un límite.

FAIL-CLOSED (Principio IX): si la profundidad no se puede determinar —el
escáner lanza cualquier cosa, el cuerpo no se puede leer— el pedido se
RECHAZA con el mismo 422. No se acepta "por las dudas".

El texto que ve la persona NO viaja acá: la convención del repo es que el
backend responde un código estable en `detail` y el frontend lo traduce
(ver frontend/src/api/errores.js y las claves de `erroresMesa` en
frontend/src/i18n/{es,en}.js). Este módulo manda
`{"detail": {"code": "json_demasiado_profundo", "limite": N}}`.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import os
import re

from starlette.responses import JSONResponse

from config_entorno import EntornoInvalido

#: Código estable que traduce el frontend. No es texto para la persona.
CODIGO = "json_demasiado_profundo"

#: Variable de entorno que fija el límite (/etc/jax/.env). Sin hardcoding:
#: el número vive en la configuración, no en el código.
VARIABLE = "JAX_JSON_MAX_DEPTH"

#: Valor por omisión, documentado. 64 niveles de anidamiento: ningún cuerpo
#: legítimo de esta API se acerca (el más anidado que manda la Mesa web ronda
#: los 6) y deja muchísimo margen antes del techo real del decodificador de
#: Python (~1000 marcos de `sys.setrecursionlimit`, y bastante menos con la
#: pila que ya consumieron Starlette y FastAPI al llegar acá).
PREDETERMINADO = 64

#: Métodos que pueden traer cuerpo. Un GET no se toca: ni se lee el canal.
METODOS_CON_CUERPO = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Las secuencias escapadas se borran primero, y SOLO si hay alguna barra en
# el cuerpo (lo normal es que no haya ninguna y esta pasada no corra). Así
# la comilla que queda siempre abre o cierra una cadena de verdad: `\"` ya
# no está.
_ESCAPES = re.compile(rb"\\.", re.DOTALL)

# Todo lo que no sea una llave, un corchete o una comilla se borra de un
# saque, en C. Así el único bucle en Python recorre unos pocos miles de
# marcas estructurales y no el cuerpo entero byte por byte. La comilla se
# conserva porque `{"a": "}}}}"}` tiene profundidad 1, no -3: lo que está
# adentro de una cadena no anida nada.
_A_BORRAR = bytes(c for c in range(256) if c not in b'{}[]"')

_ABRE = (0x7B, 0x5B)  # { [
_COMILLA = 0x22


def limite_configurado() -> int:
    """Límite vigente, del entorno o el predeterminado.

    Un valor presente pero inservible (no entero, o menor que 1) NO cae a un
    default silencioso: lanza `EntornoInvalido` y el servicio no arranca, el
    mismo criterio que `JAX_OLLAMA_URL` en config_entorno.py.
    """
    crudo = os.environ.get(VARIABLE, "").strip()
    if not crudo:
        return PREDETERMINADO
    try:
        valor = int(crudo)
    except ValueError:
        raise EntornoInvalido(
            f"{VARIABLE}={crudo!r} tiene que ser un entero positivo "
            f"(profundidad máxima de anidamiento JSON)."
        ) from None
    if valor < 1:
        raise EntornoInvalido(
            f"{VARIABLE}={crudo!r} tiene que ser un entero positivo "
            f"(profundidad máxima de anidamiento JSON)."
        )
    return valor


def profundidad(cuerpo: bytes) -> int:
    """Profundidad máxima de anidamiento del cuerpo, sin parsearlo.

    Cuenta llaves y corchetes fuera de cadenas. No valida el JSON: un cuerpo
    malformado devuelve un número igual y sigue su camino hacia FastAPI, que
    responde su 422 de siempre. Acá solo importa el anidamiento.
    """
    if b"\\" in cuerpo:
        cuerpo = _ESCAPES.sub(b"", cuerpo)
    marcas = cuerpo.translate(None, _A_BORRAR)
    maximo = 0
    actual = 0
    en_cadena = False
    for caracter in marcas:
        if caracter == _COMILLA:
            en_cadena = not en_cadena
        elif en_cadena:
            continue
        elif caracter in _ABRE:
            actual += 1
            if actual > maximo:
                maximo = actual
        else:
            actual -= 1
    return maximo


def excede(cuerpo: bytes, limite: int) -> bool:
    """¿El cuerpo pasa el límite? Con atajo barato para el caso normal.

    Esto está en el camino de TODO pedido, así que primero se hace una cota
    superior a velocidad de C: la profundidad nunca puede ser mayor que la
    cantidad total de aperturas del cuerpo, y `bytes.count` no arma objetos
    nuevos. Un cuerpo típico de la Mesa trae una decena de aperturas contra
    un límite de 64: se resuelve ahí, sin recorrerlo de verdad. El escáner
    completo —que sí arma una copia sin cadenas— corre solo cuando la cota
    no alcanza para descartar, es decir en cuerpos muy ramificados o
    realmente profundos. Números medidos en el informe de la rama.
    """
    if cuerpo.count(b"{") + cuerpo.count(b"[") <= limite:
        return False
    return profundidad(cuerpo) > limite


#: Lo único que FastAPI NUNCA parsea como JSON: los dos tipos de formulario.
#: Ahí va la subida de archivos, que además no conviene bufferear acá.
TIPOS_DE_FORMULARIO = (b"multipart/", b"application/x-www-form-urlencoded")


def _puede_ser_json(scope) -> bool:
    """¿Este cuerpo puede terminar en un `json.loads`?

    La regla es por exclusión, no por lista blanca, y a propósito: mirar
    solo `application/json` deja una puerta abierta. `api/pipelines.py` hace
    `await request.json()` a mano, y eso parsea el cuerpo **sea cual sea** el
    content-type — un `text/plain` con 3000 niveles volvería a ser un 500.
    Así que se mide todo salvo los dos tipos de formulario, que FastAPI
    resuelve con el parser de formularios y nunca con JSON. Sin content-type
    también se mide: FastAPI, sin la cabecera, asume JSON (routing.py).
    """
    for nombre, valor in scope.get("headers", ()):
        if nombre.lower() != b"content-type":
            continue
        tipo = valor.split(b";", 1)[0].strip().lower()
        return not tipo.startswith(TIPOS_DE_FORMULARIO)
    return True  # sin content-type: FastAPI lo trata como JSON


class LimiteDeProfundidadJSON:
    """Middleware ASGI puro que corta los cuerpos demasiado anidados.

    ASGI puro y no `BaseHTTPMiddleware` a propósito: hay que leer el cuerpo
    del canal `receive` y volver a ofrecerlo intacto al router, y eso con
    BaseHTTPMiddleware obliga a rearmar el Request.

    Se registra ANTES que el CORSMiddleware en main.py, o sea que queda por
    DENTRO de él (Starlette pone el último agregado como el más externo): el
    422 sale con las cabeceras CORS y el navegador lo puede leer.
    """

    def __init__(self, app, limite: int | None = None):
        self.app = app
        # Se resuelve UNA vez, al construir la app: es el camino de todo
        # pedido, no se relee el entorno por request.
        self.limite = limite_configurado() if limite is None else limite

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method", "").upper() not in METODOS_CON_CUERPO
            or not _puede_ser_json(scope)
        ):
            await self.app(scope, receive, send)
            return

        cuerpo = bytearray()
        mensajes = []
        while True:
            mensaje = await receive()
            mensajes.append(mensaje)
            if mensaje["type"] != "http.request":
                break  # http.disconnect: el cliente se fue
            cuerpo += mensaje.get("body", b"")
            if not mensaje.get("more_body", False):
                break

        try:
            rechazar = excede(bytes(cuerpo), self.limite)
        except Exception:  # fail-soft: no relanza a proposito, pero NO afloja nada -- cae CERRADO: si la profundidad no se puede determinar, el cuerpo se rechaza con el mismo 422 (Principio IX). No hay rama que acepte un cuerpo sin medir.
            rechazar = True

        if rechazar:
            respuesta = JSONResponse(
                status_code=422,
                content={"detail": {"code": CODIGO, "limite": self.limite}},
            )
            await respuesta(scope, receive, send)
            return

        pendientes = iter(mensajes)

        async def repetir():
            for mensaje in pendientes:
                return mensaje
            return await receive()

        await self.app(scope, repetir, send)
