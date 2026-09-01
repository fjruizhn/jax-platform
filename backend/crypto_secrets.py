import os
from abc import ABC, abstractmethod
from cryptography.fernet import Fernet, InvalidToken


class KeyProvider(ABC):
    """Interfaz para obtener la llave maestra de cifrado. El dia que se
    mueva a un KMS/Vault, se cambia la implementacion instanciada en
    _key_provider — ningun otro modulo vuelve a leer FERNET_KEY directo.
    Ver jax-platform/docs/fase1-credenciales-diseno.md (B1.3)."""

    @abstractmethod
    def get_master_key(self) -> bytes: ...


class EnvKeyProvider(KeyProvider):
    """Unica implementacion de esta fase. NO resuelve R2 (FERNET_KEY
    co-ubicada con lo que cifra en /etc/jax/.env) — deuda abierta,
    declarada explicitamente en el diseño de Fase 1."""

    def get_master_key(self) -> bytes:
        return os.environ.get("FERNET_KEY", "").encode()


_key_provider: KeyProvider = EnvKeyProvider()

PROVIDER_ENV_KEYS = [
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "KIMI_API_KEY",
    "ZAI_API_KEY",
    "ZHIPU_API_KEY",
]


def _get_fernet() -> Fernet | None:
    key = _key_provider.get_master_key()
    if not key:
        return None
    return Fernet(key)


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


def decrypt_provider_keys_in_env() -> None:
    """Descifra en memoria (os.environ) las API keys de proveedor que
    systemd cargó desde /etc/jax/.env vía EnvironmentFile. Debe llamarse
    antes de que cualquier módulo lea os.getenv()/os.environ para estas
    variables — en cada proceso, lo más temprano posible en su arranque.
    No modifica el archivo en disco."""
    for env_key in PROVIDER_ENV_KEYS:
        raw = os.environ.get(env_key, "")
        if raw:
            os.environ[env_key] = decrypt_secret(raw)
