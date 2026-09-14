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


# `tv` = jax_users.token_version al emitir (2026-09-12, admin usuarios etapa
# 2). El default 0 es el mismo valor con que se leen los tokens emitidos antes
# de que existiera `tv` (spec §3.2), y lo usan los tests que firman para
# user_id=1, cuya versión nunca cambia.
def create_access_token(user_id: str, tenant_id: str, role: str, token_version: int = 0) -> str:
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "tv": int(token_version),
        "exp": int(time.time()) + ACCESS_EXPIRE_SECONDS,
        "type": "access",
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def create_refresh_token(user_id: str, tenant_id: str, role: str, token_version: int = 0) -> str:
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "tv": int(token_version),
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
