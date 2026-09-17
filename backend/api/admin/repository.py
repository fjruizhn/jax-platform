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


def _resolve(path: str) -> Path:
    """La única validación de rutas (A-26). A-40 (2026-09-16): antes era
    `realpath(...).startswith(base)` sin separador, y `documents/../../repo-x/…`
    salía del repositorio. is_relative_to compara por componentes."""
    partes = path.replace("\\", "/").split("/", 1)
    if len(partes) < 2 or partes[0] not in ALLOWED_FOLDERS:
        raise HTTPException(status_code=400, detail="ruta_invalida")
    base = Path(REPO_BASE).resolve()
    destino = (base / partes[0] / partes[1]).resolve()
    if not destino.is_relative_to(base):
        raise HTTPException(status_code=400, detail="ruta_invalida")
    if not destino.is_file():
        raise HTTPException(status_code=404, detail="archivo_no_encontrado")
    return destino


def _file_info(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "path": os.path.relpath(path, Path(REPO_BASE).resolve()),
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def _listar() -> dict:
    base = Path(REPO_BASE)
    result = {}
    for folder in ALLOWED_FOLDERS:
        carpeta = base / folder
        carpeta.mkdir(parents=True, exist_ok=True)
        result[folder] = [_file_info(p.resolve()) for p in sorted(carpeta.iterdir()) if p.is_file()]
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
