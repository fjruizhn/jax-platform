import os
import time
from jose import jwt, JWTError
from fastapi import HTTPException, status

SECRET = os.getenv("JAX_JWT_SECRET", "")
if not SECRET:
    raise RuntimeError("JAX_JWT_SECRET no configurada en /etc/jax/.env")
ALGORITHM = "HS256"
ACCESS_EXPIRE_SECONDS = 15 * 60
REFRESH_EXPIRE_SECONDS = 7 * 24 * 3600


def create_access_token(user_id: str, tenant_id: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "exp": int(time.time()) + ACCESS_EXPIRE_SECONDS,
        "type": "access",
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def create_refresh_token(user_id: str, tenant_id: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "exp": int(time.time()) + REFRESH_EXPIRE_SECONDS,
        "type": "refresh",
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
        )
