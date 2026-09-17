import base64
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx
from auth.middleware import get_current_user
from auth.models import AuthUser
from credential_resolver import resolve_credential_instrumented, CredentialUnavailableError
from http_client import get_http_client
from api.admin.usage import record_usage, validar_ids_de_uso
from redaccion import recortar_redactado

router = APIRouter(prefix="/api")


class ImageRequest(BaseModel):
    prompt: str


class ImageResponse(BaseModel):
    url: str
    revised_prompt: str


@router.post("/image/generate", response_model=ImageResponse)
async def generate_image(req: ImageRequest, user: AuthUser = Depends(get_current_user)):
    # Task 7: antes de la credencial y del proveedor (0,04 USD por imagen).
    validar_ids_de_uso(user.user_id, user.tenant_id)
    try:
        api_key = await resolve_credential_instrumented("openai")
    except CredentialUnavailableError:
        raise HTTPException(status_code=503, detail={"code": "credencial_no_disponible", "provider": "openai"})

    client = await get_http_client()
    try:
        r = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-image-1",
                "prompt": req.prompt,
                "size": "1024x1024",
                "quality": "medium",
                "n": 1,
            },
            timeout=120.0,
        )
        r.raise_for_status()
    # Fix wave final (2026-09-15): el texto del proveedor sale al usuario.
    # Se REDACTA y DESPUES se recorta (recortar_redactado, el mismo criterio
    # que chat.py::_detalle_502_http), con la credencial de este pedido como
    # secreto conocido: recortando primero, un secreto que cruzaba el
    # caracter 200 quedaba partido, sin forma reconocible, y salia en claro.
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail={
            "code": "imagen_error_http", "status": e.response.status_code,
            "motivo": recortar_redactado(e.response.text, 200, (api_key,))})
    except Exception as e:
        raise HTTPException(status_code=502, detail={
            "code": "imagen_error", "motivo": recortar_redactado(str(e), 200, (api_key,))})

    data = r.json()
    item = data["data"][0]

    # gpt-image-1 devuelve b64_json; convertir a data URI
    b64 = item.get("b64_json", "")
    if b64:
        url = f"data:image/png;base64,{b64}"
    else:
        url = item.get("url", "")

    await record_usage(
        user.user_id, user.tenant_id, "thot_image", "openai", "gpt-image-1",
        0, 0, "imagen", cost_usd_override=0.04,
    )
    # gpt-image-1 no incluye revised_prompt
    return ImageResponse(url=url, revised_prompt=req.prompt)
