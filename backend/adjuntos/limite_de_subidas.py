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
usuario ya gastó su cupo, responde 429 SIN llamar a receive(): el cuerpo no
se lee, no se parsea y no se vuelca. Qué pasa con los bytes que el cliente
igual manda lo decide el servidor HTTP (uvicorn); medido en RD7
(rd6-report.md): TMPDIR no recibe nada.

IDENTIDAD. Solo la firma del token de ACCESO: sin base, a propósito (una
lectura por PK por request sería el costo que se quiere evitar). Un token
revocado pero no vencido (<= 15 min) solo puede gastar el cupo de SU propio
usuario; la ruta lo sigue rechazando con 401 como siempre. Sin token, con uno
inválido, de refresh o con un user_id malformado, no hay a quién limitar: se
deja pasar sin contar y la ruta responde su 401.

CUÁNTO. JAX_ADJUNTOS_SUBIDAS_POR_MINUTO (1..600, sin default: fail-closed),
ventana deslizante de 60 s (auth.rate_limit.SlidingWindowLimiter, la misma
clase del login y del SMTP). Un intento rechazado no se cuenta. Retry-After =
segundos hasta que se libera un lugar, redondeado arriba, mínimo 1.

ESTADO. En memoria del proceso: jax-platform es UN proceso
(auth/rate_limit.py::exigir_un_solo_proceso). Tope de claves: el de login
(20.000, LRU). El limitador se rehace si cambia el valor configurado (el
entorno de producción no cambia en caliente; los tests sí lo cambian).
"""
import json
import math
import os
import re
import time

from adjuntos.limites import LimitesDeAdjuntosInvalidos
from auth.rate_limit import SlidingWindowLimiter

VARIABLE = "JAX_ADJUNTOS_SUBIDAS_POR_MINUTO"
RUTA = "/api/chat/upload"
CODIGO = "adjuntos_subidas_limite"

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


def cargar_subidas_por_minuto() -> int:
    crudo = os.environ.get(VARIABLE)
    valor = int(crudo) if crudo is not None and _ENTERO.fullmatch(crudo) else None
    if valor is None or not SUBIDAS_POR_MINUTO_MIN <= valor <= SUBIDAS_POR_MINUTO_MAX:
        raise LimitesDeAdjuntosInvalidos(
            f"límite de subidas de adjuntos sin configurar o fuera de rango (entero "
            f"{SUBIDAS_POR_MINUTO_MIN}..{SUBIDAS_POR_MINUTO_MAX}, solo dígitos, en /etc/jax/.env): "
            f"{VARIABLE}={crudo!r}")
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


def _usuario_del_token(scope) -> str | None:
    from fastapi import HTTPException

    from adjuntos.almacen import user_id_valido
    from auth.jwt import decode_token

    autorizacion = None
    for nombre, valor in scope.get("headers", ()):
        if nombre == b"authorization":
            autorizacion = valor.decode("latin-1")
            break
    if not autorizacion:
        return None
    esquema, _, token = autorizacion.partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        return None
    try:
        payload = decode_token(token.strip())
    except HTTPException:
        return None  # firma o vencimiento inválidos: la ruta responde su 401
    if not isinstance(payload, dict) or payload.get("type") != "access":
        return None
    user_id = payload.get("user_id")
    return user_id if user_id_valido(user_id) else None


class LimiteDeSubidas:
    """Middleware ASGI. Ver el docstring del módulo."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != RUTA:
            await self.app(scope, receive, send)
            return
        user_id = _usuario_del_token(scope)
        if user_id is None:
            await self.app(scope, receive, send)
            return
        espera = _limitador_vigente().hit(user_id, now=time.monotonic())
        if espera is None:
            await self.app(scope, receive, send)
            return
        segundos = max(1, math.ceil(espera))
        cuerpo = json.dumps({"detail": {"code": CODIGO, "retry_after": segundos}}).encode()
        await send({"type": "http.response.start", "status": 429, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(cuerpo)).encode()),
            (b"retry-after", str(segundos).encode()),
        ]})
        await send({"type": "http.response.body", "body": cuerpo})
