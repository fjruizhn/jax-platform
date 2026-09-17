import asyncio
import binascii
import json
import mimetypes
import os
import weakref
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from auth.middleware import require_superadmin
from auth.models import AuthUser
from config_de_entorno import ruta_requerida

router = APIRouter(prefix="/api/admin")

REPO_BASE = ruta_requerida("JAX_REPO_BASE")
ALLOWED_FOLDERS = {"missions", "pipelines", "documents", "images"}
IMAGENES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def _carpeta(base: Path, nombre: str) -> Path:
    """La carpeta permitida, resuelta; 400 si un symlink la saca del repo."""
    carpeta = (base / nombre).resolve()
    if carpeta.parent != base or carpeta.name not in ALLOWED_FOLDERS:
        raise HTTPException(status_code=400, detail="ruta_invalida")
    return carpeta


def _dentro(destino: Path, carpeta: Path) -> bool:
    return destino != carpeta and destino.is_relative_to(carpeta)


def _resolve(path: str) -> Path:
    """La única validación de rutas (A-26). A-40 (2026-09-16): antes era
    `realpath(...).startswith(base)` sin separador, y `documents/../../repo-x/…`
    salía del repositorio. is_relative_to compara por componentes.
    Ronda final (2026-09-16): la contención es contra la CARPETA permitida, no
    contra la base (`documents/../privado/x` se leía y se borraba), y un NUL en
    la ruta es 400, no un ValueError sin atrapar (500)."""
    partes = path.replace("\\", "/").split("/", 1)
    if len(partes) < 2 or partes[0] not in ALLOWED_FOLDERS:
        raise HTTPException(status_code=400, detail="ruta_invalida")
    base = Path(REPO_BASE).resolve()
    try:
        carpeta = _carpeta(base, partes[0])
        destino = (carpeta / partes[1]).resolve()
    except ValueError:  # NUL en la ruta: os.lstat lo rechaza
        raise HTTPException(status_code=400, detail="ruta_invalida") from None
    if not _dentro(destino, carpeta):
        raise HTTPException(status_code=400, detail="ruta_invalida")
    if not destino.is_file():
        raise HTTPException(status_code=404, detail="archivo_no_encontrado")
    return destino


def _file_info(path: Path) -> dict:
    # La ruta propia de la entrada, no la de su destino: un symlink no expone
    # a dónde apunta. El stat sí sigue el enlace (tamaño y fecha reales).
    stat = path.stat()
    return {
        "name": path.name,
        "path": os.path.relpath(path, Path(REPO_BASE)),
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def _listar() -> dict:
    base = Path(REPO_BASE)
    base_real = base.resolve()
    result = {}
    for folder in ALLOWED_FOLDERS:
        (base / folder).mkdir(parents=True, exist_ok=True)
        try:
            carpeta = _carpeta(base_real, folder)
        except HTTPException:  # la carpeta misma sale del repo: no se lista nada
            result[folder] = []
            continue
        # Lo que resuelve fuera de su carpeta no se lista (lo mismo que _resolve rechaza).
        result[folder] = [_file_info(p) for p in sorted((base / folder).iterdir())
                          if p.is_file() and _dentro(p.resolve(), carpeta)]
    return {"folders": result}


def _json(valor) -> bytes:
    # Igual que el JSONResponse de FastAPI: el contrato del cuerpo no cambia.
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# Multiplo de 3: cada trozo codifica sin relleno y se concatena tal cual.
_TROZO_B64 = 3 * 64 * 1024


def _leer(destino: Path) -> bytes:
    """El cuerpo JSON YA ARMADO, en el hilo (Task 15 R12b, 2026-09-16): antes
    se devolvia un dict y FastAPI serializaba 2,7 MB en el loop. El base64 va
    en trozos para soltar el GIL entre uno y otro."""
    nombre = _json(destino.name)
    if destino.suffix.lower() in IMAGENES:
        mime = mimetypes.guess_type(destino.name)[0]
        datos = destino.read_bytes()
        b64 = b"".join(binascii.b2a_base64(datos[i:i + _TROZO_B64], newline=False)
                       for i in range(0, len(datos), _TROZO_B64))
        prefijo = _json(f"data:{mime};base64,")[:-1]  # sin la comilla de cierre
        return b'{"name":' + nombre + b',"type":"image","base64":' + prefijo + b64 + b'"}'
    contenido = destino.read_text(encoding="utf-8", errors="replace")
    tipo = "markdown" if destino.suffix.lower() == ".md" else "text"
    return _json({"name": destino.name, "type": tipo, "content": contenido})


# Task 15 R12b (2026-09-16): de a UNA lectura por loop. La carga I midio que
# 25 hilos leyendo y codificando un png de 2 MB hacen convoy con el loop por el
# GIL (p95 de /api/health 70-90 ms, reposo 0,6); con cupo 1 quedo en 2,7-3,9x
# el reposo y el endpoint rindio el doble (con cupo 2: 5,6-7,5x; con 4: 16-17x).
# Un Semaphore por loop: uno de modulo queda atado al primer loop que lo
# disputa. Sin invalidacion: vive y muere con su loop (WeakKeyDictionary).
_LECTURAS_SIMULTANEAS = 1
_cupos: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _cupo() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    cupo = _cupos.get(loop)
    if cupo is None:
        cupo = _cupos[loop] = asyncio.Semaphore(_LECTURAS_SIMULTANEAS)
    return cupo


# Disco en un hilo (LAS CUATRO, async).
@router.get("/repo")
async def list_repo(user: AuthUser = Depends(require_superadmin)):
    return await asyncio.to_thread(_listar)


@router.get("/repo/file")
async def get_file(path: str = Query(...), user: AuthUser = Depends(require_superadmin)):
    destino = await asyncio.to_thread(_resolve, path)  # fuera del cupo: un 400/404 no hace fila
    async with _cupo():
        cuerpo = await asyncio.to_thread(_leer, destino)
    return Response(cuerpo, media_type="application/json")


@router.delete("/repo/file")
async def delete_file(path: str = Query(...), user: AuthUser = Depends(require_superadmin)):
    destino = await asyncio.to_thread(_resolve, path)
    await asyncio.to_thread(destino.unlink)
    return {"ok": True}
