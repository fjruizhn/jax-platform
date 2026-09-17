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

Sin caché del uso: se relee la carpeta del usuario en cada reserva y en cada
commit. Es la fuente de verdad (sidecars) y no hay nada que invalidar cuando
el limpiador, la baja o el vencimiento cambian el uso. Costo: un scandir + un
json chico por entrada de la carpeta DE ESE USUARIO (RD7: una carpeta por
user_id; nunca la raíz ni la de otro), en un hilo. RD6 midió el barrido
cuando todavía era de todos los usuarios: ~6 us por sidecar en ext4 (10.000
en 60 ms). Con plazo (Final fix wave #2, item 6: `_uso_acotado`): un disco
trabado no retiene el candado del usuario para siempre.
"""
import asyncio
import logging
import math
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


# Plazo del commit (RD7 fix round, Ruling R29). El commit escribe en un hilo
# que no se puede interrumpir: si el disco se cuelga (EIO, almacenamiento
# trabado), esperar sin tope retenía el candado del usuario para siempre y ese
# usuario no volvía a subir hasta reiniciar. Lo más pesado del commit es el
# fsync de la imagen entera (<= JAX_ADJUNTO_MAX_BYTES) más el sidecar y el
# fsync del directorio. Plazo = tope por archivo a 256 KiB/s (un disco
# degradado: un HDD sano escribe >= 100 MiB/s, uno en reintentos de EIO puede
# bajar a KB/s), con piso de 60 s para cubrir la cola de fsync cuando muchas
# subidas confirman a la vez (RD5: 14 GB escritos en 30 s). Con el tope de
# producción (10 MiB) da 60 s. Sin variable nueva: se deriva de un límite que
# ya existe y de una cota física declarada acá.
PLAZO_DE_ESCRITURA_PISO_SEGUNDOS = 60
DISCO_LENTO_BYTES_POR_SEGUNDO = 256 * 1024


def plazo_de_escritura_segundos() -> float:
    return max(PLAZO_DE_ESCRITURA_PISO_SEGUNDOS,
               math.ceil(cargar_limites().max_bytes / DISCO_LENTO_BYTES_POR_SEGUNDO))


def _temporizador_de_plazo(plazo: float) -> "asyncio.Task[None]":
    """Tarea que se resuelve cuando vence `plazo`. Separada de `confirmar`
    para que los tests puedan sustituirla por un `asyncio.Event` bajo su
    propio control -- verificar "si el commit no termina a tiempo, se
    libera la cuota" sin correr contra el reloj real ni contra la carga de
    la máquina que corre la suite."""
    return asyncio.ensure_future(asyncio.sleep(plazo))


# Plazo de la lectura del uso (Final fix wave #2, item 6). `almacen.uso_de_usuario`
# corre en un hilo BAJO el candado del usuario, dos veces por subida (reserva y
# confirmación): colgada sin plazo, ese usuario no volvía a subir hasta
# reiniciar -- el mismo modo de falla que R29 cerró para el commit. Es una
# lectura: abandonarla no deja nada a medio escribir, así que al vencer se
# suelta el candado sin esperar al hilo. Por qué 60 s: RD6 midió el barrido a
# ~6 us por sidecar en ext4 (10.000 en 60 ms, p95 62 ms) y desde RD7 recorre
# solo la carpeta del usuario. Con el límite de deploy (30 subidas/min) y el
# TTL máximo (168 h), un usuario junta como mucho 302.400 sidecars: ~1,8 s.
# 60 s deja más de 30 veces de margen (con el techo del rango, 600/min, serían
# ~6 millones y ~36 s: sigue adentro); pasarlo es un disco trabado, no una
# carpeta grande. Sin variable nueva: es un margen técnico, igual que el piso
# del commit.
PLAZO_DE_LECTURA_DE_USO_SEGUNDOS = 60


def plazo_de_lectura_de_uso_segundos() -> float:
    return PLAZO_DE_LECTURA_DE_USO_SEGUNDOS


async def _uso_acotado(directorio: Path, clave: str) -> int:
    """`almacen.uso_de_usuario` en un hilo, con plazo. Vencido: loguea
    TimeoutError (sin rutas ni ids) y 503 `adjuntos_reintentar`; el llamador
    está dentro del `async with` del candado, así que la excepción lo suelta.
    El hilo abandonado sigue en el pool por defecto hasta que el disco
    responda (ver el diseño §7.1)."""
    plazo = plazo_de_lectura_de_uso_segundos()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(almacen.uso_de_usuario, directorio, clave), timeout=plazo)
    except TimeoutError:
        logger.error("adjuntos: leer el uso del usuario no terminó en %s s (TimeoutError); "
                     "se suelta su candado", plazo)
        raise LecturaDeUsoSinTerminar() from None


class LecturaDeUsoSinTerminar(AdjuntoRechazado):
    """La lectura del uso no terminó dentro del plazo: 503, el cliente reintenta."""

    def __init__(self):
        super().__init__(503, "adjuntos_reintentar")


class EscrituraSinTerminar(AdjuntoRechazado):
    """El commit no terminó dentro del plazo: 503, el cliente reintenta."""

    def __init__(self):
        super().__init__(503, "adjuntos_reintentar")


def _registrar_escritura_tardia(escritura) -> None:
    """Callback de la escritura abandonada por plazo: si al final falla, se
    loguea el tipo (y la excepción queda recuperada); si termina bien, el
    adjunto existe sin que nadie tenga su id y lo borra el limpiador al vencer."""
    if escritura.cancelled():
        return
    error = escritura.exception()
    if error is not None:
        logger.error("adjuntos: la escritura abandonada por plazo terminó con error (%s)", type(error).__name__)
    else:
        logger.warning("adjuntos: la escritura abandonada por plazo terminó tarde; el adjunto vence por TTL")


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
            usado = await _uso_acotado(self._directorio, str(self._user.user_id))
            if usado + (cuenta.reservado - self.bytes) + tamano > self._cuota:
                raise CuotaExcedida(self._cuota)
            plazo = plazo_de_escritura_segundos()
            escritura = asyncio.ensure_future(guardar())
            vencimiento = _temporizador_de_plazo(plazo)
            cancelacion = None
            try:
                while not escritura.done() and not vencimiento.done():
                    try:
                        # asyncio.wait no cancela `escritura` ni `vencimiento`
                        # al cancelarse él ni al vencer su plazo.
                        await asyncio.wait({escritura, vencimiento}, return_when=asyncio.FIRST_COMPLETED)
                    except asyncio.CancelledError as e:
                        cancelacion = e
            finally:
                if not vencimiento.done():
                    vencimiento.cancel()
            # Escrito, fallido o fuera de plazo: se suelta la reserva (y al
            # salir del `async with`, el candado).
            self._soltar()
            if not escritura.done():
                # R29: el hilo sigue colgado. Lo que escriba tarde queda como
                # adjunto sin id conocido (lo borra el limpiador al vencer) y
                # puede no haberse contado en una subida siguiente: se acepta
                # a cambio de no dejar al usuario sin subir hasta reiniciar.
                logger.error("adjuntos: el commit no terminó en %s s (TimeoutError); se libera la cuota "
                             "del usuario", plazo)
                escritura.add_done_callback(_registrar_escritura_tardia)
                if cancelacion is not None:
                    raise cancelacion
                raise EscrituraSinTerminar()
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
            usado = await _uso_acotado(directorio, clave)
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
