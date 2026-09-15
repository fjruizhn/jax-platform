from pydantic import BaseModel, Field
from typing import Optional


class TokenPayload(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    exp: int


class LoginRequest(BaseModel):
    # 254 = máximo de RFC 5321 (2026-09-12). Sin tope, cada email distinto --
    # nginx acepta cuerpos de 50 MB -- quedaba como clave del limitador de
    # login hasta que el LRU la expulsaba: memoria que decide el atacante.
    email: str = Field(max_length=254)
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    tenant_id: int
    role: str
    email: str
    must_change_password: bool = False


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    user_id: int
    tenant_id: int
    role: str
    email: str
    must_change_password: bool = False


class AuthUser(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    email: Optional[str] = None
    token_version: int = 0
    must_change_password: bool = False
