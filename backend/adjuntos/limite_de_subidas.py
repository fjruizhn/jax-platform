"""Límite de subidas de adjuntos por usuario (RD7, decisión del principal
2026-09-17).

POR QUÉ. La cuota (RD6) acota lo GUARDADO, no lo RECIBIDO: un usuario sobre
su cuota seguía mandando cuerpos de 10 MB que Starlette volcaba a TMPDIR
antes del 413 (medido en RD6: 23-25 volcados en vuelo, hasta 166 MB, ~130
subidas rechazadas/s y health p95 de ~1 a ~2,7 ms).

DÓNDE CORRE, Y POR QUÉ NO ES UN Depends. FastAPI 0.139
(fastapi/routing.py::get_request_handler) hace `await request.form()` --
python-multipart parsea el cuerpo entero y Starlette vuelca cada archivo a
un SpooledTemporaryFile -- y RECIÉN DESPUÉS `solve_dependencies`. Un Depends
(o el propio get_current_user) llega con el cuerpo ya leído; lo fija
tests/test_adjuntos_limite_de_subidas.py::test_fastapi_lee_el_multipart_entero_antes_de_resolver_las_dependencias.
Por eso es un middleware ASGI puro, montado DENTRO de CORSMiddleware (para
que el 429 lleve sus cabeceras) y antes del router: para POST
/api/chat/upload lee la cabecera Authorization, verifica la firma y el
vencimiento del JWT (auth.jwt.decode_token: HS256, sin base) y, si el
usuario ya gastó su cupo, espera JAX_ADJUNTOS_RECHAZO_ESPERA_MS y responde 429
SIN llamar a receive(): el cuerpo no se parsea con python-multipart ni se
vuelca (medido en RD7: 0 volcados en TMPDIR durante el flood). Lo que el
cliente igual manda lo lee y descarta uvicorn después del 429 para mantener
la conexión: ese es el costo que acota la espera (ver ESPERA_DE_RECHAZO_MS_*).

IDENTIDAD (Ruling R28). Sin token de ACCESO válido -- sin cabecera, esquema
que no es Bearer, firma inválida, vencido, de refresh, user_id o tv que no
son enteros -- responde, tras la misma espera JAX_ADJUNTOS_RECHAZO_ESPERA_MS que el 429 (R30), el MISMO 401 que la dependencia de la
ruta (mismo status, cuerpo y cabeceras; reusa `auth.middleware.bearer`,
`decode_token` y `validar_payload`), sin leer el cuerpo y sin gastar cupo.
Con firma válida no mira la base, a propósito: pasa a la ruta, cuya
autenticación con base rechaza un token revocado. RIESGO ACEPTADO por el
principal: un token revocado pero no vencido (<= 15 min) solo gasta el cupo
de SU dueño en el middleware. El límite cuenta intentos, también los que la
ruta rechaza después (401 por revocación, 413, 415, 422): solo gastan el cupo
del que llama.

CUÁNTO. JAX_ADJUNTOS_SUBIDAS_POR_MINUTO (1..600, sin default: fail-closed),
ventana deslizante de 60 s (auth.rate_limit.SlidingWindowLimiter, la misma
clase del login y del SMTP). Un intento rechazado no se cuenta. Retry-After =
segundos hasta que se libera un lugar, redondeado arriba, mínimo 1.

ESTADO. En memoria del proceso: jax-platform es UN proceso
(auth/rate_limit.py::exigir_un_solo_proceso). Tope de claves: el de login
(20.000, LRU). El limitador se rehace si cambia el valor configurado (el
entorno de producción no cambia en caliente; los tests sí lo cambian).
"""
import asyncio
import json
import math
import os
import re
import time

from fastapi import HTTPException
from fastapi.exception_handlers import http_exception_handler
from starlette.requests import Request

from adjuntos.errores import AdjuntoRechazado
from adjuntos.limites import LimitesDeAdjuntosInvalidos
from auth.rate_limit import SlidingWindowLimiter

VARIABLE = "JAX_ADJUNTOS_SUBIDAS_POR_MINUTO"
RUTA = "/api/chat/upload"
CODIGO = AdjuntoRechazado(429, "adjuntos_subidas_limite").detail["code"]  # falla al importar si no está en CODIGOS

# 1..600 por minuto. Piso 1: 0 sería "no se puede adjuntar", eso no es un
# límite sino apagar la función. Techo 600 (10/s): medido en RD5/RD6, una
# subida de imagen de 10 MB tarda p50 ~0,23-0,27 s de punta a punta en
# staging; un cliente sin freno hace ~4/s. Por encima de 10/s el límite ya no
# frena nada que el servidor no frene solo.
SUBIDAS_POR_MINUTO_MIN = 1
SUBIDAS_POR_MINUTO_MAX = 600
VENTANA_SEGUNDOS = 60
MAX_CLAVES = 20_000

_ENTERO = re.compile(r"[1-9][0-9]{0,2}")
_limitador: SlidingWindowLimiter | None = None
# Espera antes de un rechazo del middleware, 429 y 401 (RD7; RD7 fix round: sale del código a
# JAX_ADJUNTOS_RECHAZO_ESPERA_MS por decisión del principal, deploy 1000). Medido
# con upload_imagen_max c=25 de un usuario, 10 MB, en staging: sin espera el
# cliente reintenta al instante y uvicorn lee y descarta ~540 cuerpos/s en el
# event loop después de cada 429 (keep-alive) -> health p95 37-40 ms. Con
# `Connection: close`, 6,2 ms pero el 19 % de las respuestas llegó como reset.
# 250 ms: 97 rechazos/s, health p95 0,37 ms; 1000 ms: 25 rechazos/s, 0,30 ms,
# 0 resets. Mientras espera no se llama a receive(): uvicorn lee hasta 64 KB y
# pausa la lectura, y TCP frena al cliente.
#
# Rango 0..5000 ms. 0 SÍ está permitido: apagar la espera es una decisión
# válida si nginx (limit_req) ya frena antes, y su costo está medido arriba.
# Techo 5 s: cada rechazo en espera retiene un socket y una corrutina; más
# largo no frena más a un cliente que reintenta (Retry-After ya lo dice) y
# acerca la espera a los timeouts de clientes y proxies.
VARIABLE_ESPERA = "JAX_ADJUNTOS_RECHAZO_ESPERA_MS"
ESPERA_DE_RECHAZO_MS_MIN = 0
ESPERA_DE_RECHAZO_MS_MAX = 5000
_ENTERO_MS = re.compile(r"0|[1-9][0-9]{0,3}")
# Referencia propia para que un test la sustituya sin tocar asyncio.sleep global.
_dormir = asyncio.sleep


def cargar_subidas_por_minuto() -> int:
    crudo = os.environ.get(VARIABLE)
    valor = int(crudo) if crudo is not None and _ENTERO.fullmatch(crudo) else None
    if valor is None or not SUBIDAS_POR_MINUTO_MIN <= valor <= SUBIDAS_POR_MINUTO_MAX:
        raise LimitesDeAdjuntosInvalidos(
            f"límite de subidas de adjuntos sin configurar o fuera de rango (entero "
            f"{SUBIDAS_POR_MINUTO_MIN}..{SUBIDAS_POR_MINUTO_MAX}, solo dígitos, en /etc/jax/.env): "
            f"{VARIABLE}={crudo!r}")
    return valor


def cargar_espera_de_rechazo_ms() -> int:
    crudo = os.environ.get(VARIABLE_ESPERA)
    valor = int(crudo) if crudo is not None and _ENTERO_MS.fullmatch(crudo) else None
    if valor is None or not ESPERA_DE_RECHAZO_MS_MIN <= valor <= ESPERA_DE_RECHAZO_MS_MAX:
        raise LimitesDeAdjuntosInvalidos(
            f"espera antes de un rechazo de subidas (401/429) sin configurar o fuera de rango (entero "
            f"{ESPERA_DE_RECHAZO_MS_MIN}..{ESPERA_DE_RECHAZO_MS_MAX} ms, solo dígitos, en /etc/jax/.env): "
            f"{VARIABLE_ESPERA}={crudo!r}")
    return valor


def _limitador_vigente() -> SlidingWindowLimiter:
    """Invalidación: el valor configurado. Se lee en cada subida (un
    os.environ.get); si difiere del del limitador, se rehace vacío."""
    global _limitador
    maximo = cargar_subidas_por_minuto()
    if _limitador is None or _limitador.max_hits != maximo:
        _limitador = SlidingWindowLimiter(maximo, VENTANA_SEGUNDOS, max_keys=MAX_CLAVES)
    return _limitador


def reiniciar() -> None:
    global _limitador
    _limitador = None


def _usuario_del_token(scope) -> str:
    """Ruling R28: la MISMA cadena que la dependencia de la ruta, sin la base
    y sin leer el cuerpo. Lanza la misma HTTPException que la ruta:
    - sin cabecera, esquema que no es Bearer o sin credenciales: el 401 de
      `auth.middleware.bearer` (HTTPBearer, con WWW-Authenticate: Bearer);
    - firma inválida o vencido: el de `auth.jwt.decode_token`;
    - token que no es de acceso, o user_id/tv que no son enteros: el de
      `auth.middleware.validar_payload`.
    Devuelve el user_id normalizado (str(int)) como clave del limitador."""
    from fastapi.security.utils import get_authorization_scheme_param
    from starlette.datastructures import Headers

    from auth.jwt import decode_token
    from auth.middleware import bearer, validar_payload

    autorizacion = Headers(scope=scope).get("Authorization")
    esquema, credenciales = get_authorization_scheme_param(autorizacion)
    if not (autorizacion and esquema and credenciales) or esquema.lower() != "bearer":
        raise bearer.make_not_authenticated_error()
    user_id, _ = validar_payload(decode_token(credenciales), "access")
    return str(user_id)


class LimiteDeSubidas:
    """Middleware ASGI. Ver el docstring del módulo."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != RUTA:
            await self.app(scope, receive, send)
            return
        try:
            user_id = _usuario_del_token(scope)
        except HTTPException as e:
            # R28: el mismo 401 que daría la ruta (mismo manejador de FastAPI),
            # sin leer el cuerpo y sin gastar cupo de nadie. R30 (enmienda
            # R28): con la MISMA espera que el 429. Flood anónimo c=25 con
            # 10 MB: 401 inmediato -> health p95 36 ms (uvicorn descarta
            # ~560 cuerpos/s en el loop); con 1000 ms -> 0,29 ms.
            await _dormir(cargar_espera_de_rechazo_ms() / 1000)
            respuesta = await http_exception_handler(Request(scope), e)
            await respuesta(scope, receive, send)
            return
        espera = _limitador_vigente().hit(user_id, now=time.monotonic())
        if espera is None:
            await self.app(scope, receive, send)
            return
        await _dormir(cargar_espera_de_rechazo_ms() / 1000)
        segundos = max(1, math.ceil(espera))
        cuerpo = json.dumps({"detail": {"code": CODIGO, "retry_after": segundos}}).encode()
        await send({"type": "http.response.start", "status": 429, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(cuerpo)).encode()),
            (b"retry-after", str(segundos).encode()),
        ]})
        await send({"type": "http.response.body", "body": cuerpo})
