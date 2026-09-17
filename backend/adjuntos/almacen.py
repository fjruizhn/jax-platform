"""Almacén de adjuntos por referencia (RD2, 2026-09-17).

DECISIÓN (principal, 2026-09-17): el chat deja de recibir los adjuntos en el
cuerpo JSON; /api/chat/upload los guarda en disco y devuelve un id, y el chat
(RD3) los pide por id. Ruling R23-2: los metadatos van en un sidecar JSON al
lado de los bytes, atado al dueño -- sin tabla nueva, sin migración (mismo
patrón de archivo que jax_engine/owner_cleanup.py).

DISPOSICIÓN en JAX_ADJUNTOS_DIR (absoluto, 0700; fail-closed). RD7 (decisión
del principal, 2026-09-17): una carpeta por usuario, JAX_ADJUNTOS_DIR/<user_id>/
(0700). Solo user_id, sin tenant: user_id es la PK global de jax_users (la
baja ya borra por user_id), y el sidecar sigue guardando tenant_id y la
búsqueda sigue exigiendo los dos. La carpeta es para que la cuota y la búsqueda
lean O(archivos del usuario), no un control de acceso: el dueño lo sigue
decidiendo el sidecar. El user_id se valida ([1-9][0-9]{0,19}) antes de armar
una ruta aunque venga del JWT. Almacén nuevo, sin desplegar: no hay
disposición plana previa que migrar. Dentro de cada carpeta:

    <id>.dato   imagen: los bytes crudos. texto/PDF: el texto UTF-8 ya
                extraído y recortado a JAX_ADJUNTO_MAX_CHARS (R23-3: del PDF
                no queda nada después de extraer; el chat nunca corre pypdf).
    <id>.json   sidecar: id, user_id, tenant_id, tipo, origen, mime, nombre
                (nombre_seguro), bytes, caracteres, recortado, creado, vence.
    .subiendo-* temporal de una subida en curso (api/upload.py).
    .tmp-*      temporal de una escritura atómica.

En la raíz solo quedan las carpetas (y el .tmp-arranque-* de un instante de
preparar_directorio).

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
hermana de owner_cleanup en el lifespan) recorre las carpetas, borra vencidos
y huérfanos, y borra la carpeta que quedó vacía (os.rmdir: atómico, falla si
algo entró). Una subida que preparó su carpeta justo antes de ese rmdir la
vuelve a crear al abrir su primer archivo (`_crear_exclusivo`): mientras una
subida vive, su temporal está adentro y la carpeta no está vacía.

BORRADO SEGURO CON LECTORES: se borra con unlink (primero el sidecar, después
el dato). Un lector que ya tiene el dato abierto lo sigue leyendo entero
(POSIX: el inodo vive hasta el último close); uno que llega después ve el
sidecar ausente y recibe el 404 de siempre.

Todo el I/O de disco corre en asyncio.to_thread: las funciones síncronas de
acá las llama api/upload.py (y RD3) a través de to_thread; `obtener` y
`leer` ya lo hacen adentro.
"""
import asyncio
import base64
import contextlib
import errno
import hashlib
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
PATRON_ID = r"[A-Za-z0-9_-]{%d}" % LARGO_ID
_ID = re.compile(PATRON_ID)

SUFIJO_DATO = ".dato"
SUFIJO_SIDECAR = ".json"
PREFIJO_SUBIDA = ".subiendo-"
PREFIJO_ESCRITURA = ".tmp-"

# Un temporal o un dato sin sidecar más viejo que esto es basura de una
# subida que murió (proceso caído, cliente cancelado a mitad). Fix round 1
# (2026-09-17): el margen tiene que cubrir también la ESPERA EN COLA. El
# temporal `.subiendo-*` toma su mtime al terminar la copia, ANTES de esperar
# turno_de_subida (JAX_ADJUNTO_SUBIDAS_EN_PROCESO, 1 en producción), y
# Starlette no cancela el handler cuando el cliente se va: una subida puede
# esperar detrás de todas las anteriores. Cada una tarda como mucho el
# timeout de pypdf (<= 60 s, LIMITE_TIMEOUT_DE_PDF_SEGUNDOS) más la
# clasificación de <= 10 MB (holgado: 30 s). 6 h cubren 240 subidas de peor
# caso en cola, muy por encima de la concurrencia real del servicio. El costo
# de un margen largo es solo disco: un huérfano no tiene sidecar, así que
# nadie puede leerlo, y es 0600. El .dato renombrado NO hereda la edad del
# temporal: guardar_imagen le refresca el mtime antes del rename. No es env:
# es un margen técnico sobre esos límites, no una política.
ORFANO_MAX_SEGUNDOS = 6 * 3600

# Cada 15 minutos. owner_cleanup corre cada 6 h porque retiene 30 días; acá
# el TTL mínimo es 1 h, y a 6 h un adjunto vencido quedaría en disco hasta 7
# veces su vida. La pasada es un scandir + un json chico por adjunto.
INTERVALO_DE_LIMPIEZA_SEGUNDOS = 15 * 60

_TAMANO_DE_BLOQUE = 1024 * 1024

# RD3 (2026-09-17): la imagen se codifica a base64 de a tramos de 262.143
# bytes (múltiplo de 3: cada tramo codifica sin relleno y la concatenación es
# el base64 del archivo entero). binascii retiene el GIL durante cada
# llamada; medido en hall9000 (loop con un tic de 1 ms, 25 imágenes de 10 MB,
# de a una por vez): de a 10 MB enteros el tic se atrasa p95 9,1 ms; de a
# 768 KB, 1,4 ms; de a 256 KB, 0,35 ms. Los tramos van tal cual al cuerpo
# del proveedor (http_client.LiteralJsonCrudo): nunca se juntan en un solo
# buffer ni en un str.
TRAMO_DE_BASE64 = 3 * 87_381

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
    valor = int(crudo) if crudo is not None and re.fullmatch(r"[1-9][0-9]{0,3}", crudo) else None
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


# ------------------------------------------------------------- carpetas

_USER_ID = re.compile(r"[1-9][0-9]{0,19}")


class UsuarioInvalido(ValueError):
    """user_id con otro formato que el de jax_users (entero positivo, sin
    ceros a la izquierda). Viene del JWT firmado, así que no debería pasar:
    si pasa, no se arma una ruta con él."""


def user_id_valido(valor) -> bool:
    return isinstance(valor, str) and _USER_ID.fullmatch(valor) is not None


def _nombre_de_carpeta(user_id) -> str:
    if not user_id_valido(user_id):
        raise UsuarioInvalido("user_id con formato inválido para el almacén de adjuntos")
    return user_id


def carpeta_de_usuario(directorio: Path, user_id) -> Path:
    """Ruta (sin tocar el disco) de la carpeta de `user_id`."""
    return directorio / _nombre_de_carpeta(user_id)


def _exigir_carpeta_real(directorio: Path, carpeta: Path) -> None:
    """La carpeta tiene que ser un directorio de verdad (no un symlink) y
    colgar directamente de la raíz."""
    info = os.lstat(carpeta)
    if not stat.S_ISDIR(info.st_mode) or carpeta.resolve().parent != directorio.resolve():
        raise NotADirectoryError(errno.ENOTDIR, "la carpeta de adjuntos del usuario no es un directorio propio")


def _crear_carpeta_si_falta(carpeta: Path) -> None:
    """mkdir 0700 idempotente. Que ya exista (otra subida la creó primero)
    no es un error: quien llama verifica después que sea un directorio propio."""
    # os.mkdir y no makedirs: si faltara la raíz, makedirs la recrearía con
    # el umask (no 0700); mkdir falla y la subida también (fail-closed).
    with contextlib.suppress(FileExistsError):
        os.mkdir(carpeta, 0o700)


def preparar_carpeta(directorio: Path, user_id) -> Path:
    """Síncrona (to_thread). Crea la carpeta 0700 si falta y comprueba que es
    un directorio propio. Devuelve su ruta."""
    carpeta = carpeta_de_usuario(directorio, user_id)
    _crear_carpeta_si_falta(carpeta)
    _exigir_carpeta_real(directorio, carpeta)
    return carpeta


_REINTENTOS_DE_CARPETA = 3


def _crear_exclusivo(ruta: Path) -> int:
    """os.open O_EXCL 0600. Si la carpeta desapareció (el limpiador la borró
    vacía entre preparar_carpeta y este open), la recrea 0700 y reintenta."""
    for intento in range(_REINTENTOS_DE_CARPETA):
        try:
            return os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileNotFoundError:
            if intento == _REINTENTOS_DE_CARPETA - 1:
                raise
            _crear_carpeta_si_falta(ruta.parent)
            _exigir_carpeta_real(ruta.parent.parent, ruta.parent)
    raise AssertionError("inalcanzable")


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


def _ruta(carpeta: Path, id_: str, sufijo: str) -> Path:
    """Solo con un id ya validado y una carpeta de usuario ya validada.
    Resuelve y exige quedar dentro de ESA carpeta, y que la carpeta cuelgue
    de la raíz: con los alfabetos de arriba no puede salir, salvo por un
    symlink plantado (el archivo o la carpeta) -- y eso también es "no
    existe"."""
    ruta = carpeta / f"{id_}{sufijo}"
    esperada = carpeta.parent.resolve() / carpeta.name
    if ruta.resolve().parent != esperada or carpeta.resolve() != esperada:
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
    fd = _crear_exclusivo(temporal)
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
    """Escribe el sidecar (el commit) en `directorio` (la carpeta del
    usuario). Si falla, se lleva el dato consigo."""
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
    fd = _crear_exclusivo(destino)
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
    subida, dentro de la carpeta del usuario): fsync y se renombra al dato,
    sin copiarla otra vez. `directorio` es la raíz."""
    carpeta = preparar_carpeta(directorio, user.user_id)
    with open(temporal, "rb") as f:
        os.fsync(f.fileno())
    # El mtime del temporal es el del fin de la copia, antes de la cola: sin
    # esto, el dato recién renombrado (aún sin sidecar) podría parecerle
    # huérfano viejo al limpiador (ver ORFANO_MAX_SEGUNDOS).
    os.utime(temporal)
    id_ = nuevo_id()
    os.replace(temporal, carpeta / f"{id_}{SUFIJO_DATO}")
    return _confirmar(carpeta, _metadatos(id_, user, "imagen", nombre, bytes_, ttl_horas, ahora,
                                          mime=mime))


def guardar_texto(directorio: Path, texto: str, *, user, origen: str, nombre: str, bytes_: int,
                  recortado: bool, ttl_horas: int, ahora: datetime | None = None) -> dict:
    """Síncrona (to_thread). `texto` ya viene recortado a max_chars; `bytes_`
    es el tamaño del archivo que subió el usuario, no el del texto.
    `directorio` es la raíz."""
    carpeta = preparar_carpeta(directorio, user.user_id)
    id_ = nuevo_id()
    _escribir_atomico(carpeta, carpeta / f"{id_}{SUFIJO_DATO}", texto.encode("utf-8"))
    return _confirmar(carpeta, _metadatos(
        id_, user, "texto", nombre, bytes_, ttl_horas, ahora, origen=origen,
        mime="text/plain", caracteres=len(texto), recortado=recortado))


# ------------------------------------------------------------------- lectura

def _vencido(meta: dict, ahora: datetime) -> bool:
    try:
        vence = datetime.fromisoformat(meta["vence"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError):
        return True
    return vence <= ahora


def _carpeta_para_leer(directorio: Path, user) -> Path:
    """La carpeta del que pide. Un user_id malformado no arma ruta: es el
    mismo "no existe"."""
    try:
        return carpeta_de_usuario(directorio, user.user_id)
    except UsuarioInvalido:
        raise AdjuntoNoEncontrado() from None


def _cargar_sidecar(directorio: Path, id_: str, user, ahora: datetime) -> dict:
    ruta = _ruta(_carpeta_para_leer(directorio, user), id_, SUFIJO_SIDECAR)
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
        with open(_ruta(_carpeta_para_leer(directorio, user), id_, SUFIJO_DATO), "rb") as f:
            return meta, f.read()
    except OSError:
        raise AdjuntoNoEncontrado() from None


def _leer_imagen_en_base64(directorio: Path, id_: str, user, ahora: datetime) -> tuple[dict, tuple[bytes, ...]]:
    """Síncrona (to_thread). Lee el dato de una IMAGEN de a TRAMO_DE_BASE64
    bytes y codifica cada tramo: en memoria hay un tramo crudo a la vez más
    el base64 que se va juntando (~4/3 del archivo), nunca el archivo crudo
    entero. Un adjunto que no es imagen, o cuyo dato no mide lo que dice el
    sidecar (truncado, reemplazado), es el mismo AdjuntoNoEncontrado."""
    meta = _cargar_sidecar(directorio, id_, user, ahora)
    esperado = meta.get("bytes")
    if meta.get("tipo") != "imagen" or not isinstance(esperado, int):
        raise AdjuntoNoEncontrado()
    tramos: list[bytes] = []
    total = 0
    try:
        with open(_ruta(_carpeta_para_leer(directorio, user), id_, SUFIJO_DATO), "rb") as f:
            while total <= esperado and (crudo := f.read(TRAMO_DE_BASE64)):
                total += len(crudo)
                tramos.append(base64.b64encode(crudo))
    except OSError:
        raise AdjuntoNoEncontrado() from None
    if total != esperado:
        raise AdjuntoNoEncontrado()
    return meta, tuple(tramos)


def uso_de_usuario(directorio: Path, user_id: str, ahora: datetime | None = None) -> int:
    """Síncrona (to_thread). Suma de `bytes` de los adjuntos VIGENTES de
    `user_id` (RD6, adjuntos/cuota.py). Vigente = lo que `obtener` le
    devolvería a su dueño: sidecar legible, suyo y sin vencer. Un sidecar
    ilegible o corrupto no es de nadie para `obtener` (404) y tampoco cuenta
    acá; su disco lo cubre la guarda global de disco libre y lo borra
    `limpiar` por edad. Por user_id solo, como `borrar_de_usuario`.
    RD7: lista SOLO la carpeta del usuario, nunca la raíz ni la de otro."""
    ahora = ahora or _ahora()
    carpeta = carpeta_de_usuario(directorio, user_id)
    total = 0
    try:
        entradas = _listar(carpeta)
    except FileNotFoundError:
        return 0  # sin carpeta: nunca subió nada (o el limpiador la vació)
    for entrada in entradas:
        nombre = entrada.name
        if not (nombre.endswith(SUFIJO_SIDECAR) and id_valido(nombre[:-len(SUFIJO_SIDECAR)])):
            continue
        try:
            meta = json.loads(Path(entrada.path).read_bytes())
        except (OSError, ValueError):
            continue  # borrado entre medio, ilegible o corrupto: no es vigente para nadie
        if (isinstance(meta, dict) and meta.get("user_id") == str(user_id)
                and isinstance(meta.get("bytes"), int) and not _vencido(meta, ahora)):
            total += meta["bytes"]
    return total


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


async def leer_imagen_en_base64(id_, user, *, ahora: datetime | None = None) -> tuple[dict, tuple[bytes, ...]]:
    """Metadatos y base64 por tramos de una imagen del `user` (RD3: el chat
    la manda al proveedor). Mismas reglas de dueño y vencimiento que `leer`;
    el tope de cuántas se codifican a la vez lo pone el llamador
    (adjuntos/turno.py::turno_de_imagen)."""
    if not id_valido(id_):
        raise AdjuntoNoEncontrado()
    return await asyncio.to_thread(_leer_imagen_en_base64, cargar_directorio(), id_, user, ahora or _ahora())


# ------------------------------------------------------------------- limpieza

def _para_log(nombre: str) -> str:
    """Nombre de una entrada apto para el log (RD3). El id es, para su dueño,
    la llave de su adjunto: al log va una huella corta (12 hex de sha256) que
    permite correlacionar líneas sin poder pedir el adjunto con ella."""
    for sufijo in (SUFIJO_SIDECAR, SUFIJO_DATO):
        if nombre.endswith(sufijo) and id_valido(nombre[:-len(sufijo)]):
            huella = hashlib.sha256(nombre[:-len(sufijo)].encode()).hexdigest()[:12]
            return f"id#{huella}{sufijo}"
    if nombre.startswith((PREFIJO_SUBIDA, PREFIJO_ESCRITURA)):
        return nombre
    return f"entrada#{hashlib.sha256(nombre.encode()).hexdigest()[:12]}"


def _borrar(ruta: Path) -> int:
    try:
        ruta.unlink()
        return 1
    except FileNotFoundError:
        return 0


def _borrar_adjunto(directorio: Path, id_: str) -> int:
    # Sidecar primero: desde ese instante nadie nuevo lo encuentra.
    return _borrar(directorio / f"{id_}{SUFIJO_SIDECAR}") + _borrar(directorio / f"{id_}{SUFIJO_DATO}")


def _listar(directorio: Path) -> list:
    """Foto del directorio (una sola lectura). Función propia para que un
    test fije el orden sin tocar os.scandir global."""
    with os.scandir(directorio) as it:
        return list(it)


def limpiar(directorio: Path, ahora: datetime | None = None) -> int:
    """Síncrona (to_thread). Recorre la raíz: cada carpeta de usuario se
    limpia (vencidos, sidecars corruptos viejos, datos sin sidecar viejos,
    temporales viejos) y, si quedó vacía, se borra. En la raíz solo se tocan
    temporales viejos. Deja todo lo que no reconoce. Una carpeta o entrada
    rota se saltea y se loguea: no aborta la pasada de los demás usuarios.
    Devuelve cuántos archivos borró."""
    ahora = ahora or _ahora()
    limite_orfano = time.time() - ORFANO_MAX_SEGUNDOS
    borrados = 0
    try:
        entradas = _listar(directorio)
    except FileNotFoundError:
        return 0
    nombres = {e.name for e in entradas}
    for entrada in entradas:
        nombre = entrada.name
        try:
            if user_id_valido(nombre) and entrada.is_dir(follow_symlinks=False):
                borrados += _limpiar_carpeta(Path(entrada.path), ahora, limite_orfano)
            elif nombre.startswith((PREFIJO_SUBIDA, PREFIJO_ESCRITURA)):
                borrados += _limpiar_entrada(directorio, entrada, nombres, ahora, limite_orfano)
        except OSError as e:  # fail-soft: una carpeta o entrada rota (sin permiso, EIO) no puede abortar la pasada para los demás usuarios; se loguea y el próximo ciclo la reintenta
            logger.warning("adjuntos: limpieza salteó %s (%s)", _para_log(nombre), type(e).__name__)
    if borrados:
        logger.info("adjuntos: limpieza borró %s archivo(s)", borrados)
    return borrados


def _limpiar_carpeta(carpeta: Path, ahora: datetime, limite_orfano: float) -> int:
    """Una carpeta de usuario. Si no se puede listar, el OSError sube a
    `limpiar`, que la saltea entera."""
    entradas = _listar(carpeta)
    nombres = {e.name for e in entradas}
    borrados = 0
    for entrada in entradas:
        try:
            borrados += _limpiar_entrada(carpeta, entrada, nombres, ahora, limite_orfano)
        except OSError as e:  # fail-soft: una entrada rota (directorio con nombre de adjunto, sin permiso, EIO) no aborta el resto de la carpeta; se loguea y el próximo ciclo la reintenta
            logger.warning("adjuntos: limpieza salteó %s (%s)", _para_log(entrada.name), type(e).__name__)
    _borrar_carpeta_si_vacia(carpeta)
    return borrados


def _borrar_carpeta_si_vacia(carpeta: Path) -> None:
    """os.rmdir es atómico y falla si hay algo adentro: nunca borra la
    carpeta de una subida que ya abrió su temporal. La que la preparó y
    todavía no abrió nada la recrea (`_crear_exclusivo`)."""
    try:
        os.rmdir(carpeta)
    except OSError:  # fail-soft: no vacía (ENOTEMPTY/EEXIST) o ya borrada (ENOENT); en los dos casos no hay nada que hacer
        return


def _limpiar_entrada(directorio: Path, entrada, nombres: set, ahora: datetime, limite_orfano: float) -> int:
    """Una entrada de `limpiar`. Cualquier OSError que no sea "ya no está"
    sube al bucle, que la saltea y sigue con las demás."""
    nombre = entrada.name
    try:
        viejo = entrada.stat(follow_symlinks=False).st_mtime < limite_orfano
        if nombre.startswith((PREFIJO_SUBIDA, PREFIJO_ESCRITURA)):
            return _borrar(Path(entrada.path)) if viejo else 0
        if nombre.endswith(SUFIJO_SIDECAR) and id_valido(nombre[:-len(SUFIJO_SIDECAR)]):
            try:
                meta = json.loads(Path(entrada.path).read_bytes())
                corrupto = not isinstance(meta, dict)
            except ValueError:
                corrupto = True
            if (corrupto and viejo) or (not corrupto and _vencido(meta, ahora)):
                return _borrar_adjunto(directorio, nombre[:-len(SUFIJO_SIDECAR)])
            return 0
        if nombre.endswith(SUFIJO_DATO) and id_valido(nombre[:-len(SUFIJO_DATO)]):
            sidecar = f"{nombre[:-len(SUFIJO_DATO)]}{SUFIJO_SIDECAR}"
            # Dos guardas: la foto de scandir y el disco AHORA (un sidecar
            # escrito después de la foto salva al dato).
            if viejo and sidecar not in nombres and not (directorio / sidecar).exists():
                return _borrar(Path(entrada.path))
        return 0
    except FileNotFoundError:  # fail-soft: carrera benigna con otra limpieza o un borrado concurrente; el archivo ya no está, que es lo que se buscaba
        return 0


def borrar_de_usuario(directorio: Path, user_id: str) -> int:
    """Síncrona (to_thread). Borra todos los adjuntos de `user_id` (la baja,
    api/admin/users.py): su carpeta entera -- adjuntos (sidecar primero),
    datos sueltos y temporales -- y después la carpeta. Por user_id solo: es
    la PK global de jax_users. No lista ni abre la carpeta de nadie más."""
    carpeta = carpeta_de_usuario(directorio, user_id)
    borrados = 0
    try:
        entradas = _listar(carpeta)
    except FileNotFoundError:
        return 0
    # Primero los adjuntos por sidecar (sidecar antes que dato: desde ese
    # instante nadie nuevo lo encuentra); después lo suelto.
    orden = sorted(entradas, key=lambda e: not e.name.endswith(SUFIJO_SIDECAR))
    for entrada in orden:
        nombre = entrada.name
        try:
            if nombre.endswith(SUFIJO_SIDECAR) and id_valido(nombre[:-len(SUFIJO_SIDECAR)]):
                borrados += _borrar_adjunto(carpeta, nombre[:-len(SUFIJO_SIDECAR)])
            elif (nombre.startswith((PREFIJO_SUBIDA, PREFIJO_ESCRITURA))
                  or (nombre.endswith(SUFIJO_DATO) and id_valido(nombre[:-len(SUFIJO_DATO)]))):
                borrados += _borrar(Path(entrada.path))
        except OSError as e:  # fail-soft: un archivo que no se deja borrar no frena el borrado del resto; su sidecar ya no está o vence por TTL, y se loguea
            logger.warning("adjuntos: baja de %s no pudo borrar %s (%s)", user_id, _para_log(nombre), type(e).__name__)
    _borrar_carpeta_si_vacia(carpeta)
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
