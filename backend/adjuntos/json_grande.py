"""json.loads del cuerpo de /api/chat sin escanear el base64 en el event loop (R16, 2026-09-17).

Medido en staging (chat_imagen_max a c=25): json.loads de un cuerpo de 14 MB
son 8,3 ms por pedido, una sola llamada en C que retiene el GIL, así que
tampoco sirve mandarla a asyncio.to_thread. Con /api/health en paralelo, su
p95 subía de 0,3 ms a 70 ms.

El literal grande (`"base64":"...`) se separa con búsquedas de bytes (memchr,
fracciones de ms), se parsea con json.loads solo el resto chico, y el literal
se vuelve a poner. Es EXACTAMENTE json.loads(body) o se cae a json.loads(body):

- q1 es la comilla que abre el valor de `"base64":` y q2 la siguiente
  comilla. Ninguna tiene una barra invertida antes (no hay ninguna entre
  ellas), así que las dos son delimitadores de string de JSON; entre ellas no
  hay otra comilla, así que el tramo es UN string o el hueco entre dos. Si
  fuera el hueco, el cuerpo con la marca no parsea y se cae al camino normal.
- Sin barras invertidas no hay escapes: el valor es el tramo tal cual, si es
  UTF-8 válido y no trae caracteres de control crudos (que json.loads
  rechaza). Los caracteres de control se buscan por tramos, cediendo el loop entre
  uno y otro (re retiene el GIL por llamada; un hilo le disputaría el GIL).
- La marca es aleatoria y tiene que aparecer una sola vez, como valor.
- Cuerpo que no es UTF-8 (JSON en UTF-16/32), cuerpo chico, o cualquier otra
  cosa rara: json.loads(body), con sus mismos errores (FastAPI los convierte
  en el mismo 422).
"""
import asyncio
import json
import re
import uuid

from adjuntos.turno import turno_de_imagen

_UMBRAL = 64 * 1024
_CLAVE = b'"base64":"'
_SIN_CONTROL = re.compile(rb"[^\x00-\x1f]*")
_TRAMO = 256 * 1024


async def _sin_caracteres_de_control(body: bytes, inicio: int, fin: int) -> bool:
    # En el loop cediendo entre tramos, no en un hilo: medido, un hilo que
    # escanea le disputa el GIL al loop y empeora la latencia de los demás.
    for desde in range(inicio, fin, _TRAMO):
        if _SIN_CONTROL.fullmatch(body, desde, min(desde + _TRAMO, fin)) is None:
            return False
        await asyncio.sleep(0)
    return True


def _reemplazar_marca(obj, marca: str, valor: str, hallados: list) -> object:
    if isinstance(obj, dict):
        if marca in obj:
            hallados.append("clave")
        return {k: _reemplazar_marca(v, marca, valor, hallados) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_reemplazar_marca(v, marca, valor, hallados) for v in obj]
    if obj == marca:
        hallados.append("valor")
        return valor
    return obj


async def cargar(body: bytes):
    q1 = body.find(_CLAVE)
    if len(body) < _UMBRAL or q1 < 0 or json.detect_encoding(body) != "utf-8":
        return json.loads(body)
    q1 += len(_CLAVE) - 1
    q2 = body.find(b'"', q1 + 1)
    if q2 < 0 or q2 - q1 - 1 < _UMBRAL or body.find(b"\\", q1 + 1, q2) >= 0:
        return json.loads(body)
    async with turno_de_imagen():
        if not await _sin_caracteres_de_control(body, q1 + 1, q2):
            return json.loads(body)
    try:
        valor = str(memoryview(body)[q1 + 1:q2], "utf-8")
    except UnicodeDecodeError:
        return json.loads(body)
    marca = f"JAXLITERAL{uuid.uuid4().hex}"
    try:
        chico = json.loads(b"".join((body[:q1 + 1], marca.encode("ascii"), body[q2:])))
    except ValueError:
        return json.loads(body)
    hallados: list[str] = []
    datos = _reemplazar_marca(chico, marca, valor, hallados)
    if hallados != ["valor"]:
        return json.loads(body)
    return datos
