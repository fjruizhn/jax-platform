"""GET /api/version, CON autenticación (get_current_user, como las demás rutas
de usuario): solo la muestran pantallas posteriores al login (inicio y
Administración).

Devuelve la versión con que arrancó el proceso (app.version, que main.py lee
del archivo VERSION una sola vez al arrancar): no toca el disco por request.
Cambiar VERSION solo requiere reiniciar jax-platform; el frontend la pide en
tiempo de ejecución y no se recompila.
"""
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from auth.middleware import get_current_user
from auth.models import AuthUser

router = APIRouter()


class Version(BaseModel):
    version: str


@router.get("/api/version", response_model=Version)
async def version(request: Request, response: Response, user: AuthUser = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-cache"
    return {"version": request.app.version}
