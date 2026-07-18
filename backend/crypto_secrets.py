import os
from cryptography.fernet import Fernet, InvalidToken

PROVIDER_ENV_KEYS = [
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "KIMI_API_KEY",
    "ZAI_API_KEY",
]


def _get_fernet() -> Fernet | None:
    key = os.getenv("FERNET_KEY", "")
    if not key:
        return None
    return Fernet(key.encode())


def encrypt_secret(value: str) -> str:
    fernet = _get_fernet()
    if not fernet:
        raise RuntimeError("FERNET_KEY no configurada en /etc/jax/.env")
    return fernet.encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    """Descifra un valor Fernet. Si no es un token válido (key legacy en
    texto plano, aún no migrada) o falta FERNET_KEY, devuelve el valor tal
    cual — permite convivir con .env parcialmente migrados."""
    fernet = _get_fernet()
    if not fernet or not value:
        return value
    try:
        return fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        return value


def decrypt_db_secret(value: str) -> str:
    """Descifra un valor que SIEMPRE debería ser Fernet válido (viene de
    user_api_keys, escrito únicamente por encrypt_secret). A diferencia de
    decrypt_secret, no hay caso legacy en texto plano que tolerar — un
    fallo real (ej. rotación de FERNET_KEY) debe tratarse como "sin key",
    no devolver el ciphertext crudo como si fuera la key."""
    fernet = _get_fernet()
    if not fernet or not value:
        return ""
    try:
        return fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        return ""


def decrypt_provider_keys_in_env():
    """Descifra en memoria (os.environ) las API keys de proveedor que
    systemd cargó desde /etc/jax/.env vía EnvironmentFile. Debe llamarse
    antes de que cualquier módulo lea os.getenv() para estas variables —
    por eso se invoca al principio de main.py, antes de importar los
    routers. No modifica el archivo en disco."""
    for env_key in PROVIDER_ENV_KEYS:
        raw = os.environ.get(env_key, "")
        if raw:
            os.environ[env_key] = decrypt_secret(raw)
