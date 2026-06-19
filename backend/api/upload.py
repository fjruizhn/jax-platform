import os
import base64
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from auth.middleware import get_current_user
from auth.models import AuthUser

router = APIRouter(prefix="/api/chat")

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"}
ALLOWED_TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json"}
ALLOWED_CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".toml", ".yml", ".yaml", ".sh"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def _detect_type(filename: str, content_type: str) -> str:
    if content_type in ALLOWED_IMAGE_TYPES or content_type.startswith("image/"):
        return "image"
    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        return "pdf"
    ext = os.path.splitext(filename.lower())[1]
    if ext in ALLOWED_CODE_EXT:
        return "code"
    return "text"


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
):
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Archivo demasiado grande (máx 10MB)")

    file_type = _detect_type(file.filename or "", file.content_type or "")
    file_id = str(uuid.uuid4())[:8]

    if file_type == "image":
        b64 = base64.b64encode(content).decode()
        mime = file.content_type or "image/jpeg"
        return {
            "file_id": file_id,
            "type": "image",
            "filename": file.filename,
            "mime_type": mime,
            "base64": f"data:{mime};base64,{b64}",
            "content_preview": f"[Imagen: {file.filename}]",
            "ready": True,
        }

    if file_type == "pdf":
        try:
            import pdfplumber
            import io
            text = ""
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages[:20]:  # max 20 páginas
                    text += page.extract_text() or ""
            return {
                "file_id": file_id,
                "type": "pdf",
                "filename": file.filename,
                "mime_type": "application/pdf",
                "content": text[:8000],  # max 8k chars
                "content_preview": text[:200],
                "ready": True,
            }
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"No se pudo leer el PDF: {e}")

    # text or code
    try:
        text = content.decode("utf-8")
    except Exception:
        text = content.decode("latin-1", errors="replace")

    return {
        "file_id": file_id,
        "type": file_type,
        "filename": file.filename,
        "mime_type": file.content_type or "text/plain",
        "content": text[:8000],
        "content_preview": text[:200],
        "ready": True,
    }
