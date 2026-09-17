"""Almacén de adjuntos por referencia (RD2, 2026-09-17).

DECISIÓN (principal, 2026-09-17): el chat deja de recibir los adjuntos en el
cuerpo JSON; /api/chat/upload los guarda en disco y devuelve un id, y el chat
(RD3) los pide por id. Ruling R23-2: los metadatos van en un sidecar JSON al
lado de los bytes, atado al dueño -- sin tabla nueva, sin migración (mismo
patrón de archivo que jax_engine/owner_cleanup.py).

DISPOSICIÓN en JAX_ADJUNTOS_DIR (absoluto, 0700; fail-closed):

    <id>.dato   imagen: los bytes crudos. texto/PDF: el texto UTF-8 ya
                extraído y recortado a JAX_ADJUNTO_MAX_CHARS (R23-3: del PDF
                no queda nada después de extraer; el chat nunca corre pypdf).
    <id>.json   sidecar: id, user_id, tenant_id, tipo, origen, mime, nombre
                (nombre_seguro), bytes, caracteres, recortado, creado, vence.
    .subiendo-* temporal de una subida en curso (api/upload.py).
    .tmp-*      temporal de una escritura atómica.

Todo 0600. El nombre del cliente nunca forma parte de una ruta. Escritura
atómica: temporal en el mismo directorio + fsync + os.replace. El sidecar se
escribe DESPUÉS del dato: es el commit -- sin sidecar, el adjunto no existe
para `obtener`, y el dato suelto es un huérfano que borra `limpiar`.

DUEÑO: `obtener`/`leer` devuelven el adjunto solo si user_id y tenant_id
coinciden y no venció. Desconocido, ajeno, vencido, malformado, fuera del
directorio o sidecar corrupto: la MISMA excepción (`AdjuntoNoEncontrado`,
404 `adjunto_no_encontrado`, sin extras) -- quien prueba ids ajenos no
distingue "existe pero no es tuyo" de "no existe". El id se valida (alfabeto
y largo exactos) ANTES de construir una ruta.

VENCIMIENTO: JAX_ADJUNTOS_TTL_HORAS (1..168). `obtener` mira `vence` del
sidecar en cada lectura, así que un vencido es 404 aunque el limpiador
todavía no haya pasado. El limpiador (`start_limpieza_de_adjuntos`, tarea
hermana de owner_cleanup en el lifespan) borra vencidos y huérfanos.

BORRADO SEGURO CON LECTORES: se borra con unlink (primero el sidecar, después
el dato). Un lector que ya tiene el dato abierto lo sigue leyendo entero
(POSIX: el inodo vive hasta el último close); uno que llega después ve el
sidecar ausente y recibe el 404 de siempre.

Todo el I/O de disco corre en asyncio.to_thread: las funciones síncronas de
acá las llama api/upload.py (y RD3) a través de to_thread; `obtener` y
`leer` ya lo hacen adentro.
"""
import asyncio
import json
import logging
import os
import re
import secrets
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adjuntos.errores import AdjuntoRechazado
from adjuntos.limites import LimitesDeAdjuntosInvalidos

logger = logging.getLogger(__name__)

VARIABLE_DIRECTORIO = "JAX_ADJUNTOS_DIR"
VARIABLE_TTL_HORAS = "JAX_ADJUNTOS_TTL_HORAS"

# Rango del TTL: 1..168 horas. Piso 1 h: un adjunto se sube mientras se
# escribe el mensaje, y menos de una hora puede vencerlo antes de enviarlo.
# Techo 168 h (7 días): un adjunto es entrada de UN turno, no un archivo del
# usuario; guardarlo más es retener datos ajenos sin uso y disco sin techo.
TTL_HORAS_MIN = 1
TTL_HORAS_MAX = 168

# secrets.token_urlsafe(24): 24 bytes aleatorios = 192 bits (>= 128), 32
# caracteres del alfabeto base64 urlsafe, sin relleno.
BYTES_DE_ENTROPIA = 24
LARGO_ID = 32
_ID = re.compile(r"[A-Za-z0-9_-]{%d}" % LARGO_ID)

SUFIJO_DATO = ".dato"
SUFIJO_SIDECAR = ".json"
PREFIJO_SUBIDA = ".subiendo-"
PREFIJO_ESCRITURA = ".tmp-"

# Un temporal o un dato sin sidecar más viejo que esto es basura de una
# subida que murió (proceso caído, cliente cancelado a mitad). Una subida
# viva no dura tanto: el cuerpo ya llegó entero antes del handler, la copia
# es de <= JAX_ADJUNTO_MAX_BYTES a disco local y pypdf tiene timeout de <= 60
# s; el proxy corta al cliente mucho antes de una hora. No es env: no es una
# política, es un margen técnico sobre esos límites.
ORFANO_MAX_SEGUNDOS = 3600

# Cada 15 minutos. owner_cleanup corre cada 6 h porque retiene 30 días; acá
# el TTL mínimo es 1 h, y a 6 h un adjunto vencido quedaría en disco hasta 7
# veces su vida. La pasada es un scandir + un json chico por adjunto.
INTERVALO_DE_LIMPIEZA_SEGUNDOS = 15 * 60

_TAMANO_DE_BLOQUE = 1024 * 1024

# Referencia propia para que un test la sustituya sin tocar asyncio.sleep global.
_dormir = asyncio.sleep


class AdjuntoNoEncontrado(AdjuntoRechazado):
    """El único "no" de la búsqueda por id. Sin argumentos a propósito: nada
    del motivo (ajeno, vencido, malformado) puede filtrarse al cuerpo."""

    def __init__(self):
        super().__init__(404, "adjunto_no_encontrado")


# ------------------------------------------------------------ configuración

def cargar_directorio() -> Path:
    """JAX_ADJUNTOS_DIR, absoluto. Sin default (fail-closed): un almacén en
    una ruta relativa dependería del cwd del proceso. Se lee en cada llamada
    (un os.environ.get): el entorno no cambia en caliente."""
    crudo = os.environ.get(VARIABLE_DIRECTORIO)
    if not crudo or not os.path.isabs(crudo):
        raise LimitesDeAdjuntosInvalidos(
            f"directorio de adjuntos sin configurar o no absoluto (ruta absoluta en "
            f"/etc/jax/.env): {VARIABLE_DIRECTORIO}={crudo!r}")
    return Path(crudo)


def cargar_ttl_horas() -> int:
    crudo = os.environ.get(VARIABLE_TTL_HORAS)
    valor = int(crudo) if crudo is not None and re.fullmatch(r"[0-9]{1,4}", crudo) else None
    if valor is None or not TTL_HORAS_MIN <= valor <= TTL_HORAS_MAX:
        raise LimitesDeAdjuntosInvalidos(
            f"vencimiento de adjuntos sin configurar o fuera de rango (entero "
            f"{TTL_HORAS_MIN}..{TTL_HORAS_MAX} en /etc/jax/.env): {VARIABLE_TTL_HORAS}={crudo!r}")
    return valor


def preparar_directorio() -> Path:
    """Arranque (lifespan, en to_thread): crea el directorio 0700 si falta y
    comprueba que sirve. Fail-closed si no es un directorio, si está abierto
    a grupo/otros (no se le corrigen los permisos a un directorio que puso
    otro: se avisa) o si este proceso no puede escribir en él (se prueba
    escribiendo, no preguntando)."""
    directorio = cargar_directorio()
    try:
        os.makedirs(directorio, mode=0o700, exist_ok=True)
        info = os.stat(directorio)
    except OSError as e:
        raise LimitesDeAdjuntosInvalidos(
            f"{VARIABLE_DIRECTORIO}={str(directorio)!r}: no se pudo crear ni leer ({type(e).__name__})") from e
    if not stat.S_ISDIR(info.st_mode):
        raise LimitesDeAdjuntosInvalidos(f"{VARIABLE_DIRECTORIO}={str(directorio)!r} no es un directorio")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise LimitesDeAdjuntosInvalidos(
            f"{VARIABLE_DIRECTORIO}={str(directorio)!r} tiene modo "
            f"{stat.S_IMODE(info.st_mode):04o}: tiene que ser 0700 (solo el servicio)")
    prueba = directorio / f"{PREFIJO_ESCRITURA}arranque-{secrets.token_hex(8)}"
    try:
        fd = os.open(prueba, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        os.unlink(prueba)
    except OSError as e:
        raise LimitesDeAdjuntosInvalidos(
            f"{VARIABLE_DIRECTORIO}={str(directorio)!r}: el servicio no puede escribir ({type(e).__name__})") from e
    return directorio


# ---------------------------------------------------------------------- ids

def nuevo_id() -> str:
    return secrets.token_urlsafe(BYTES_DE_ENTROPIA)


def id_valido(valor) -> bool:
    return isinstance(valor, str) and _ID.fullmatch(valor) is not None


def nombre_temporal() -> str:
    """Nombre (no ruta) del temporal de una subida. Se decide en el event
    loop, antes de que el hilo lo cree: si la request se cancela a mitad,
    el handler sabe qué borrar."""
    return f"{PREFIJO_SUBIDA}{secrets.token_hex(16)}"


def _ruta(directorio: Path, id_: str, sufijo: str) -> Path:
    """Solo con un id ya validado. Resuelve y exige quedar dentro del
    directorio: con el alfabeto de arriba no puede salir, salvo por un
    symlink plantado adentro -- y eso también es "no existe"."""
    ruta = directorio / f"{id_}{sufijo}"
    if ruta.resolve().parent != directorio.resolve():
        raise AdjuntoNoEncontrado()
    return ruta


# ------------------------------------------------------------------ escritura

def _iso(momento: datetime) -> str:
    return momento.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _fsync_directorio(directorio: Path) -> None:
    fd = os.open(directorio, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _escribir_atomico(directorio: Path, destino: Path, contenido: bytes) -> None:
    temporal = directorio / f"{PREFIJO_ESCRITURA}{secrets.token_hex(16)}"
    fd = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(contenido)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporal, destino)
    except BaseException:
        temporal.unlink(missing_ok=True)
        raise


def _metadatos(id_, user, tipo, nombre, bytes_, ttl_horas, ahora, **extra) -> dict:
    ahora = ahora or _ahora()
    return {
        "id": id_,
        "user_id": str(user.user_id),
        "tenant_id": str(user.tenant_id),
        "tipo": tipo,
        "nombre": nombre,
        "bytes": bytes_,
        **extra,
        "creado": _iso(ahora),
        "vence": _iso(ahora + timedelta(hours=ttl_horas)),
    }


def _confirmar(directorio: Path, meta: dict) -> dict:
    """Escribe el sidecar (el commit). Si falla, se lleva el dato consigo."""
    id_ = meta["id"]
    try:
        _escribir_atomico(directorio, directorio / f"{id_}{SUFIJO_SIDECAR}",
                          json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        _fsync_directorio(directorio)
    except BaseException:
        # Sidecar incluido: si lo que falló fue el fsync del directorio, el
        # replace ya lo dejó en su lugar y un adjunto a medias no se confirma.
        _borrar_adjunto(directorio, id_)
        raise
    return meta


class SubidaDemasiadoGrande(Exception):
    """La copia pasó de max_bytes: se cortó ahí (413 en api/upload.py)."""


def copiar_subida(origen, destino: Path, max_bytes: int) -> int:
    """Síncrona (to_thread). Copia el archivo que dejó Starlette (`origen`,
    el SpooledTemporaryFile de UploadFile) a `destino` (0600, O_EXCL) en
    bloques de 1 MB, contando. En cuanto pasa de `max_bytes` corta, borra
    `destino` y lanza SubidaDemasiadoGrande: nunca se copia más de
    max_bytes + 1 MB. Memoria: un bloque a la vez."""
    fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    total = 0
    try:
        with os.fdopen(fd, "wb") as f:
            while bloque := origen.read(_TAMANO_DE_BLOQUE):
                total += len(bloque)
                if total > max_bytes:
                    raise SubidaDemasiadoGrande()
                f.write(bloque)
    except BaseException:
        destino.unlink(missing_ok=True)
        raise
    return total


def borrar_temporal(ruta: Path) -> None:
    """Síncrona (to_thread). Idempotente: después de guardar_imagen el
    temporal ya fue renombrado y no hay nada que borrar."""
    ruta.unlink(missing_ok=True)


def guardar_imagen(directorio: Path, temporal: Path, *, user, mime: str, nombre: str,
                   bytes_: int, ttl_horas: int, ahora: datetime | None = None) -> dict:
    """Síncrona (to_thread). La imagen ya está entera en `temporal` (la
    subida): fsync y se renombra al dato, sin copiarla otra vez."""
    with open(temporal, "rb") as f:
        os.fsync(f.fileno())
    id_ = nuevo_id()
    os.replace(temporal, directorio / f"{id_}{SUFIJO_DATO}")
    return _confirmar(directorio, _metadatos(id_, user, "imagen", nombre, bytes_, ttl_horas, ahora,
                                             mime=mime))


def guardar_texto(directorio: Path, texto: str, *, user, origen: str, nombre: str, bytes_: int,
                  recortado: bool, ttl_horas: int, ahora: datetime | None = None) -> dict:
    """Síncrona (to_thread). `texto` ya viene recortado a max_chars; `bytes_`
    es el tamaño del archivo que subió el usuario, no el del texto."""
    id_ = nuevo_id()
    _escribir_atomico(directorio, directorio / f"{id_}{SUFIJO_DATO}", texto.encode("utf-8"))
    return _confirmar(directorio, _metadatos(
        id_, user, "texto", nombre, bytes_, ttl_horas, ahora, origen=origen,
        mime="text/plain", caracteres=len(texto), recortado=recortado))


# ------------------------------------------------------------------- lectura

def _vencido(meta: dict, ahora: datetime) -> bool:
    try:
        vence = datetime.fromisoformat(meta["vence"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError):
        return True
    return vence <= ahora


def _cargar_sidecar(directorio: Path, id_: str, user, ahora: datetime) -> dict:
    ruta = _ruta(directorio, id_, SUFIJO_SIDECAR)
    try:
        meta = json.loads(ruta.read_bytes())
    except (OSError, ValueError):
        raise AdjuntoNoEncontrado() from None
    if (not isinstance(meta, dict) or meta.get("id") != id_
            or meta.get("user_id") != str(user.user_id)
            or meta.get("tenant_id") != str(user.tenant_id)
            or _vencido(meta, ahora)):
        raise AdjuntoNoEncontrado()
    return meta


def _leer(directorio: Path, id_: str, user, ahora: datetime) -> tuple[dict, bytes]:
    meta = _cargar_sidecar(directorio, id_, user, ahora)
    try:
        # Abierto el dato, un borrado concurrente ya no lo corta (ver docstring).
        with open(_ruta(directorio, id_, SUFIJO_DATO), "rb") as f:
            return meta, f.read()
    except OSError:
        raise AdjuntoNoEncontrado() from None


async def obtener(id_, user, *, ahora: datetime | None = None) -> dict:
    """Metadatos de un adjunto del `user`, vigente. Cualquier otro caso:
    AdjuntoNoEncontrado. El id se valida en el loop (una regex sobre <= 32
    caracteres) antes de tocar el disco."""
    if not id_valido(id_):
        raise AdjuntoNoEncontrado()
    return await asyncio.to_thread(_cargar_sidecar, cargar_directorio(), id_, user, ahora or _ahora())


async def leer(id_, user, *, ahora: datetime | None = None) -> tuple[dict, bytes]:
    """Metadatos y bytes del dato (<= JAX_ADJUNTO_MAX_BYTES), leídos en un
    hilo. Mismas reglas que `obtener`."""
    if not id_valido(id_):
        raise AdjuntoNoEncontrado()
    return await asyncio.to_thread(_leer, cargar_directorio(), id_, user, ahora or _ahora())


# ------------------------------------------------------------------- limpieza

def _borrar(ruta: Path) -> int:
    try:
        ruta.unlink()
        return 1
    except FileNotFoundError:
        return 0


def _borrar_adjunto(directorio: Path, id_: str) -> int:
    # Sidecar primero: desde ese instante nadie nuevo lo encuentra.
    return _borrar(directorio / f"{id_}{SUFIJO_SIDECAR}") + _borrar(directorio / f"{id_}{SUFIJO_DATO}")


def limpiar(directorio: Path, ahora: datetime | None = None) -> int:
    """Síncrona (to_thread). Borra adjuntos vencidos, sidecars corruptos
    viejos, datos sin sidecar viejos y temporales viejos. Deja todo lo demás,
    incluido lo que no reconoce. Devuelve cuántos archivos borró."""
    ahora = ahora or _ahora()
    limite_orfano = time.time() - ORFANO_MAX_SEGUNDOS
    borrados = 0
    try:
        entradas = list(os.scandir(directorio))
    except FileNotFoundError:
        return 0
    nombres = {e.name for e in entradas}
    for entrada in entradas:
        nombre = entrada.name
        try:
            viejo = entrada.stat(follow_symlinks=False).st_mtime < limite_orfano
            if nombre.startswith((PREFIJO_SUBIDA, PREFIJO_ESCRITURA)):
                if viejo:
                    borrados += _borrar(Path(entrada.path))
            elif nombre.endswith(SUFIJO_SIDECAR) and id_valido(nombre[:-len(SUFIJO_SIDECAR)]):
                id_ = nombre[:-len(SUFIJO_SIDECAR)]
                try:
                    meta = json.loads(Path(entrada.path).read_bytes())
                    corrupto = not isinstance(meta, dict)
                except ValueError:
                    corrupto = True
                if (corrupto and viejo) or (not corrupto and _vencido(meta, ahora)):
                    borrados += _borrar_adjunto(directorio, id_)
            elif nombre.endswith(SUFIJO_DATO) and id_valido(nombre[:-len(SUFIJO_DATO)]):
                if viejo and f"{nombre[:-len(SUFIJO_DATO)]}{SUFIJO_SIDECAR}" not in nombres \
                        and not (directorio / f"{nombre[:-len(SUFIJO_DATO)]}{SUFIJO_SIDECAR}").exists():
                    borrados += _borrar(Path(entrada.path))
        except FileNotFoundError:  # fail-soft: carrera benigna con otra limpieza o un borrado concurrente; el archivo ya no está, que es lo que se buscaba
            continue
    if borrados:
        logger.info("adjuntos: limpieza borró %s archivo(s)", borrados)
    return borrados


def borrar_de_usuario(directorio: Path, user_id: str) -> int:
    """Síncrona (to_thread). Borra todos los adjuntos de `user_id` (la baja,
    api/admin/users.py). Por user_id solo: es la PK global de jax_users."""
    borrados = 0
    try:
        entradas = list(os.scandir(directorio))
    except FileNotFoundError:
        return 0
    for entrada in entradas:
        nombre = entrada.name
        if not (nombre.endswith(SUFIJO_SIDECAR) and id_valido(nombre[:-len(SUFIJO_SIDECAR)])):
            continue
        try:
            meta = json.loads(Path(entrada.path).read_bytes())
        except (OSError, ValueError):
            continue  # corrupto o ya borrado: lo levanta limpiar() por edad
        if isinstance(meta, dict) and meta.get("user_id") == str(user_id):
            borrados += _borrar_adjunto(directorio, nombre[:-len(SUFIJO_SIDECAR)])
    return borrados


async def start_limpieza_de_adjuntos():
    """Tarea de fondo del lifespan, hermana de owner_cleanup (mismo patrón:
    pasa al arrancar, después duerme; nunca muere por un fallo). No va dentro
    del bucle de owner_cleanup: ese duerme 6 h (ver INTERVALO_DE_LIMPIEZA)."""
    while True:
        try:
            await asyncio.to_thread(limpiar, cargar_directorio())
        except Exception:  # fail-soft: loop de limpieza en background, mismo patrón que owner_cleanup.py -- nunca debe tumbar el proceso; el próximo ciclo reintenta y un vencido ya es 404 en la lectura aunque siga en disco
            logger.warning("adjuntos: la limpieza falló, se reintenta en el próximo ciclo", exc_info=True)
        await _dormir(INTERVALO_DE_LIMPIEZA_SEGUNDOS)
