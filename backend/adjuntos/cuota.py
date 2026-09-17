"""Cuota por usuario y guarda de disco libre de los adjuntos (RD6, 2026-09-17).

DECISIÓN (principal, 2026-09-17, "quota"): RD5 midió que upload_imagen_max a
c=25 escribe ~0,5 GB/s en JAX_ADJUNTOS_DIR, con retención hasta el TTL. Sin
techo por usuario, uno solo llena el disco del servicio.

CUOTA (JAX_ADJUNTOS_CUOTA_BYTES_USUARIO): lo que ocupa un usuario es la suma
del campo `bytes` (lo que SUBIÓ, no lo que quedó guardado: de un PDF queda
solo el texto) de sus adjuntos VIGENTES -- los que `almacen.obtener` le
devolvería --, más lo reservado por sus subidas en vuelo. Se mira dos veces:

  1. `reserva(...)`, ANTES de copiar: con el tamaño que Starlette ya recibió.
     Si entra, queda reservado; si no, 413 `adjuntos_cuota_excedida` y no se
     escribe nada.
  2. `Reserva.confirmar(...)`, al hacer el commit (el sidecar que vuelve
     visible y contable el adjunto): se vuelve a leer el disco y se confirma
     con el tamaño REAL copiado, sin soltar el candado hasta que el sidecar
     está escrito y la reserva convertida en adjunto.

Reserva y confirmación corren bajo un asyncio.Lock POR user_id. Eso alcanza
porque jax-platform es UN proceso: auth/rate_limit.py::exigir_un_solo_proceso
aborta el arranque con --workers/WEB_CONCURRENCY > 1 (el límite de login
también vive en memoria). Si algún día hay más de un proceso, este candado
deja de ser atómico y la cuota pasa a necesitar un candado compartido (flock
sobre el directorio o la base): no es un caché que se invalida, es la
exclusión mutua misma.

Dos subidas del mismo usuario que juntas se pasan: la primera reserva bajo el
candado; la segunda lee el disco bajo el mismo candado, ve la reserva de la
primera y es 413 antes de copiar.

DISCO (JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES): shutil.disk_usage del
filesystem de JAX_ADJUNTOS_DIR, en un hilo, antes de copiar. Rechaza si,
escrito este archivo, quedaría menos que el mínimo libre. Global (no por
usuario): protege a todo lo que comparte el disco. Si MEDIR falla, se
rechaza igual (fail-closed): no saber cuánto queda no es permiso para
escribir. 507 `adjuntos_sin_espacio`, sin números en el cuerpo (el estado
del disco del servidor no es asunto del cliente).

Sin caché del uso: se relee el directorio en cada reserva y en cada commit.
Es la fuente de verdad (sidecars) y no hay nada que invalidar cuando el
limpiador, la baja o el vencimiento cambian el uso. Costo: un scandir + un
json chico por adjunto vigente de TODOS los usuarios, en un hilo; medido en
RD6 (rd6-report.md). Si el directorio crece a decenas de miles de entradas,
esa es la medición que obliga a indexar por usuario.
"""
import asyncio
import logging
import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from adjuntos import almacen
from adjuntos.errores import AdjuntoRechazado
from adjuntos.limites import LimitesDeAdjuntosInvalidos, cargar_limites

logger = logging.getLogger(__name__)

VARIABLE_CUOTA = "JAX_ADJUNTOS_CUOTA_BYTES_USUARIO"
VARIABLE_DISCO_LIBRE = "JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES"

# Cuota 1 MiB..1 TiB. Piso 1 MiB: menos no deja subir ni una foto de
# teléfono; además, validar_configuracion exige cuota >= JAX_ADJUNTO_MAX_BYTES
# (una cuota menor que el tope por archivo rechazaría archivos que el tope
# admite, con el código equivocado). Techo 1 TiB: por encima ya no es un
# límite por usuario sino "sin límite" escrito con más ceros.
CUOTA_MIN = 1024 ** 2
CUOTA_MAX = 1024 ** 4

# Disco libre mínimo 1 GiB..1 TiB. Piso 1 GiB: por debajo, el resto de lo que
# vive en ese filesystem (MariaDB, logs, el spool de Starlette si TMPDIR está
# ahí) se queda sin aire antes de que la guarda corte. Techo 1 TiB: un mínimo
# mayor que el disco entero es "nunca aceptar", un error de configuración
# que tiene que verse al arrancar y no como 507 a cada subida.
DISCO_LIBRE_MIN = 1024 ** 3
DISCO_LIBRE_MAX = 1024 ** 4

_ENTERO = re.compile(r"[1-9][0-9]{0,15}")


class CuotaExcedida(AdjuntoRechazado):
    def __init__(self, cuota_bytes: int):
        super().__init__(413, "adjuntos_cuota_excedida", cuota_bytes=cuota_bytes)


class SinEspacio(AdjuntoRechazado):
    def __init__(self):
        super().__init__(507, "adjuntos_sin_espacio")


# ------------------------------------------------------------ configuración

def _cargar_acotado(variable: str, que: str, minimo: int, maximo: int) -> int:
    crudo = os.environ.get(variable)
    valor = int(crudo) if crudo is not None and _ENTERO.fullmatch(crudo) else None
    if valor is None or not minimo <= valor <= maximo:
        raise LimitesDeAdjuntosInvalidos(
            f"{que} sin configurar o fuera de rango (entero {minimo}..{maximo}, solo "
            f"dígitos, en /etc/jax/.env): {variable}={crudo!r}")
    return valor


def cargar_cuota_por_usuario() -> int:
    """Se lee en cada subida (un os.environ.get): el entorno no cambia en
    caliente. Sin default: si falta, el servicio no arranca."""
    return _cargar_acotado(VARIABLE_CUOTA, "cuota de adjuntos por usuario", CUOTA_MIN, CUOTA_MAX)


def cargar_disco_libre_minimo() -> int:
    return _cargar_acotado(VARIABLE_DISCO_LIBRE, "disco libre mínimo de adjuntos",
                           DISCO_LIBRE_MIN, DISCO_LIBRE_MAX)


def validar_configuracion() -> None:
    """Arranque (lifespan): las dos variables en rango y la cuota no menor
    que el tope por archivo."""
    cuota = cargar_cuota_por_usuario()
    cargar_disco_libre_minimo()
    max_bytes = cargar_limites().max_bytes
    if cuota < max_bytes:
        raise LimitesDeAdjuntosInvalidos(
            f"{VARIABLE_CUOTA}={cuota} es menor que JAX_ADJUNTO_MAX_BYTES={max_bytes}: "
            f"un archivo admitido por el tope nunca entraría en la cuota")


# ----------------------------------------------------------------- disco

async def exigir_disco_libre(directorio: Path, necesario: int) -> None:
    """Antes de copiar. `necesario`: lo que se va a escribir."""
    minimo = cargar_disco_libre_minimo()
    try:
        libre = (await asyncio.to_thread(shutil.disk_usage, directorio)).free
    except Exception as e:  # no traga: medir falló -> se rechaza la subida (fail-closed) y se loguea el tipo
        logger.error("adjuntos: no se pudo medir el disco libre de %s (%s); subida rechazada",
                     almacen.VARIABLE_DIRECTORIO, type(e).__name__)
        raise SinEspacio() from e
    if libre - necesario < minimo:
        logger.warning("adjuntos: disco libre bajo el mínimo (%s bytes libres, %s necesarios, mínimo %s); "
                       "subida rechazada", libre, necesario, minimo)
        raise SinEspacio()


# ----------------------------------------------------------------- cuota

class _Cuenta:
    """Estado por user_id: el candado, lo reservado en vuelo y cuántas
    subidas lo están usando (para sacarlo de la tabla al quedar libre: sin
    eso la tabla crece con cada usuario que alguna vez subió algo, y un
    asyncio.Lock usado por un loop no sirve en otro -- los tests abren uno
    por asyncio.run)."""

    __slots__ = ("candado", "reservado", "en_uso")

    def __init__(self):
        self.candado = asyncio.Lock()
        self.reservado = 0
        self.en_uso = 0


_cuentas: dict[str, _Cuenta] = {}


class Reserva:
    def __init__(self, cuenta: _Cuenta, directorio: Path, user, bytes_: int, cuota: int):
        self._cuenta = cuenta
        self._directorio = directorio
        self._user = user
        self._cuota = cuota
        self.bytes = bytes_

    def _soltar(self) -> None:
        self._cuenta.reservado -= self.bytes
        self.bytes = 0

    async def confirmar(self, tamano: int, guardar):
        """Segundo chequeo y commit bajo el candado. `guardar` es una función
        sin argumentos que devuelve el awaitable que escribe el sidecar (en un
        hilo). Si la tarea se cancela durante la escritura -- una vez o
        muchas --, el hilo no se puede interrumpir: se sigue esperando hasta
        que termine y recién ahí se relanza la cancelación, con el candado y
        la reserva todavía tomados. Así nadie cuenta de menos un adjunto que
        igual aparece. Consecuencia (documentada en el diseño §7.1): un
        cancelado en el commit deja el adjunto guardado; cuenta en la cuota
        hasta vencer y el cliente nunca recibe su id."""
        cuenta = self._cuenta
        async with cuenta.candado:
            usado = await asyncio.to_thread(almacen.uso_de_usuario, self._directorio,
                                            str(self._user.user_id))
            if usado + (cuenta.reservado - self.bytes) + tamano > self._cuota:
                raise CuotaExcedida(self._cuota)
            escritura = asyncio.ensure_future(guardar())
            cancelacion = None
            while not escritura.done():
                try:
                    # asyncio.wait no cancela `escritura` al cancelarse él.
                    await asyncio.wait({escritura})
                except asyncio.CancelledError as e:
                    cancelacion = e
            # Escrito (o fallido): el adjunto, si existe, ya es vigente en
            # disco y la reserva sobra.
            self._soltar()
            if cancelacion is not None:
                if not escritura.cancelled():
                    escritura.exception()  # recuperada: si falló, manda la cancelación
                raise cancelacion
            return escritura.result()


@asynccontextmanager
async def reserva(directorio: Path, user, declarado: int, cuota: int):
    """Primer chequeo: reserva `declarado` bytes para `user` o CuotaExcedida.
    Al salir (éxito, rechazo, error o cancelación) libera lo que quede
    reservado -- sin await: una cancelación no puede dejar una reserva
    colgada."""
    clave = str(user.user_id)
    cuenta = _cuentas.get(clave)
    if cuenta is None:
        cuenta = _cuentas[clave] = _Cuenta()
    cuenta.en_uso += 1
    r = Reserva(cuenta, directorio, user, 0, cuota)
    try:
        async with cuenta.candado:
            usado = await asyncio.to_thread(almacen.uso_de_usuario, directorio, clave)
            if usado + cuenta.reservado + declarado > cuota:
                raise CuotaExcedida(cuota)
            cuenta.reservado += declarado
            r.bytes = declarado
        yield r
    finally:
        r._soltar()
        cuenta.en_uso -= 1
        if cuenta.en_uso == 0 and _cuentas.get(clave) is cuenta:
            del _cuentas[clave]
