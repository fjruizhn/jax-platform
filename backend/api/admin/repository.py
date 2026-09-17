import asyncio
import base64
import mimetypes
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.middleware import require_superadmin
from auth.models import AuthUser

router = APIRouter(prefix="/api/admin")

REPO_BASE = os.path.expanduser("~/jax/repo")
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


def _leer(destino: Path) -> dict:
    if destino.suffix.lower() in IMAGENES:
        mime = mimetypes.guess_type(destino.name)[0]
        b64 = base64.b64encode(destino.read_bytes()).decode()
        return {"name": destino.name, "type": "image", "base64": f"data:{mime};base64,{b64}"}
    contenido = destino.read_text(encoding="utf-8", errors="replace")
    tipo = "markdown" if destino.suffix.lower() == ".md" else "text"
    return {"name": destino.name, "type": tipo, "content": contenido}


# Disco en un hilo (LAS CUATRO, async): un archivo grande no congela el loop.
@router.get("/repo")
async def list_repo(user: AuthUser = Depends(require_superadmin)):
    return await asyncio.to_thread(_listar)


@router.get("/repo/file")
async def get_file(path: str = Query(...), user: AuthUser = Depends(require_superadmin)):
    destino = await asyncio.to_thread(_resolve, path)
    return await asyncio.to_thread(_leer, destino)


@router.delete("/repo/file")
async def delete_file(path: str = Query(...), user: AuthUser = Depends(require_superadmin)):
    destino = await asyncio.to_thread(_resolve, path)
    await asyncio.to_thread(destino.unlink)
    return {"ok": True}
