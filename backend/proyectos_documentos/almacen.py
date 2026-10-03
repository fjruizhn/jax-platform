"""Escritura de documentos de un proyecto en `entrada/` (E2a, T6).

Todo archivo que sube un cliente cae en
`<JAX_WORKSPACE_DIR>/proyectos/<project_uuid>/entrada/<lote>/<nombre_seguro>` y en
ningun otro lugar: ni bajo `fuente/` ni fuera de la carpeta del lote. Dos
guardas lo hacen cumplir, ninguna depende de la otra:

  - `carpeta_entrada` solo arma rutas con componentes de un alfabeto cerrado
    (letras, digitos, `-` y `_`): un `..` o un separador no llegan a la ruta.
  - `nombre_seguro` deja el nombre del cliente en UN componente, sin separadores,
    sin controles y de largo acotado; el archivo se abre `O_EXCL | O_NOFOLLOW`.

El resto es escritura en streaming: bloques de 1 MiB en `asyncio.to_thread` (como
`adjuntos/almacen.copiar_subida`), con el sha256 calculado mientras se escribe y
el tope medido sobre lo REALMENTE leido, nunca sobre un tamano declarado.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import unicodedata
from pathlib import Path

VARIABLE_WORKSPACE = "JAX_WORKSPACE_DIR"
TAMANO_DE_BLOQUE = 1024 * 1024

# Modos de `entrada/`. Bajo `proyectos/` hay ACL por defecto (g:fruiz, u:jaxsvc) y
# setgid: un archivo creado 0600 deja la mascara de la ACL en `---` y anula el
# acceso de fruiz (por eso LAS MANOS hace fchmod 0660 en tool_authority._write_file).
# El modo se fija explicito con fchmod/chmod, nunca por el umask.
MODO_ARCHIVO = 0o660
MODO_CARPETA = 0o770

# Largos. 200 caracteres Y 200 bytes: un nombre de 200 emojis son 800 bytes y el
# sistema de archivos corta en 255 bytes por componente (ENAMETOOLONG). El margen
# hasta 255 es para el " (N)" de los repetidos.
NOMBRE_MAX_CARACTERES = 200
NOMBRE_MAX_BYTES = 200
NOMBRE_PARA_MOSTRAR_MAX = 255
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


def preparar_carpeta(carpeta: Path, workspace: Path) -> None:
    """Sincrona (to_thread). Crea la carpeta del lote (y lo que falte de
    `proyectos/<uuid>/entrada`) con modo explicito 0770 -- chmod despues de crear,
    sin depender del umask -- y comprueba que, ya resuelta (symlinks incluidos),
    sigue dentro del workspace. Solo se fija el modo de lo que se crea aqui."""
    for nivel in reversed([carpeta, *carpeta.parents]):
        if nivel == workspace or workspace not in nivel.parents:
            continue
        try:
            nivel.mkdir(mode=MODO_CARPETA)
        except FileExistsError:
            continue
        os.chmod(nivel, MODO_CARPETA)
    if not carpeta.resolve().is_relative_to(workspace.resolve()):
        raise WorkspaceNoConfigurado("la carpeta de entrada resuelve fuera del workspace")


def _limpiar(nombre: str) -> str:
    """NFC, un solo componente (lo que va despues del ultimo separador, `/` o `\\`,
    asi `../../x.pdf` es `x.pdf`) y sin ningun caracter de la categoria C
    (controles, `\\x00`, de formato como el cero-ancho y el override de
    derecha a izquierda, sustitutos y no asignados)."""
    nombre = unicodedata.normalize("NFC", nombre or "")
    nombre = _SEPARADORES.split(nombre)[-1]
    return "".join(c for c in nombre if not unicodedata.category(c).startswith("C")).strip()


def nombre_para_mostrar(original: str | None) -> str:
    """El nombre tal cual lo vera la lista: limpio, pero sin renombrar por repetido."""
    return _limpiar(original or "")[:NOMBRE_PARA_MOSTRAR_MAX]


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


def _escribir(archivo, destino: Path, tope: int) -> tuple[int, str]:
    fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, MODO_ARCHIVO)
    try:
        os.fchmod(fd, MODO_ARCHIVO)
    except BaseException:
        os.close(fd)
        destino.unlink(missing_ok=True)
        raise
    total, resumen = 0, hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as f:
            while bloque := archivo.read(TAMANO_DE_BLOQUE):
                total += len(bloque)
                if total > tope:
                    raise DemasiadoGrande()
                resumen.update(bloque)
                f.write(bloque)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        destino.unlink(missing_ok=True)
        raise
    return total, resumen.hexdigest()


async def escribir_streaming(upload, destino: Path, tope: int) -> tuple[int, str]:
    """Copia `upload` (un UploadFile de Starlette: se lee su `.file`) a `destino`
    (0660, O_EXCL: nunca pisa un archivo existente) y devuelve `(bytes, sha256)`.
    Corta en cuanto lo leido cruza `tope` -- no mira ningun tamano declarado --,
    borra el parcial y lanza `DemasiadoGrande`: nunca se escribe mas de
    `tope + 1 MiB`. Memoria: un bloque a la vez."""
    try:
        return await asyncio.to_thread(_escribir, upload.file, destino, tope)
    except asyncio.CancelledError:
        # Una cancelacion no detiene el hilo ya lanzado; si el archivo ya existe se
        # borra aca (POSIX: el hilo sigue con el inodo y se libera al cerrarlo). Solo
        # la cancelacion: un FileExistsError es de OTRO archivo y no se toca.
        destino.unlink(missing_ok=True)
        raise
