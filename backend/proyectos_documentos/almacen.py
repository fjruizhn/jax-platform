"""Escritura de documentos de un proyecto en `entrada/` (E2a, T6).

Todo archivo que sube un cliente cae en
`<JAX_WORKSPACE_DIR>/proyectos/<project_uuid>/entrada/<lote>/<nombre_seguro>` y en
ningun otro lugar: ni bajo `fuente/` ni fuera de la carpeta del lote. Tres guardas
lo hacen cumplir, ninguna depende de otra:

  - `carpeta_entrada` solo arma rutas con componentes de un alfabeto cerrado
    (letras, digitos, `-` y `_`): un `..` o un separador no llegan a la ruta.
  - `nombre_seguro` deja el nombre del cliente en UN componente, sin separadores,
    sin controles y de largo acotado.
  - `abrir_carpeta_lote` recorre la ruta desde el workspace nivel por nivel con
    `openat(O_DIRECTORY | O_NOFOLLOW)` y crea lo que falta con `mkdirat`; el archivo
    se abre relativo al descriptor del lote con `O_CREAT | O_EXCL | O_NOFOLLOW`.
    Un enlace simbolico en cualquier nivel es `RutaInsegura`, y como todo cuelga de
    descriptores ya abiertos no hay ventana entre comprobar y escribir (TOCTOU).

El resto es escritura en streaming: bloques de 1 MiB en `asyncio.to_thread` (como
`adjuntos/almacen.copiar_subida`), con el sha256 calculado mientras se escribe y
el tope medido sobre lo REALMENTE leido, nunca sobre un tamano declarado.
"""
from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import re
import threading
import unicodedata
from pathlib import Path

VARIABLE_WORKSPACE = "JAX_WORKSPACE_DIR"
TAMANO_DE_BLOQUE = 1024 * 1024

# Modos de `entrada/`. Bajo `proyectos/` hay ACL por defecto (g:fruiz, u:jaxsvc) y
# setgid: un archivo creado 0600 deja la mascara de la ACL en `---` y anula el
# acceso de fruiz (por eso LAS MANOS hace fchmod 0660 en tool_authority._write_file).
# El modo se fija explicito con fchmod/chmod, nunca por el umask.
# `S_ISGID` (el 2 inicial) se pone EXPLICITO: un chmod 0770 lo borraria y las carpetas
# nuevas dejarian de heredar el grupo de `proyectos/`.
MODO_ARCHIVO = 0o660
MODO_CARPETA = 0o2770

# Largos. 200 caracteres Y 200 bytes: un nombre de 200 emojis son 800 bytes y el
# sistema de archivos corta en 255 bytes por componente (ENAMETOOLONG). El margen
# hasta 255 es para el " (N)" de los repetidos.
NOMBRE_MAX_CARACTERES = 200
NOMBRE_MAX_BYTES = 200
NOMBRE_ORIGINAL_MAX = 1024
EXTENSION_MAX = 20

_COMPONENTE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")
_SEPARADORES = re.compile(r"[\\/]")


class DemasiadoGrande(Exception):
    """El archivo cruzo el tope mientras se leia; el parcial ya se borro."""


class WorkspaceNoConfigurado(RuntimeError):
    """`JAX_WORKSPACE_DIR` ausente, relativo o sin carpeta: no se escribe nada."""


def cargar_workspace() -> Path:
    crudo = os.environ.get(VARIABLE_WORKSPACE, "").strip()
    if not crudo or not os.path.isabs(crudo):
        raise WorkspaceNoConfigurado(f"{VARIABLE_WORKSPACE} debe ser una ruta absoluta")
    ruta = Path(crudo)
    if not ruta.is_dir():
        raise WorkspaceNoConfigurado(f"{VARIABLE_WORKSPACE} no es una carpeta existente")
    return ruta


def carpeta_entrada(workspace: Path, project_uuid: str, lote: str) -> Path:
    """`<workspace>/proyectos/<uuid>/entrada/<lote>`. ValueError si el uuid o el
    lote no son un componente de ruta simple."""
    for nombre, valor in (("project_uuid", project_uuid), ("lote", lote)):
        if not isinstance(valor, str) or not _COMPONENTE.match(valor):
            raise ValueError(f"{nombre} no es un componente de ruta valido")
    return workspace / "proyectos" / project_uuid / "entrada" / lote


class RutaInsegura(Exception):
    """Un nivel de `proyectos/<uuid>/entrada/<lote>` es un enlace simbolico (o no es
    una carpeta). Nunca se sigue: no se escribe nada y se deshace lo creado."""


class CarpetaLote:
    """La carpeta del lote ya abierta, sin haber seguido ningun enlace. Todo lo que
    se hace dentro es relativo a `fd`. `cerrar()` siempre, al terminar."""

    def __init__(self, ruta: Path, fds: list[int], creados: list[tuple[int, str]], relativa: str):
        self.ruta = ruta                  # solo informativa (logs, mensajes de prueba)
        self._fds = fds
        self._creados = creados           # (fd del padre, nombre) de lo que creo ESTA llamada
        self._relativa = relativa

    @property
    def fd(self) -> int:
        return self._fds[-1]

    def ruta_relativa(self, nombre: str) -> str:
        """La ruta que se guarda en la base y recibe LAS MANOS (relativa al workspace)."""
        return f"{self._relativa}/{nombre}"

    def borrar(self, nombre: str) -> None:
        """Sincrona (to_thread). Idempotente; nunca sigue un enlace."""
        try:
            os.unlink(nombre, dir_fd=self.fd)
        except FileNotFoundError:  # fail-soft: ya no esta; borrar es idempotente
            pass

    def quitar_lote_si_vacio(self) -> None:
        """Sincrona (to_thread). Solo la carpeta del lote, que es de esta subida: los
        niveles de arriba son estructura compartida con otras subidas."""
        try:
            os.rmdir(self._relativa.rsplit("/", 1)[-1], dir_fd=self._fds[-2])
        except OSError:  # fail-soft: no esta vacia o ya no esta; es limpieza, no un resultado
            pass

    def deshacer(self) -> None:
        """Quita lo que esta llamada creo, del mas hondo al mas alto, si esta vacio."""
        for padre, nombre in reversed(self._creados):
            try:
                os.rmdir(nombre, dir_fd=padre)
            except OSError:  # fail-soft: otra subida ya puso algo adentro; no es nuestro
                pass

    def cerrar(self) -> None:
        for fd in self._fds:
            try:
                os.close(fd)
            except OSError:  # fail-soft: ya cerrado; cerrar no puede fallar la subida
                pass
        self._fds = []


def abrir_carpeta_lote(workspace: Path, project_uuid: str, lote: str) -> CarpetaLote:
    """Sincrona (to_thread). Abre `proyectos/<uuid>/entrada/<lote>` desde el workspace,
    un nivel a la vez con `openat(O_DIRECTORY|O_NOFOLLOW)`, creando con `mkdirat` lo que
    falte (modo explicito 0o2770 por `fchmod`, sin depender del umask; solo en lo que se
    crea aqui). RutaInsegura si algun nivel es un enlace; en ese caso (y en cualquier
    otro error) no queda nada creado ni nada abierto. ValueError si uuid o lote no son
    un componente simple."""
    ruta = carpeta_entrada(workspace, project_uuid, lote)
    niveles = ("proyectos", project_uuid, "entrada", lote)
    fds = [os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)]
    creados: list[tuple[int, str]] = []
    try:
        for nombre in niveles:
            padre = fds[-1]
            creado = False
            try:
                os.mkdir(nombre, MODO_CARPETA, dir_fd=padre)
                creado = True
            except FileExistsError:  # fail-soft: el nivel ya existe; el open siguiente decide si es una carpeta real
                pass
            try:
                hijo = os.open(nombre, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=padre)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise RutaInsegura(f"{nombre!r} no es una carpeta real (enlace?) bajo {workspace}") from None
                raise
            if creado:
                creados.append((padre, nombre))
                os.fchmod(hijo, MODO_CARPETA)
            fds.append(hijo)
    except BaseException:
        CarpetaLote(ruta, fds, creados, "/".join(niveles)).deshacer()
        for fd in fds:
            os.close(fd)
        raise
    return CarpetaLote(ruta, fds, creados, "/".join(niveles))


def _limpiar(nombre: str) -> str:
    """NFC, un solo componente (lo que va despues del ultimo separador, `/` o `\\`,
    asi `../../x.pdf` es `x.pdf`) y sin ningun caracter de la categoria C
    (controles, `\\x00`, de formato como el cero-ancho y el override de
    derecha a izquierda, sustitutos y no asignados)."""
    nombre = unicodedata.normalize("NFC", nombre or "")
    nombre = _SEPARADORES.split(nombre)[-1]
    return "".join(c for c in nombre if not unicodedata.category(c).startswith("C")).strip()


def nombre_para_mostrar(original: str | None) -> str:
    """El nombre ORIGINAL como texto (lo que guarda `nombre_original` y ve la lista):
    NFC, sin caracteres de control y a lo mas 1024. Si el cliente lo mando con
    carpeta (webkitRelativePath), conserva su ruta relativa. NO es el nombre del
    disco: ese es `nombre_seguro`."""
    nombre = unicodedata.normalize("NFC", original or "")
    return "".join(c for c in nombre if not unicodedata.category(c).startswith("C"))[:NOMBRE_ORIGINAL_MAX]


def nombre_seguro(original: str, usados: set[str]) -> str:
    """Nombre de archivo para el disco. Nunca vacio (`documento.<ext>`), a lo mas
    200 caracteres y 200 bytes conservando la extension, y distinto de todos los
    de `usados` sin distinguir mayusculas (agrega ` (2)`, ` (3)`...). No modifica
    `usados`: quien escribe el archivo lo agrega."""
    nombre = _limpiar(original)
    base, punto, extension = nombre.rpartition(".")
    if not punto:
        base, extension = nombre, ""
    extension = extension[:EXTENSION_MAX]
    sufijo = f".{extension}" if punto and extension else ""
    base = base.lstrip(". ")            # nada de ocultos (`.x`) ni de `..`
    base = base[:max(NOMBRE_MAX_CARACTERES - len(sufijo), 0)]
    while base and len((base + sufijo).encode()) > NOMBRE_MAX_BYTES:
        base = base[:-1]
    base = base.rstrip() or "documento"
    ocupados = {u.casefold() for u in usados}
    candidato, n = base + sufijo, 1
    while candidato.casefold() in ocupados:
        n += 1
        candidato = f"{base} ({n}){sufijo}"
    return candidato


class _Cancelado(Exception):
    """El hilo vio la bandera de cancelacion: borro lo que habia creado y se rinde."""


def _crear(fd_carpeta: int, nombre: str) -> int:
    return os.open(nombre, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, MODO_ARCHIVO,
                   dir_fd=fd_carpeta)


def _borrar(fd_carpeta: int, nombre: str) -> None:
    try:
        os.unlink(nombre, dir_fd=fd_carpeta)
    except FileNotFoundError:  # fail-soft: ya no esta; borrar es idempotente
        pass


def _escribir(archivo, fd_carpeta: int, nombre: str, tope: int, cancelado: threading.Event) -> tuple[int, str]:
    if cancelado.is_set():
        raise _Cancelado()
    fd = _crear(fd_carpeta, nombre)
    try:
        os.fchmod(fd, MODO_ARCHIVO)
    except BaseException:
        os.close(fd)
        _borrar(fd_carpeta, nombre)
        raise
    total, resumen = 0, hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as f:
            while bloque := archivo.read(TAMANO_DE_BLOQUE):
                if cancelado.is_set():
                    raise _Cancelado()
                total += len(bloque)
                if total > tope:
                    raise DemasiadoGrande()
                resumen.update(bloque)
                f.write(bloque)
            f.flush()
            os.fsync(f.fileno())
        if cancelado.is_set():              # tambien despues de abrir: una cancelacion que llego
            raise _Cancelado()              # antes del open no puede dejar el archivo
    except BaseException:
        _borrar(fd_carpeta, nombre)
        raise
    return total, resumen.hexdigest()


async def escribir_streaming(upload, carpeta: CarpetaLote, nombre: str, tope: int) -> tuple[int, str]:
    """Copia `upload` (un UploadFile de Starlette: se lee su `.file`) a `nombre`
    DENTRO de `carpeta` (0660, O_EXCL, relativo a su descriptor: nunca pisa un archivo
    ni sigue un enlace) y devuelve `(bytes, sha256)`. Corta en cuanto lo leido cruza
    `tope` -- no mira ningun tamano declarado --, borra el parcial y lanza
    `DemasiadoGrande`: nunca se escribe mas de `tope + 1 MiB`. Memoria: un bloque a la vez.

    Cancelacion: un hilo ya lanzado no se detiene solo. Se le avisa con una bandera que
    revisa antes de abrir, en cada bloque y antes de cerrar, y se ESPERA a que termine (aunque
    lleguen mas cancelaciones) antes de dejar salir la cancelacion; si el hilo alcanzo a
    terminar bien, el archivo se borra aqui. Asi no existe cuando el llamador sigue, haya
    llegado la cancelacion antes del `open`, a mitad o despues de la ultima mirada."""
    cancelado = threading.Event()
    hilo = asyncio.ensure_future(
        asyncio.to_thread(_escribir, upload.file, carpeta.fd, nombre, tope, cancelado))
    try:
        return await asyncio.shield(hilo)
    except asyncio.CancelledError:
        cancelado.set()
        # Esperar al hilo de forma BLINDADA: una segunda cancelacion no puede sacarnos de
        # la espera (el hilo seguiria escribiendo con el llamador ya adelante).
        while True:
            try:
                await asyncio.shield(hilo)
                break
            except asyncio.CancelledError:
                continue
            except Exception:  # fail-soft: _Cancelado o el error del hilo; ya limpio por el, y abajo se re-lanza la cancelacion
                break
        if not hilo.cancelled() and hilo.exception() is None:
            # La cancelacion llego despues de la ultima mirada del hilo a la bandera: termino
            # bien, el archivo esta en disco y nadie lo va a registrar.
            _borrar(carpeta.fd, nombre)
        raise
