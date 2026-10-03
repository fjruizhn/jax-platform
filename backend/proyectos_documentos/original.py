"""Ubicar el original de un documento en `proyectos/<uuid>/fuente/` (reprocesar).

La ingesta de jax deja el original en `fuente/` (`procesamiento/ingesta.py::ingerir`) y
LAS MANOS procesa en el lugar lo que ya esta ahi (`_origen_ya_en_fuente`), asi que
reprocesar no copia nada: solo hace falta la ruta. Se busca por dos caminos, y en los dos
el archivo tiene que tener EL MISMO sha256 que la fila antes de devolverse:

  1. La ficha: `<carpeta_procesado>/ficha.json` trae `origen = "fuente/<ruta>"`.
  2. Si no hay ficha usable (o su archivo no coincide), un recorrido de `fuente/`: gana el
     archivo que se llama igual que `nombre_original`; si no, cualquiera con ese sha256.

Disciplina de rutas, la de `almacen.py`: todo cuelga de descriptores abiertos nivel por nivel
desde el workspace con `openat(O_DIRECTORY | O_NOFOLLOW)`, y el archivo se abre con
`O_NOFOLLOW`. Un enlace simbolico en cualquier nivel -- la carpeta `fuente/`, una subcarpeta o
el archivo -- no se sigue: ese candidato no existe. El recorrido esta acotado (`TOPE_ENTRADAS`)
y la ficha tambien (`FICHA_MAX_BYTES`). Es sincrona: se llama desde `asyncio.to_thread`.
La ruta devuelta es relativa al workspace, `proyectos/<uuid>/fuente/<ruta>`.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from pathlib import Path

TAMANO_DE_BLOQUE = 1024 * 1024
FICHA_MAX_BYTES = 1024 * 1024
# Entradas (archivos y carpetas) que el recorrido de `fuente/` mira como maximo.
TOPE_ENTRADAS = 100_000
# Niveles de carpetas bajo `fuente/` que el recorrido baja como maximo. Es tambien el maximo de
# descriptores que el recorrido tiene abiertos a la vez (uno por nivel del camino actual).
TOPE_PROFUNDIDAD = 64
# Tope del largo de `project_documents.ruta_entrada` (VARCHAR(1024)).
RUTA_MAX = 1024
_ABRIR_CARPETA = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
# O_NONBLOCK: abrir un FIFO plantado en `fuente/` no se queda esperando; luego se exige archivo comun.
_ABRIR_ARCHIVO = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class FuenteIlegible(Exception):
    """No se pudo mirar `fuente/` hasta el final: un error de E/S, de permisos o de descriptores, o
    mas profundidad o mas entradas que el tope. NO es «no encontrado»: responder eso seria mentir
    sobre un original que quiza esta ahi. La API lo responde 503 `fuente_ilegible`."""


# Lo que significa «eso no esta (o es un enlace, que nunca se sigue)»; todo otro OSError es
# `FuenteIlegible`. ELOOP/ENOTDIR: un enlace donde se esperaba una carpeta o un archivo.
_AUSENTE = (errno.ENOENT, errno.ELOOP, errno.ENOTDIR, errno.ENXIO, errno.EISDIR)


def _componente_valido(parte: str) -> bool:
    return parte not in ("", ".", "..") and "/" not in parte and "\x00" not in parte


def _abrir_ruta(raiz: int, partes: list[str], *, bandera_final: int) -> int | None:
    """fd de `partes` relativo a `raiz`, sin seguir ningun enlace; None si no existe o algun
    nivel es un enlace / no es lo que debe ser. El que llama cierra el fd devuelto."""
    abiertos: list[int] = []
    try:
        actual = raiz
        for parte in partes[:-1]:
            actual = os.open(parte, _ABRIR_CARPETA, dir_fd=actual)
            abiertos.append(actual)
        return os.open(partes[-1], bandera_final, dir_fd=actual)
    except OSError as exc:
        if exc.errno in _AUSENTE:
            return None
        raise
    finally:
        for fd in abiertos:
            os.close(fd)


def _sha256_de(fd_fuente: int, partes: list[str], *, bytes_: int | None) -> str | None:
    """sha256 del archivo comun en `partes` (None si no existe, no es comun o su tamano no es
    `bytes_`). Lee con el mismo descriptor que valido: sin ventana entre comprobar y leer."""
    fd = _abrir_ruta(fd_fuente, partes, bandera_final=_ABRIR_ARCHIVO)
    if fd is None:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (bytes_ is not None and info.st_size != bytes_):
            return None
        resumen = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=False) as f:
            while bloque := f.read(TAMANO_DE_BLOQUE):
                resumen.update(bloque)
        return resumen.hexdigest()
    finally:
        os.close(fd)


def _ficha_de(workspace_fd: int, project_uuid: str, carpeta_procesado: str | None) -> dict | None:
    """El `ficha.json` de `carpeta_procesado` (que tiene que ser `proyectos/<uuid>/procesado/...`
    de ESTE proyecto) como dict, o None: carpeta ajena, sin ficha, enlace, mayor que
    `FICHA_MAX_BYTES`, o JSON que no es un objeto. Una ficha ilegible no es un error: es que no hay."""
    if not isinstance(carpeta_procesado, str):
        return None
    partes = carpeta_procesado.split("/")
    if (len(partes) < 4 or partes[:3] != ["proyectos", project_uuid, "procesado"]
            or not all(_componente_valido(p) for p in partes)):
        return None
    fd = _abrir_ruta(workspace_fd, [*partes, "ficha.json"], bandera_final=_ABRIR_ARCHIVO)
    if fd is None:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            crudo = f.read(FICHA_MAX_BYTES + 1)
        if len(crudo) > FICHA_MAX_BYTES:
            return None
        ficha = json.loads(crudo.decode("utf-8"))
    except (ValueError, OSError):  # fail-soft: ficha corrupta o ilegible; sin ficha se sigue sin ella
        return None
    finally:
        os.close(fd)
    return ficha if isinstance(ficha, dict) else None


def leer_ficha(workspace: Path, project_uuid: str, carpeta_procesado: str | None) -> dict | None:
    """Sincrona (to_thread). Ver `_ficha_de`: la usa el despachador para el motivo de un error."""
    if not _componente_valido(project_uuid):
        return None
    raiz = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return _ficha_de(raiz, project_uuid, carpeta_procesado)
    finally:
        os.close(raiz)


def _origen_de_la_ficha(workspace_fd: int, project_uuid: str, carpeta_procesado: str | None) -> list[str] | None:
    """Los componentes de `origen` (sin el `fuente/` inicial) si la ficha lo declara bien."""
    ficha = _ficha_de(workspace_fd, project_uuid, carpeta_procesado)
    origen = ficha.get("origen") if ficha else None
    if not isinstance(origen, str):
        return None
    trozos = origen.split("/")
    if len(trozos) < 2 or trozos[0] != "fuente" or not all(_componente_valido(p) for p in trozos[1:]):
        return None
    return trozos[1:]


def _recorrer(fd_fuente: int) -> list[list[str]]:
    """Rutas (como componentes, relativas a `fuente/`) de los archivos comunes, en orden de nombre y
    sin seguir enlaces. En PROFUNDIDAD y con un solo descriptor abierto por nivel del camino actual:
    se abre la carpeta, se listan sus nombres, se cierra y recien despues se baja a cada
    subcarpeta, reabriendola desde su padre con `O_NOFOLLOW`. Lo que desaparece a mitad del
    recorrido (ENOENT, ENOTDIR, ELOOP) se salta; cualquier otro OSError, pasar `TOPE_PROFUNDIDAD` o
    `TOPE_ENTRADAS` es `FuenteIlegible`."""
    encontrados: list[list[str]] = []
    vistas = 0

    def listar(fd: int) -> tuple[list[str], list[str]]:
        nonlocal vistas
        archivos: list[str] = []
        carpetas: list[str] = []
        entradas = []
        with os.scandir(fd) as it:
            for entrada in it:
                # Se cuenta MIENTRAS se itera, antes de ordenar: una carpeta enorme no se carga entera.
                vistas += 1
                if vistas > TOPE_ENTRADAS:
                    raise FuenteIlegible(f"mas de {TOPE_ENTRADAS} entradas en fuente/")
                entradas.append(entrada)
        entradas.sort(key=lambda x: x.name)
        for entrada in entradas:
            if not _componente_valido(entrada.name) or entrada.is_symlink():
                continue
            if entrada.is_file(follow_symlinks=False):
                archivos.append(entrada.name)
            elif entrada.is_dir(follow_symlinks=False):
                carpetas.append(entrada.name)
        return archivos, carpetas

    def bajar(fd: int, prefijo: list[str]) -> None:
        if len(prefijo) > TOPE_PROFUNDIDAD:
            raise FuenteIlegible(f"mas de {TOPE_PROFUNDIDAD} niveles de carpetas en fuente/")
        archivos, carpetas = listar(fd)
        encontrados.extend([*prefijo, nombre] for nombre in archivos)
        for nombre in carpetas:
            try:
                hijo = os.open(nombre, _ABRIR_CARPETA, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                    continue                    # desaparecio (o se volvio un enlace) a mitad del recorrido
                raise
            try:
                bajar(hijo, [*prefijo, nombre])
            finally:
                os.close(hijo)

    try:
        bajar(fd_fuente, [])
    except OSError as exc:
        raise FuenteIlegible(f"no se pudo recorrer fuente/ ({errno.errorcode.get(exc.errno, exc.errno)})") from None
    return encontrados


def buscar_original(workspace: Path, project_uuid: str, *, sha256: str, nombre_original: str,
                    carpeta_procesado: str | None, bytes_: int | None = None) -> str | None:
    """`proyectos/<uuid>/fuente/<ruta>` del archivo con ese sha256, o None. Sincrona (to_thread).
    `FuenteIlegible` si no se pudo mirar `fuente/` (E/S, permisos, descriptores, topes): no es None.
    `project_uuid` tiene que ser un componente simple (viene de `projects.project_uuid`).
    `bytes_` solo evita leer los archivos de otro tamano; la prueba es siempre el sha256."""
    if not _componente_valido(project_uuid):
        return None
    try:
        return _buscar(workspace, project_uuid, sha256, nombre_original, carpeta_procesado, bytes_)
    except OSError as exc:
        raise FuenteIlegible(f"no se pudo leer fuente/ ({errno.errorcode.get(exc.errno, exc.errno)})") from None


def _buscar(workspace: Path, project_uuid: str, sha256: str, nombre_original: str,
            carpeta_procesado: str | None, bytes_: int | None) -> str | None:
    raiz = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fuente = _abrir_ruta(raiz, ["proyectos", project_uuid, "fuente"], bandera_final=_ABRIR_CARPETA)
        if fuente is None:
            return None
        try:
            def completa(partes: list[str]) -> str | None:
                ruta = "/".join(["proyectos", project_uuid, "fuente", *partes])
                return ruta if len(ruta) <= RUTA_MAX else None

            partes = _origen_de_la_ficha(raiz, project_uuid, carpeta_procesado)
            if partes is not None and _sha256_de(fuente, partes, bytes_=bytes_) == sha256:
                return completa(partes)

            candidatos = _recorrer(fuente)
            nombre = nombre_original.rsplit("/", 1)[-1]
            candidatos.sort(key=lambda p: p[-1] != nombre)       # estable: los que se llaman igual, primero
            for candidato in candidatos:
                if _sha256_de(fuente, candidato, bytes_=bytes_) == sha256:
                    ruta = completa(candidato)
                    if ruta is not None:
                        return ruta
            return None
        finally:
            os.close(fuente)
    finally:
        os.close(raiz)
