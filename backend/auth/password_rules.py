"""Regla única de contraseña (2026-09-12, admin usuarios etapa 4, spec §3.4).

Mínimo 8 caracteres -- contados como puntos de código, que es lo que cuenta
len() de Python y lo que imita frontend/src/lib/reglasPassword.js --, y máximo
72 BYTES: bcrypt solo usa los primeros 72, y bcrypt 5 (el instalado) LANZA
con más (db/seed.py::BCRYPT_MAX_BYTES).
"""
from db.seed import BCRYPT_MAX_BYTES

MIN_CARACTERES = 8


def problema_de_password(password: str) -> str | None:
    if len(password) < MIN_CARACTERES:
        return "corta"
    if len(password.encode()) > BCRYPT_MAX_BYTES:
        return "larga"
    return None
