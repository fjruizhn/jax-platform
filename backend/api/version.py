"""GET /api/version, SIN autenticación: el Login también muestra la versión.

Devuelve la versión con que arrancó el proceso (app.version, que main.py lee
del archivo VERSION una sola vez al arrancar): no toca el disco por request.
Cambiar VERSION solo requiere reiniciar jax-platform; el frontend la pide en
tiempo de ejecución y no se recompila.
"""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

router = APIRouter()


class Version(BaseModel):
    version: str


@router.get("/api/version", response_model=Version)
async def version(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-cache"
    return {"version": request.app.version}
