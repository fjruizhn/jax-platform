import base64
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx
from auth.middleware import get_current_user
from auth.models import AuthUser
from jax_engine.schemas import JAXEvent
from jax_engine.events import event_bus
from http_client import get_http_client

router = APIRouter(prefix="/api")


def _load_jax_env():
    try:
        with open("/etc/jax/.env") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


_load_jax_env()


class ImageRequest(BaseModel):
    prompt: str


class ImageResponse(BaseModel):
    url: str
    revised_prompt: str


@router.post("/image/generate", response_model=ImageResponse)
async def generate_image(req: ImageRequest, user: AuthUser = Depends(get_current_user)):
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY no configurado")

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
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Image API error {e.response.status_code}: {e.response.text[:200]}",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error generando imagen: {str(e)[:200]}")

    data = r.json()
    item = data["data"][0]

    # gpt-image-1 devuelve b64_json; convertir a data URI
    b64 = item.get("b64_json", "")
    if b64:
        url = f"data:image/png;base64,{b64}"
    else:
        url = item.get("url", "")

    revised_prompt = req.prompt  # gpt-image-1 no incluye revised_prompt

    tenant_id = user.tenant_id
    user_id = user.user_id
    event = JAXEvent(
        event_type="image_generated",
        tenant_id=tenant_id,
        user_id=user_id,
        payload={"prompt": req.prompt},
    )
    await event_bus.publish(event)

    return ImageResponse(url=url, revised_prompt=revised_prompt)
