"""Límite de intentos de login, por IP y por email (2026-09-12).

Desde que el login ya no deja enumerar cuentas (jax-platform#57), TODO intento
-- exista o no el email -- verifica un bcrypt de costo 12: ~155 ms de CPU.
No había ningún limitador, así que cualquiera que alcanzara /api/auth/login
podía hacerle gastar CPU al servidor (medido en vivo: c=5 -> 32 rps). Esto se
aplica ANTES de la DB y ANTES del bcrypt: lo que se protege es el bcrypt.

En memoria, sin Redis: jax-platform corre UN proceso uvicorn (sin --workers,
medido en el ExecStart de la unidad). Si algún día corre varios, cada uno
tendría su propio contador y el límite efectivo se multiplicaría -- ahí sí
haría falta un almacén compartido.

LA IP DEL CLIENTE. El público entra por nginx en la VM dev, que fija
`X-Real-IP $remote_addr` (sobrescribe lo que mande el cliente). Sin mirar ese
header, TODO el público llega con la IP del proxy y compartiría un solo
balde. Pero el header solo vale si la conexión viene del proxy: cualquiera
que llegue directo al :8080 podría mandarlo inventado. Por eso se usa solo
si el peer está en JAX_TRUSTED_PROXIES (lista en /etc/jax/.env, no en el
código); de cualquier otro peer se toma la IP de la conexión y se ignoran los
headers. X-Forwarded-For NO se usa: nginx le AGREGA al valor del cliente, así
que su primer elemento es falsificable.

Umbrales (configurables, "intentos/segundos"):
- JAX_LOGIN_RATE_IP=20/60: una persona usa 1-3 intentos; 20 deja margen a una
  oficina detrás de un NAT. Un atacante de una IP queda en <=0,33 bcrypt/s.
- JAX_LOGIN_RATE_EMAIL=10/300: frena el ataque distribuido contra UNA cuenta
  (muchas IPs, un email); el bloqueo por cuenta (5 fallos) sigue aparte.
- JAX_LOGIN_RATE_MAX_KEYS=20000: memoria acotada. Peor caso medido en orden de
  ~1 KB por clave llena (deque de 20 floats), ~20 MB. Al pasar el tope se
  descarta la clave usada hace más tiempo (LRU): un atacante con más de 20.000
  IPs ya no está limitado por IP de todos modos.

Excedido -> 429 con Retry-After y el MISMO cuerpo para todos: no revela si la
cuenta existe. Un intento rechazado no se cuenta, así que el bloqueo no se
extiende solo mientras el cliente insiste.
"""
from __future__ import annotations

import logging
import math
import os
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

DEMASIADOS_INTENTOS = "Demasiados intentos. Espera y vuelve a intentarlo."


def parse_rate(spec: str) -> tuple[int, float]:
    """'20/60' -> (20, 60.0). Falla fuerte ante un valor mal escrito: un límite
    que no se entiende no puede quedar desactivado en silencio."""
    try:
        hits_raw, window_raw = spec.split("/")
        hits, window = int(hits_raw), float(window_raw)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"límite inválido {spec!r}: formato 'intentos/segundos'") from exc
    if hits <= 0 or window <= 0:
        raise ValueError(f"límite inválido {spec!r}: intentos y segundos deben ser > 0")
    return hits, window


class SlidingWindowLimiter:
    """Ventana deslizante por clave, con tope de claves (LRU)."""

    def __init__(self, max_hits: int, window_s: float, max_keys: int = 20_000) -> None:
        self.max_hits = max_hits
        self.window_s = float(window_s)
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._hits)

    def reset(self) -> None:
        self._hits.clear()

    def hit(self, key: str, now: float | None = None) -> float | None:
        """Registra un intento. None si se permite; si no, los segundos que
        faltan para que se libere un lugar (el intento NO se registra)."""
        now = time.monotonic() if now is None else now
        hits = self._hits.get(key)
        if hits is None:
            hits = deque()
            self._hits[key] = hits
        else:
            self._hits.move_to_end(key)
        cutoff = now - self.window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.max_hits:
            return max(hits[0] + self.window_s - now, 0.0)
        hits.append(now)
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)
        return None


def client_ip(request: Request, trusted: frozenset[str]) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in trusted:
        real = request.headers.get("x-real-ip", "").strip()
        if real:
            return real
    return peer


def _trusted_from_env() -> frozenset[str]:
    return frozenset(p.strip() for p in os.getenv("JAX_TRUSTED_PROXIES", "").split(",") if p.strip())


TRUSTED_PROXIES = _trusted_from_env()
if not TRUSTED_PROXIES:
    logger.warning(
        "JAX_TRUSTED_PROXIES vacío: detrás de nginx todo el público llega con la IP "
        "del proxy y comparte un solo límite de login. Fijarlo en /etc/jax/.env."
    )

_MAX_KEYS = int(os.getenv("JAX_LOGIN_RATE_MAX_KEYS", "20000"))
LOGIN_IP_LIMITER = SlidingWindowLimiter(*parse_rate(os.getenv("JAX_LOGIN_RATE_IP", "20/60")), max_keys=_MAX_KEYS)
LOGIN_EMAIL_LIMITER = SlidingWindowLimiter(*parse_rate(os.getenv("JAX_LOGIN_RATE_EMAIL", "10/300")), max_keys=_MAX_KEYS)


def reset_login_limiters() -> None:
    LOGIN_IP_LIMITER.reset()
    LOGIN_EMAIL_LIMITER.reset()


def _demasiados(retry: float) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=DEMASIADOS_INTENTOS,
        headers={"Retry-After": str(max(1, math.ceil(retry)))},
    )


def check_login_rate(request: Request, email: str) -> None:
    """Primero la IP, después el email normalizado. Lanza 429 si alguno se
    pasó. Lee los limitadores del módulo en cada llamada (los tests los
    reemplazan)."""
    retry = LOGIN_IP_LIMITER.hit(client_ip(request, TRUSTED_PROXIES))
    if retry is not None:
        raise _demasiados(retry)
    retry = LOGIN_EMAIL_LIMITER.hit((email or "").strip().lower())
    if retry is not None:
        raise _demasiados(retry)
