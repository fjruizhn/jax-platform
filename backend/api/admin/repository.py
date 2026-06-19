import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from auth.middleware import require_superadmin
from auth.models import AuthUser

router = APIRouter(prefix="/api/admin")

REPO_BASE = os.path.expanduser("~/jax/repo")
ALLOWED_FOLDERS = {"missions", "pipelines", "documents", "images"}


def _safe_path(folder: str, filename: str = "") -> str:
    base = os.path.realpath(REPO_BASE)
    target = os.path.realpath(os.path.join(base, folder, filename))
    if not target.startswith(base):
        raise HTTPException(status_code=400, detail="Ruta inválida")
    return target


def _file_info(path: str, base: str) -> dict:
    stat = os.stat(path)
    rel = os.path.relpath(path, base)
    return {
        "name": os.path.basename(path),
        "path": rel,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


@router.get("/repo")
async def list_repo(user: AuthUser = Depends(require_superadmin)):
    result = {}
    for folder in ALLOWED_FOLDERS:
        folder_path = os.path.join(REPO_BASE, folder)
        os.makedirs(folder_path, exist_ok=True)
        files = []
        for fname in sorted(os.listdir(folder_path)):
            fpath = os.path.join(folder_path, fname)
            if os.path.isfile(fpath):
                files.append(_file_info(fpath, REPO_BASE))
        result[folder] = files
    return {"folders": result}


@router.get("/repo/file")
async def get_file(path: str = Query(...), user: AuthUser = Depends(require_superadmin)):
    parts = path.replace("\\", "/").split("/", 1)
    if len(parts) < 2 or parts[0] not in ALLOWED_FOLDERS:
        raise HTTPException(status_code=400, detail="Ruta inválida")

    full_path = _safe_path(parts[0], parts[1])
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    ext = os.path.splitext(full_path)[1].lower()
    img_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

    if ext in img_exts:
        import base64
        with open(full_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        return {"name": os.path.basename(full_path), "type": "image", "base64": f"data:{mime};base64,{b64}"}

    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    ftype = "markdown" if ext == ".md" else "text"
    return {"name": os.path.basename(full_path), "type": ftype, "content": content}


@router.delete("/repo/file")
async def delete_file(path: str = Query(...), user: AuthUser = Depends(require_superadmin)):
    parts = path.replace("\\", "/").split("/", 1)
    if len(parts) < 2 or parts[0] not in ALLOWED_FOLDERS:
        raise HTTPException(status_code=400, detail="Ruta inválida")

    full_path = _safe_path(parts[0], parts[1])
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    os.remove(full_path)
    return {"ok": True}


class SaveFileRequest(BaseModel):
    name: str
    content: str
    folder: str = "documents"


@router.post("/repo/save")
async def save_file(req: SaveFileRequest, user: AuthUser = Depends(require_superadmin)):
    if req.folder not in ALLOWED_FOLDERS:
        raise HTTPException(status_code=400, detail="Carpeta inválida")

    safe_name = os.path.basename(req.name)
    folder_path = os.path.join(REPO_BASE, req.folder)
    os.makedirs(folder_path, exist_ok=True)
    full_path = os.path.join(folder_path, safe_name)

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(req.content)

    rel = os.path.join(req.folder, safe_name)
    return {"ok": True, "path": rel}
