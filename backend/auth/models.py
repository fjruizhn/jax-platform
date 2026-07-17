from pydantic import BaseModel
from typing import Optional


class TokenPayload(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    exp: int


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    tenant_id: int
    role: str
    email: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    user_id: int
    tenant_id: int
    role: str
    email: str


class AuthUser(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    email: Optional[str] = None
