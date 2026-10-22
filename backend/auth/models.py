from pydantic import BaseModel, Field
from typing import Literal, Optional


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
    account_class: str = "INTERNAL"


class MFALoginChallengeResponse(BaseModel):
    """A password-success response which intentionally carries no session."""
    mfa_required: Literal[True] = True
    challenge_token: str
    expires_at: str
    token_type: Literal["mfa_challenge"] = "mfa_challenge"


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    user_id: int
    tenant_id: int
    role: str
    email: str
    must_change_password: bool = False
    account_class: str = "INTERNAL"


class AuthUser(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    email: Optional[str] = None
    token_version: int = 0
    must_change_password: bool = False
    # Only verificar_sesion may mint a known class from jax_users. A direct
    # construction is unclassified and cannot pass the classic stream guard.
    account_class: Literal["INTERNAL", "OFFICE_INTERNAL", "EXTERNAL_CLIENT"] | None = None
    # These claims are advisory transport references only.  Office authority
    # reloads the event and versions from the database before allowing work.
    mfa_proof_event_id: int | None = None
    mfa_version: int = 0
    mfa_at: int | None = None
