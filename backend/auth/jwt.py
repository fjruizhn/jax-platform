import os
import time
from jose import jwt, JWTError
from fastapi import HTTPException, status

from auth.constantes import ACCESS_EXPIRE_SECONDS

SECRET = os.getenv("JAX_JWT_SECRET", "")
if not SECRET:
    raise RuntimeError("JAX_JWT_SECRET no configurada en /etc/jax/.env")
ALGORITHM = "HS256"


# `con_iat` (frente C, 2026-09-16): el refresh lleva `iat` para que
# api/auth.py::refresh mida su vida contra el ajuste vigente; el access no lo
# necesita y no cambia.
def _crear_token(tipo: str, segundos: int, user_id: str, tenant_id: str, role: str, token_version: int,
                 *, con_iat: bool) -> str:
    ahora = int(time.time())
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "tv": int(token_version),
        "exp": ahora + int(segundos),
        "type": tipo,
    }
    if con_iat:
        payload["iat"] = ahora
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


# `tv` = jax_users.token_version al emitir (2026-09-12, admin usuarios etapa
# 2). El default 0 es el mismo valor con que se leen los tokens emitidos antes
# de que existiera `tv` (spec §3.2), y lo usan los tests que firman para
# user_id=1, cuya versión nunca cambia.
def create_access_token(user_id: str, tenant_id: str, role: str, token_version: int = 0) -> str:
    return _crear_token("access", ACCESS_EXPIRE_SECONDS, user_id, tenant_id, role, token_version, con_iat=False)


# Frente C (2026-09-16): la vida del refresh la decide el ajuste
# session_timeout_min (ajustes.py), no una constante. Se exige por nombre, sin
# default: un refresh emitido sin decir cuánto vive sería un default
# silencioso. `iat` permite medir la vida al renovar (api/auth.py::refresh),
# así que acortar el ajuste alcanza a las sesiones abiertas.
def create_refresh_token(user_id: str, tenant_id: str, role: str, token_version: int = 0, *,
                         vida_segundos: int) -> str:
    return _crear_token("refresh", vida_segundos, user_id, tenant_id, role, token_version, con_iat=True)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
        )
